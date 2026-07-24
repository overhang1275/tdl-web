from __future__ import annotations

import shutil
import subprocess

from rq import Queue
from rq.job import Job
from redis.exceptions import RedisError
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import DownloadJob, JobStage, JobStatus, MediaType
from app.schemas import JobCreate
from app.services.files import job_download_root
from app.services.logs import append_deleted_job_log, append_job_log
from app.services.paths import chat_path_key, safe_child


class QueueUnavailableError(RuntimeError):
    pass


class DeleteJobError(RuntimeError):
    pass


ACTIVE_STATUSES = (JobStatus.pending, JobStatus.running)


def get_queue() -> Queue:
    return Queue("telegram-downloads", connection=Redis.from_url(settings.redis_url))


def ensure_queue_available() -> Queue:
    queue = get_queue()
    try:
        queue.connection.ping()
    except RedisError as exc:
        raise QueueUnavailableError(f"Redis is not available at {settings.redis_url}") from exc
    return queue


def create_job(db: Session, payload: JobCreate, enqueue: bool = True) -> DownloadJob:
    queue = ensure_queue_available() if enqueue else None
    job = DownloadJob(
        chat_id=payload.chat_id,
        chat_title=payload.chat_title,
        hashtag=payload.hashtag,
        media_type=MediaType(payload.media_type),
        search_text=payload.search_text,
        date_from=payload.date_from,
        date_to=payload.date_to,
        skip_same=payload.skip_same,
        refresh_export=payload.refresh_export,
        export_only=payload.export_only,
        output_subfolder=payload.output_subfolder,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    chat_key = chat_path_key(job.chat_id)
    export_dir = safe_child(settings.exports_dir, chat_key)
    download_dir = safe_child(settings.downloads_dir, chat_key, job.output_subfolder)
    job.export_json_path = str(export_dir / "export.json")
    job.filtered_json_path = str(export_dir / f"filtered-job-{job.id}.json")
    job.download_path = str(download_dir)
    db.commit()
    db.refresh(job)
    if enqueue:
        try:
            rq_job = queue.enqueue("app.worker.run_download_job", job.id, job_timeout=settings.command_timeout_seconds + 600)
        except RedisError as exc:
            job.stage = JobStage.failed
            job.status = JobStatus.failed
            job.error_message = f"Could not enqueue job in Redis: {exc}"
            db.commit()
            raise QueueUnavailableError(job.error_message) from exc
        job.rq_job_id = rq_job.id
        db.commit()
        db.refresh(job)
    return job


def find_duplicate_active_job(db: Session, payload: JobCreate) -> DownloadJob | None:
    return db.scalar(
        select(DownloadJob)
        .where(
            DownloadJob.status.in_(ACTIVE_STATUSES),
            DownloadJob.chat_id == payload.chat_id,
            DownloadJob.hashtag == payload.hashtag,
            DownloadJob.media_type == MediaType(payload.media_type),
            DownloadJob.search_text == payload.search_text,
            DownloadJob.date_from == payload.date_from,
            DownloadJob.date_to == payload.date_to,
            DownloadJob.output_subfolder == payload.output_subfolder,
            DownloadJob.export_only == payload.export_only,
        )
        .order_by(DownloadJob.created_at.desc())
    )


def cancel_job(db: Session, job: DownloadJob) -> DownloadJob:
    if job.status not in ACTIVE_STATUSES:
        return job
    job.cancel_requested = True
    if job.status == JobStatus.pending:
        job.status = JobStatus.cancelled
        job.stage = JobStage.cancelled
    db.commit()
    if job.rq_job_id:
        try:
            rq_job = Job.fetch(job.rq_job_id, connection=get_queue().connection)
            rq_job.cancel()
        except Exception:
            pass
    db.refresh(job)
    return job


def retry_job(db: Session, job: DownloadJob) -> DownloadJob:
    payload = JobCreate(
        chat_id=job.chat_id,
        chat_title=job.chat_title,
        hashtag=job.hashtag,
        media_type=job.media_type.value,
        search_text=job.search_text,
        date_from=job.date_from,
        date_to=job.date_to,
        skip_same=job.skip_same,
        refresh_export=job.refresh_export,
        export_only=job.export_only,
        output_subfolder=job.output_subfolder,
    )
    return create_job(db, payload)


def wipe_delete_job(db: Session, job: DownloadJob) -> None:
    if not shutil.which("wipe"):
        raise DeleteJobError("wipe no está instalado.")

    try:
        path = job_download_root(job.id, job.download_path).resolve()
    except (OSError, ValueError) as exc:
        raise DeleteJobError("La ruta del job no pertenece al directorio de descargas.") from exc

    downloads_dir = settings.downloads_dir.resolve()
    if path == downloads_dir or downloads_dir not in path.parents:
        raise DeleteJobError("Ruta insegura: no pertenece al directorio de jobs.")
    if not path.exists():
        raise DeleteJobError(f"La carpeta del job no existe: {path}")
    if not path.is_dir():
        raise DeleteJobError(f"La ruta del job no es una carpeta: {path}")

    command = ["wipe", "-rfiq", str(path)]
    append_job_log(job.id, f"Ejecutando: wipe -rfiq {path}")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output: list[str] = []
    if process.stdout:
        for line in process.stdout:
            line = line.rstrip()
            if line:
                output.append(line)
                append_job_log(job.id, line)
    returncode = process.wait()
    if returncode != 0:
        detail = "\n".join(output[-20:]) or f"wipe falló con código {returncode}"
        append_deleted_job_log(job.id, f"falló eliminación segura de {path}: {detail}")
        raise DeleteJobError(detail)

    append_job_log(job.id, f"Job eliminado con wipe: {path}")
    append_deleted_job_log(job.id, f"eliminado con wipe: {path}")
    db.delete(job)
    db.commit()


def wipe_delete_job_by_id(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(DownloadJob, job_id)
        if not job:
            return
        try:
            wipe_delete_job(db, job)
        except DeleteJobError as exc:
            job.error_message = f"No se eliminó el job: {exc}"
            append_job_log(job.id, job.error_message)
            db.commit()
    finally:
        db.close()


def queue_position(job: DownloadJob) -> int | None:
    if job.status != JobStatus.pending or not job.rq_job_id:
        return None
    try:
        job_ids = get_queue().job_ids
    except Exception:
        return None
    try:
        return list(job_ids).index(job.rq_job_id) + 1
    except ValueError:
        return None


def list_jobs(db: Session) -> list[DownloadJob]:
    sync_pending_jobs_with_queue(db)
    return list(db.scalars(select(DownloadJob).order_by(DownloadJob.created_at.desc())).all())


def list_jobs_for_chat(db: Session, chat_id: str) -> list[DownloadJob]:
    sync_pending_jobs_with_queue(db)
    return list(
        db.scalars(
            select(DownloadJob)
            .where(DownloadJob.chat_id == chat_id)
            .order_by(DownloadJob.created_at.desc())
        ).all()
    )


def sync_pending_jobs_with_queue(db: Session) -> None:
    pending_jobs = list(
        db.scalars(
            select(DownloadJob).where(
                DownloadJob.status == JobStatus.pending,
                DownloadJob.rq_job_id.is_not(None),
            )
        ).all()
    )
    if not pending_jobs:
        return
    try:
        redis = Redis.from_url(settings.redis_url)
        redis.ping()
    except RedisError:
        return
    changed = False
    for job in pending_jobs:
        try:
            rq_job = Job.fetch(job.rq_job_id, connection=redis)
        except Exception:
            continue
        rq_status = str(rq_job.get_status()).lower()
        if "failed" in rq_status:
            job.stage = JobStage.failed
            job.status = JobStatus.failed
            job.error_message = (rq_job.exc_info or "Worker failed before updating this job.")[:4000]
            changed = True
        elif "finished" in rq_status:
            job.stage = JobStage.failed
            job.status = JobStatus.failed
            job.error_message = "Worker finished without updating this job state. Recreate the job."
            changed = True
    if changed:
        db.commit()
