from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DownloadFile, DownloadJob
from app.services.files import job_download_root
from app.services.filtering import find_messages_container, message_id
from app.services.paths import safe_child


MAX_ATTEMPTS = 3


def canonical_export(payload, chat_id: str) -> dict:
    messages, _ = find_messages_container(payload)
    dialog = payload.get("id", chat_id) if isinstance(payload, dict) else chat_id
    try:
        dialog = int(dialog)
    except (ValueError, TypeError) as exc:
        raise ValueError("Export JSON sin ID numérico del chat; actualiza la exportación") from exc
    return {"id": dialog, "messages": [{**message, "type": message.get("type") or "message"} for message in messages]}


def write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def verified_file(item: DownloadFile) -> bool:
    if not item.local_path:
        return False
    try:
        path = safe_child(Path(item.destination_root), item.local_path)
        return path.is_file() and path.stat().st_size == item.size and (
            item.expected_size is None or item.size == item.expected_size
        )
    except (ValueError, OSError):
        return False


def initialize_transfers(db: Session, job: DownloadJob) -> None:
    payload = canonical_export(json.loads(Path(job.filtered_json_path).read_text(encoding="utf-8")), job.chat_id)
    messages, _ = find_messages_container(payload)
    root = job_download_root(job.id, job.download_path)
    seen = set()
    for message in messages:
        ident = message_id(message)
        name = message.get("file")
        if not name or not ident or ident in seen:
            continue
        if not ident.isdigit():
            raise ValueError("Invalid exported message ID")
        seen.add(ident)
        raw_size = message.get("file_size") or message.get("size")
        expected = int(raw_size) if isinstance(raw_size, (int, float)) and raw_size > 0 else None
        item = DownloadFile(
            job_id=job.id, chat_id=job.chat_id, message_id=ident,
            file_name=str(name), message_json=json.dumps(message, ensure_ascii=False),
            destination_root=str(root), output_dir=str(safe_child(root, f"job-{job.id}", ident)),
            expected_size=expected, size=0, attempts=0, status="pending",
        )
        # Only a verified file at the requested destination can be reused.
        if job.skip_same:
            # Adopt finalized files from the previous default tdl template.
            dialog = payload.get("id") if isinstance(payload, dict) else None
            if dialog is not None:
                legacy = safe_child(root, f"{dialog}_{ident}_{name}")
                if legacy.is_file() and not legacy.name.endswith(".tmp"):
                    size = legacy.stat().st_size
                    if size > 0 and (expected is None or expected == size):
                        item.local_path, item.size, item.status = str(legacy), size, "completed"
            previous = db.scalars(select(DownloadFile).where(
                DownloadFile.chat_id == job.chat_id,
                DownloadFile.message_id == ident,
                DownloadFile.file_name == str(name),
                DownloadFile.destination_root == str(root),
                DownloadFile.status == "completed",
            ).order_by(DownloadFile.id.desc()))
            for old in previous:
                if verified_file(old) and (expected is None or expected == old.size):
                    item.local_path, item.size, item.status = old.local_path, old.size, "completed"
                    break
        db.add(item)
    job.transfer_initialized = True
    job.total_filtered_messages = len(seen)
    write_json_atomic(Path(job.filtered_json_path).with_suffix(".metadata.json"), {"id": payload['id']})
    db.commit()
    update_transfer_totals(db, job)


def reconcile_files(db: Session, items: list[DownloadFile]) -> None:
    for item in items:
        if item.status == "completed":
            if not verified_file(item):
                item.expected_size = item.expected_size or item.size or None
                item.status, item.local_path, item.size = "pending", None, 0
            continue
        # Only folders belonging to an attempted transfer may contain evidence.
        if not item.attempts:
            continue
        folder = safe_child(Path(item.destination_root), item.output_dir)
        if not folder.is_dir():
            continue
        candidates = [p for p in folder.iterdir() if p.is_file() and (
            not p.name.endswith(".tmp") or p.name == item.file_name
        )]
        if len(candidates) != 1:
            continue
        path = safe_child(Path(item.destination_root), str(candidates[0]))
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0 or (item.expected_size is not None and size != item.expected_size):
            continue
        item.status, item.local_path, item.size = "completed", str(path), size
        item.last_error = None
    db.commit()


def update_transfer_totals(db: Session, job: DownloadJob) -> None:
    files, size = db.execute(select(func.count(), func.coalesce(func.sum(DownloadFile.size), 0)).where(
        DownloadFile.job_id == job.id, DownloadFile.status == "completed",
    )).one()
    job.total_downloaded_files = files
    job.download_observed_files = files
    job.download_observed_bytes = size
    job.total_failed_files = db.scalar(select(func.count()).select_from(DownloadFile).where(
        DownloadFile.job_id == job.id, DownloadFile.status == "failed",
    )) or 0
    db.commit()


def reset_pending_files(db: Session, job: DownloadJob) -> None:
    items = list(db.scalars(select(DownloadFile).where(DownloadFile.job_id == job.id)))
    reconcile_files(db, items)
    for item in items:
        if item.status != "completed":
            item.status, item.attempts, item.last_error = "pending", 0, None
    job.operation_attempts = 0
    db.commit()
    update_transfer_totals(db, job)
