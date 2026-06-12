from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.database import SessionLocal, engine, init_db
from app.models import DownloadJob, JobStage, JobStatus
from app.services.download import DownloadService
from app.services.export import ExportService
from app.services.files import count_downloaded_files
from app.services.filtering import filter_export
from app.services.logs import append_job_log


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
        job.status = JobStatus.running
        job.stage = JobStage.exporting
        job.started_at = datetime.utcnow()
        db.commit()
        export_path = Path(job.export_json_path)
        if export_path.exists() and not job.refresh_export:
            append_job_log(job.id, f"Reusing existing export: {export_path}")
        else:
            append_job_log(job.id, f"Exporting chat {job.chat_id}")
            export_service.export_chat(job.chat_id, export_path)

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

        job.stage = JobStage.downloading
        db.commit()
        append_job_log(job.id, "Starting media download")
        download_service.download_from_file(Path(job.filtered_json_path), Path(job.download_path), skip_same=job.skip_same)

        job.total_downloaded_files = count_downloaded_files(Path(job.download_path))
        job.stage = JobStage.completed
        job.status = JobStatus.completed
        job.finished_at = datetime.utcnow()
        db.commit()
        append_job_log(job.id, f"Completed. Files on disk: {job.total_downloaded_files}")
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
