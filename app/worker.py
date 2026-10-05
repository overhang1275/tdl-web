from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import DownloadFile, DownloadJob, JobStage, JobStatus
from app.services.filtering import filter_export, find_messages_container
from app.services.jobs import enqueue_job
from app.services.logs import append_job_log
from app.services.paths import safe_child
from app.services.tdl import TdlCancelled, TdlError, TdlService
from app.services.transfers import (
    MAX_ATTEMPTS, canonical_export, initialize_transfers, reconcile_files, update_transfer_totals, write_json_atomic,
)


class JobCancelled(RuntimeError):
    pass


def export_step(db, job: DownloadJob, tdl: TdlService, should_cancel) -> bool:
    """Export one bounded ID range; publish the cache only after all ranges."""
    export_path = Path(job.export_json_path)
    snapshot = export_path.parent / f"export-job-{job.id}.json"
    if snapshot.exists():
        return True
    if export_path.exists() and not job.refresh_export and job.export_cursor is None:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        write_json_atomic(snapshot, payload)
        append_job_log(job.id, f"Reusing existing export: {export_path}")
        return True
    fragments = export_path.parent / f"export-job-{job.id}"
    fragments.mkdir(parents=True, exist_ok=True)
    if job.export_upper_id is None:
        latest = fragments / "latest.json"
        tdl.export_chat(job.chat_id, latest, should_cancel=should_cancel, last=1)
        payload = json.loads(latest.read_text(encoding="utf-8"))
        messages, _ = find_messages_container(payload)
        upper = max((int(m['id']) for m in messages), default=0)
        job.export_upper_id, job.export_cursor = upper, upper
        db.commit()
        if upper:
            return False
    upper = job.export_cursor
    if upper > 0:
        lower = max(1, upper - settings.export_batch_size + 1)
        shard = fragments / f"{lower}-{upper}.json"
        if not shard.exists():
            tdl.export_chat(job.chat_id, shard, should_cancel=should_cancel, id_range=(lower, upper))
        # Validate before advancing the persisted cursor.
        json.loads(shard.read_text(encoding="utf-8"))
        job.export_cursor = lower - 1
        job.operation_attempts = 0
        db.commit()
        append_job_log(job.id, f"Exportados IDs {lower}–{upper}; siguiente límite: {job.export_cursor}")
        if job.export_cursor > 0:
            return False
    payload = json.loads((fragments / "latest.json").read_text(encoding="utf-8"))
    messages = []
    for shard in fragments.glob("*-*.json"):
        part = json.loads(shard.read_text(encoding="utf-8"))
        rows, _ = find_messages_container(part)
        messages.extend(rows)
    # ID ranges are disjoint and their upper bound stays fixed throughout a run.
    payload['messages'] = sorted(messages, key=lambda m: int(m['id']), reverse=True)
    write_json_atomic(snapshot, payload)
    write_json_atomic(export_path, payload)
    append_job_log(job.id, f"Export ready: {snapshot}")
    return True


def mark_terminal(db, job: DownloadJob, status: JobStatus, error: str | None = None) -> bool:
    owner_token = job.rq_job_id
    conditions = [DownloadJob.id == job.id, DownloadJob.rq_job_id == job.rq_job_id,
                  DownloadJob.status == JobStatus.running]
    if status != JobStatus.cancelled:
        conditions.append(DownloadJob.cancel_requested.is_(False))
    result = db.execute(update(DownloadJob).where(*conditions).values(
        status=status, stage=JobStage(status.value), error_message=error,
        finished_at=datetime.utcnow(), download_speed_bps=0,
        download_eta_seconds=0 if status == JobStatus.completed else None,
    ), execution_options={"synchronize_session": False})
    db.commit()
    db.refresh(job)
    if (not result.rowcount and status != JobStatus.cancelled and job.cancel_requested
            and job.status == JobStatus.running and job.rq_job_id == owner_token):
        mark_terminal(db, job, JobStatus.cancelled)
    return bool(result.rowcount)


