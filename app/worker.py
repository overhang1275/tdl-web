from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import time

from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import DownloadJob, JobStage, JobStatus
from app.services.errors import friendly_error
from app.services.files import scan_download_progress
from app.services.filtering import exclude_message_ids, filter_export, message_ids_from_export
from app.services.logs import append_job_log
from app.services.paths import chat_path_key, safe_child
from app.services.tdl import TdlCancelled, TdlService


class JobCancelled(RuntimeError):
    pass


def downloaded_ids_path(chat_id: str) -> Path:
    return safe_child(settings.exports_dir, chat_path_key(chat_id), "downloaded-message-ids.json")


def read_downloaded_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {str(item) for item in payload if item is not None} if isinstance(payload, list) else set()


def add_downloaded_ids(path: Path, ids: set[str]) -> int:
    if not ids:
        return 0
    current = read_downloaded_ids(path)
    merged = current | ids
    path.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: no cross-process lock; move this ledger to DB if multiple workers write the same chat concurrently.
    path.write_text(json.dumps(sorted(merged), indent=2), encoding="utf-8")
    return len(merged) - len(current)


def run_download_job(job_id: int) -> None:
    engine.dispose()
    init_db()
    db = SessionLocal()
    tdl = TdlService()
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
            tdl.export_chat(job.chat_id, export_path, should_cancel=ensure_not_cancelled)
        append_job_log(job.id, f"Export ready: {export_path}")

        if job.export_only:
            job.stage = JobStage.completed
            job.status = JobStatus.completed
            job.finished_at = datetime.utcnow()
            db.commit()
            append_job_log(job.id, "Completed export-only job. No filtering or download was requested.")
            return

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
        if total == 0:
            raise ValueError("Filtered messages: 0")

        ledger_path = downloaded_ids_path(job.chat_id)
        remaining, skipped = exclude_message_ids(
            Path(job.filtered_json_path),
            Path(job.filtered_json_path),
            read_downloaded_ids(ledger_path),
        )
        job.total_filtered_messages = remaining
        db.commit()
        if skipped:
            append_job_log(job.id, f"Already downloaded messages skipped: {skipped}")
        if remaining == 0:
            job.stage = JobStage.completed
            job.status = JobStatus.completed
            job.finished_at = datetime.utcnow()
            db.commit()
            append_job_log(job.id, "Completed. No new messages to download.")
            return

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

        tdl.download_from_file(
            Path(job.filtered_json_path),
            Path(job.download_path),
            skip_same=job.skip_same,
            should_cancel=cancel_or_track_download,
        )

        raise_if_cancelled()
        recorded = add_downloaded_ids(downloaded_ids_path(job.chat_id), message_ids_from_export(Path(job.filtered_json_path)))
        if recorded:
            append_job_log(job.id, f"Recorded downloaded messages: {recorded}")
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
            friendly = friendly_error(str(exc))
            append_job_log(job.id, f"Failed: {friendly['title'] if friendly else exc}")
            if friendly:
                append_job_log(job.id, friendly["detail"])
        raise
    finally:
        db.close()
