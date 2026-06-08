from __future__ import annotations

from rq import Queue
from redis.exceptions import RedisError
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DownloadJob, MediaType
from app.schemas import JobCreate
from app.services.paths import safe_child


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
        output_subfolder=payload.output_subfolder,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    export_dir = safe_child(settings.exports_dir, str(job.id))
    download_dir = safe_child(settings.downloads_dir, str(job.id), job.output_subfolder)
    job.export_json_path = str(export_dir / "export.json")
    job.filtered_json_path = str(export_dir / "filtered.json")
    job.download_path = str(download_dir)
    db.commit()
    db.refresh(job)
    if enqueue:
        rq_job = queue.enqueue("app.worker.run_download_job", job.id, job_timeout=settings.command_timeout_seconds + 600)
        job.rq_job_id = rq_job.id
        db.commit()
        db.refresh(job)
    return job


def list_jobs(db: Session) -> list[DownloadJob]:
    return list(db.scalars(select(DownloadJob).order_by(DownloadJob.created_at.desc())).all())