def finish_job(db, job: DownloadJob) -> None:
    update_transfer_totals(db, job)
    failed = bool(job.total_failed_files)
    error = f"{job.total_failed_files} archivos fallaron tras {MAX_ATTEMPTS} intentos. Reintentar pendientes conserva los completados." if failed else None
    if mark_terminal(db, job, JobStatus.failed if failed else JobStatus.completed, error):
        append_job_log(job.id, f"{job.status.value}: {job.total_downloaded_files} archivos verificados; {job.total_failed_files} fallidos.")


def download_batch(db, job: DownloadJob, tdl: TdlService, should_cancel) -> None:
    exhausted = list(db.scalars(select(DownloadFile).where(
        DownloadFile.job_id == job.id,
        DownloadFile.status.in_(("pending", "downloading")),
        DownloadFile.attempts >= MAX_ATTEMPTS,
    )))
    if exhausted:
        reconcile_files(db, exhausted)
        for item in exhausted:
            if item.status != "completed":
                item.status = "failed"
                item.last_error = item.last_error or "Worker interrumpido durante el último intento"
        db.commit()
    candidates = list(db.scalars(select(DownloadFile).where(
        DownloadFile.job_id == job.id,
        DownloadFile.status.in_(("pending", "downloading")),
        DownloadFile.attempts < MAX_ATTEMPTS,
    ).order_by(DownloadFile.attempts, DownloadFile.id).limit(settings.download_batch_size)))
    if not candidates:
        finish_job(db, job)
        return
    # After a failed batch, retry messages individually to isolate bad media.
    if candidates[0].attempts:
        candidates = candidates[:1]
    reconcile_files(db, candidates)
    candidates = [item for item in candidates if item.status != "completed"]
    for item in candidates:
        item.attempts += 1
        item.status = "downloading"
    db.commit()
    source = Path(job.filtered_json_path).with_name(f"batch-job-{job.id}.json")
    metadata = Path(job.filtered_json_path).with_suffix(".metadata.json")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload['messages'] = [{**json.loads(item.message_json), "type": "message"} for item in candidates]
    write_json_atomic(source, payload)
    job.stage = JobStage.downloading
    job.error_message = None
    db.commit()
    root = safe_child(Path(job.download_path), f"job-{job.id}")
    last_update = 0.0
    last_bytes = job.download_observed_bytes
    started = time.monotonic()
    observed = 0

    def progress(force=False):
        nonlocal last_update, last_bytes, observed
        now = time.monotonic()
        if not force and now - last_update < 3:
            return observed
        reconcile_files(db, candidates)
        update_transfer_totals(db, job)
        partial = 0
        for item in candidates:
            if item.status == "completed":
                continue
            folder = safe_child(Path(item.destination_root), item.output_dir)
            if folder.is_dir():
                for path in folder.iterdir():
                    if path.is_file():
                        try:
                            partial += path.stat().st_size
                        except OSError:
                            pass  # tdl may rename a temporary between listing and stat.
        observed = job.download_observed_bytes + partial
        elapsed = max(now - last_update, 1) if last_update else max(now - started, 1)
        job.download_speed_bps = max(0, int((observed - last_bytes) / elapsed))
        job.download_observed_bytes = observed
        job.download_eta_seconds = None
        last_update, last_bytes = now, observed
        db.commit()
        return observed

    def cancel_and_track():
        if should_cancel():
            return True
        progress()
        return False

    error = None
    try:
        if candidates:
            append_job_log(job.id, f"Descargando lote de {len(candidates)} archivos")
            tdl.download_from_file(source, root, skip_same=job.skip_same,
                                   should_cancel=cancel_and_track, progress_bytes=progress)
    except TdlError as exc:
        error = str(exc)
        if isinstance(exc, TdlCancelled):
            raise
    finally:
        # Runs on timeout/cancellation too: finalized files are never lost.
        progress(force=True)
    if should_cancel():
        raise JobCancelled("Job cancelled by user.")
    for item in candidates:
        if item.status != "completed":
            item.last_error = error or "tdl terminó sin generar un archivo completo verificable"
            item.status = "failed" if item.attempts >= MAX_ATTEMPTS else "pending"
            append_job_log(job.id, f"Mensaje {item.message_id}: intento {item.attempts}/{MAX_ATTEMPTS}, {item.last_error[:500]}")
    db.commit()
    update_transfer_totals(db, job)
    pending = db.scalar(select(DownloadFile).where(
        DownloadFile.job_id == job.id, DownloadFile.status == "pending",
    ).order_by(DownloadFile.attempts, DownloadFile.id).limit(1))
    if pending:
        delay = (15 * 4 ** (pending.attempts - 1)) if pending.attempts else 0
        enqueue_job(db, job, delay=delay)
    else:
        finish_job(db, job)


