from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.paths import safe_child


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def file_kind(path: Path) -> str:
    media_type, _ = mimetypes.guess_type(path.name)
    if not media_type:
        return "file"
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type == "application/pdf":
        return "pdf"
    if media_type.startswith("text/"):
        return "text"
    return "file"


def list_downloaded_files(root: Path) -> list[dict[str, str | int | bool]]:
    if not root.exists():
        return []
    files: list[dict[str, str | int | bool]] = []
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            size = path.stat().st_size
            modified_at = int(path.stat().st_mtime)
            kind = file_kind(path)
            files.append(
                {
                    "name": path.name,
                    "relative_path": relative,
                    "size": size,
                    "human_size": human_size(size),
                    "modified_at": modified_at,
                    "modified_label": datetime.fromtimestamp(modified_at).strftime("%Y-%m-%d %H:%M"),
                    "kind": kind,
                    "can_preview": kind in {"image", "video", "audio", "pdf", "text"},
                }
            )
    return sorted(files, key=lambda item: str(item["relative_path"]).lower())


def job_download_root(job_id: int, download_path: str | None) -> Path:
    if download_path:
        return safe_child(settings.downloads_dir, *Path(download_path).resolve().relative_to(settings.downloads_dir.resolve()).parts)
    return safe_child(settings.downloads_dir, str(job_id))


def downloaded_file_path(root: Path, relative_path: str) -> Path:
    path = safe_child(root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    return path


def count_downloaded_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total
