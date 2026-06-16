from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.tdl import TdlService


def chats_cache_path() -> Path:
    return settings.base_dir / "chats.json"


def read_chats_cache() -> tuple[list[dict[str, Any]], datetime | None]:
    path = chats_cache_path()
    if not path.exists():
        return [], None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    chats = payload.get("chats", [])
    updated_at = None
    if payload.get("updated_at"):
        try:
            updated_at = datetime.fromisoformat(str(payload["updated_at"]))
        except ValueError:
            updated_at = None
    return chats if isinstance(chats, list) else [], updated_at


def write_chats_cache(chats: list[dict[str, Any]]) -> datetime:
    settings.ensure_directories()
    updated_at = datetime.now(UTC)
    payload = {
        "updated_at": updated_at.isoformat(),
        "total": len(chats),
        "chats": chats,
    }
    with chats_cache_path().open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    return updated_at


def refresh_chats_cache(tdl: TdlService | None = None) -> tuple[list[dict[str, Any]], datetime]:
    chats = (tdl or TdlService()).list_chats()
    updated_at = write_chats_cache(chats)
    return chats, updated_at


def delete_chats_cache() -> None:
    chats_cache_path().unlink(missing_ok=True)
