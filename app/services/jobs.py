from __future__ import annotations

from rq import Queue
from rq.job import Job
from redis.exceptions import RedisError
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DownloadJob, JobStage, JobStatus, MediaType
from app.schemas import JobCreate
from app.services.paths import chat_path_key, safe_child


class QueueUnavailableError(RuntimeError):
    pass


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
