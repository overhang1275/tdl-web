from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.paths import safe_child


EVENT_TITLES = (
    ("exporting chat", "Export iniciado"),
    ("export ready", "Export listo"),
    ("reusing existing export", "Export listo"),
    ("filtering exported json", "Filtrado iniciado"),
    ("filtered messages", "Filtro terminado"),
    ("starting media download", "Descarga iniciada"),
    ("completed", "Job terminado"),
    ("failed", "Job falló"),
    ("cancelled", "Job cancelado"),
)


def job_log_path(job_id: int) -> Path:
    return safe_child(settings.logs_dir, f"job-{job_id}.log")


def append_job_log(job_id: int, message: str) -> None:
    settings.ensure_directories()
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    path = job_log_path(job_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}Z] {message.rstrip()}\n")


def append_deleted_job_log(job_id: int, message: str) -> None:
    settings.ensure_directories()
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    path = safe_child(settings.logs_dir, "deleted-jobs.log")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}Z] Job #{job_id}: {message.rstrip()}\n")


def read_job_log(job_id: int, tail_bytes: int = 200_000) -> str:
    path = job_log_path(job_id)
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > tail_bytes:
            handle.seek(size - tail_bytes)
        return handle.read().decode("utf-8", errors="replace")


def job_events(job_id: int) -> list[dict[str, str]]:
    events = []
    for line in read_job_log(job_id).splitlines():
        lower = line.lower()
        title = next((title for needle, title in EVENT_TITLES if needle in lower), None)
        if title is None:
            continue
        timestamp = ""
        message = line
        if line.startswith("[") and "]" in line:
            timestamp, message = line[1:].split("]", 1)
            message = message.strip()
        events.append({"title": title, "timestamp": timestamp, "message": message})
    return events[-30:]
