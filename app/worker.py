from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time

from app.database import SessionLocal, engine, init_db
from app.models import DownloadJob, JobStage, JobStatus
from app.services.download import DownloadService
from app.services.export import ExportService
from app.services.files import scan_download_progress
from app.services.filtering import filter_export
from app.services.logs import append_job_log
from app.services.tdl import TdlCancelled


class JobCancelled(RuntimeError):
    pass


def run_download_job(job_id: int) -> None:
    engine.dispose()
    init_db()
    db = SessionLocal()
    export_service = ExportService()
    download_service = DownloadService()
    try:
        job = db.get(DownloadJob, job_id)
        if job is None:
            return
        if job.cancel_requested:
            job.status = JobStatus.cancelled
            job.stage = JobStage.cancelled
            job.finished_at = datetime.utcnow()
            db.commit()
            append_job_log(job.id, "Cancelled before starting.")
            return
        job.status = JobStatus.running
        job.stage = JobStage.exporting
        job.started_at = datetime.utcnow()
        db.commit()

        def ensure_not_cancelled() -> bool:
            db.expire_all()
            current = db.get(DownloadJob, job_id)
            if current is None:
                return True
            return bool(current.cancel_requested or current.status == JobStatus.cancelled)

        def raise_if_cancelled() -> None:
            if ensure_not_cancelled():
                raise JobCancelled("Job cancelled by user.")

        raise_if_cancelled()
        export_path = Path(job.export_json_path)
        if export_path.exists() and not job.refresh_export:
            append_job_log(job.id, f"Reusing existing export: {export_path}")
        else:
            append_job_log(job.id, f"Exporting chat {job.chat_id}")
            export_service.export_chat(job.chat_id, export_path, should_cancel=ensure_not_cancelled)

        raise_if_cancelled()
        job.stage = JobStage.filtering
        db.commit()
        append_job_log(job.id, "Filtering exported JSON")
        total = filter_export(
            Path(job.export_json_path),
            Path(job.filtered_json_path),
            hashtag=job.hashtag,
            media_type=job.media_type.value,
            search_text=job.search_text,
            date_from=job.date_from,
            date_to=job.date_to,
        )
        job.total_filtered_messages = total
        db.commit()
        append_job_log(job.id, f"Filtered messages: {total}")

        raise_if_cancelled()
        job.stage = JobStage.downloading
        job.download_observed_files = 0
        job.download_observed_bytes = 0
        job.download_speed_bps = 0
        job.download_eta_seconds = None
        db.commit()
        append_job_log(job.id, "Starting media download")
        download_started_at = time.monotonic()
        last_progress_at = 0.0

        def track_download_progress(force: bool = False) -> None:
            nonlocal last_progress_at
            now = time.monotonic()
            if not force and now - last_progress_at < 3:
                return
            last_progress_at = now
            files, bytes_total = scan_download_progress(Path(job.download_path))
            elapsed = max(1, int(now - download_started_at))
            speed = int(bytes_total / elapsed)
            eta = None
            if job.total_filtered_messages > 0 and files > 0:
                remaining = max(job.total_filtered_messages - files, 0)
                eta = int((elapsed / files) * remaining)
            current = db.get(DownloadJob, job_id)
            if current is not None:
                current.download_observed_files = files
                current.download_observed_bytes = bytes_total
                current.download_speed_bps = speed
                current.download_eta_seconds = eta
                current.total_downloaded_files = files
                db.commit()

        def cancel_or_track_download() -> bool:
            cancelled = ensure_not_cancelled()
            if not cancelled:
                track_download_progress()
            return cancelled

        download_service.download_from_file(
            Path(job.filtered_json_path),
            Path(job.download_path),
            skip_same=job.skip_same,
            should_cancel=cancel_or_track_download,
        )

        raise_if_cancelled()
        track_download_progress(force=True)
        db.refresh(job)
        job.stage = JobStage.completed
        job.status = JobStatus.completed
        job.download_eta_seconds = 0
        job.finished_at = datetime.utcnow()
        db.commit()
        append_job_log(job.id, f"Completed. Files on disk: {job.total_downloaded_files}")
    except (JobCancelled, TdlCancelled) as exc:
        db.rollback()
        job = db.get(DownloadJob, job_id)
        if job is not None:
            job.stage = JobStage.cancelled
            job.status = JobStatus.cancelled
            job.error_message = None
            job.finished_at = datetime.utcnow()
            db.commit()
            append_job_log(job.id, f"Cancelled: {exc}")
    except Exception as exc:
        db.rollback()
        job = db.get(DownloadJob, job_id)
        if job is not None:
            job.stage = JobStage.failed
            job.status = JobStatus.failed
            job.error_message = str(exc)
            job.finished_at = datetime.utcnow()
            db.commit()
            append_job_log(job.id, f"Failed: {exc}")
        raise
    finally:
        db.close()
