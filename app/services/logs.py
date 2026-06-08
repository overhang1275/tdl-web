from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.paths import safe_child


def job_log_path(job_id: int) -> Path:
    return safe_child(settings.logs_dir, f"job-{job_id}.log")


def append_job_log(job_id: int, message: str) -> None:
    settings.ensure_directories()
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    path = job_log_path(job_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}Z] {message.rstrip()}\n")


def read_job_log(job_id: int, tail_bytes: int = 200_000) -> str:
    path = job_log_path(job_id)
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > tail_bytes:
            handle.seek(size - tail_bytes)
        return handle.read().decode("utf-8", errors="replace")
