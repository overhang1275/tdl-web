from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.paths import safe_child


def list_downloaded_files(job_id: int) -> list[dict[str, str | int]]:
    root = safe_child(settings.downloads_dir, str(job_id))
    if not root.exists():
        return []
    files: list[dict[str, str | int]] = []
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files.append({"name": path.name, "relative_path": relative, "size": path.stat().st_size})
    return sorted(files, key=lambda item: str(item["relative_path"]).lower())


def count_downloaded_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())