def run_download_job(job_id: int, token: str | None = None) -> None:
    engine.dispose()
    init_db()
    db = SessionLocal()
    execution_token = token
    try:
        condition = [DownloadJob.id == job_id, DownloadJob.status == JobStatus.pending]
        if token is not None:
            condition.append(DownloadJob.rq_job_id == token)
        claimed = db.execute(update(DownloadJob).where(*condition).values(status=JobStatus.running))
        db.commit()
        if not claimed.rowcount:
            return
        job = db.get(DownloadJob, job_id)
        execution_token = job.rq_job_id
        job.started_at = job.started_at or datetime.utcnow()
        db.commit()
        tdl = TdlService()

        def cancelled():
            # A separate read avoids expiring uncommitted transfer progress.
            with SessionLocal() as control:
                current = control.get(DownloadJob, job_id)
                return (current is None or current.cancel_requested or current.status == JobStatus.cancelled
                        or current.rq_job_id != execution_token)

        if cancelled():
            raise JobCancelled("Job cancelled by user.")
        if job.transfer_initialized:
            download_batch(db, job, tdl, cancelled)
            return
        job.stage = JobStage.exporting
        db.commit()
        if not export_step(db, job, tdl, cancelled):
            if cancelled():
                raise JobCancelled("Job cancelled by user.")
            enqueue_job(db, job)
            return
        if cancelled():
            raise JobCancelled("Job cancelled by user.")
        if job.export_only:
            if mark_terminal(db, job, JobStatus.completed):
                append_job_log(job.id, "Completed export-only job.")
            return
        job.stage = JobStage.filtering
        db.commit()
        snapshot = Path(job.export_json_path).parent / f"export-job-{job.id}.json"
        filter_export(snapshot, Path(job.filtered_json_path), hashtag=job.hashtag,
                      media_type=job.media_type.value, search_text=job.search_text,
                      date_from=job.date_from, date_to=job.date_to)
        initialize_transfers(db, job)
        if not job.total_filtered_messages or job.total_downloaded_files == job.total_filtered_messages:
            finish_job(db, job)
        else:
            job.stage = JobStage.downloading
            db.commit()
            enqueue_job(db, job)
    except (JobCancelled, TdlCancelled) as exc:
        db.rollback()
        db.expire_all()
        job = db.get(DownloadJob, job_id)
        if job and job.rq_job_id == execution_token:
            if mark_terminal(db, job, JobStatus.cancelled):
                append_job_log(job.id, f"Cancelled: {exc}")
    except Exception as exc:
        db.rollback()
        db.expire_all()
        job = db.get(DownloadJob, job_id)
        if job and job.rq_job_id == execution_token:
            if job.stage == JobStage.exporting and isinstance(exc, TdlError) and not job.cancel_requested:
                job.operation_attempts += 1
                db.commit()
                if job.operation_attempts < MAX_ATTEMPTS:
                    append_job_log(job.id, f"Exportación interrumpida; reintento {job.operation_attempts}/{MAX_ATTEMPTS}: {exc}")
                    try:
                        enqueue_job(db, job, delay=15 * 4 ** (job.operation_attempts - 1))
                        return
                    except Exception as enqueue_error:
                        exc = enqueue_error
            if mark_terminal(db, job, JobStatus.failed, str(exc)):
                append_job_log(job.id, f"Failed: {exc}")
        raise
    finally:
        db.close()
