from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "value"):
                    if isinstance(item.get(key), str):
                        parts.append(item[key])
        return " ".join(parts)
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values() if isinstance(v, str))
    return ""


def message_text(message: dict[str, Any]) -> str:
    return extract_text(message.get("text") or message.get("message") or message.get("content"))


def parse_message_date(message: dict[str, Any]) -> date | None:
    raw = message.get("date") or message.get("time") or message.get("created_at")
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return datetime.utcfromtimestamp(raw).date()
    if isinstance(raw, str):
        value = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
    return None


def has_media_type(message: dict[str, Any], media_type: str) -> bool:
    if media_type == "all":
        return True
    haystack: list[str] = []
    for key in ("type", "media_type", "mime_type"):
        if message.get(key):
            haystack.append(str(message[key]).lower())
    media = message.get("media")
    if isinstance(media, dict):
        haystack.extend(str(value).lower() for value in media.values() if isinstance(value, str))
    elif isinstance(media, str):
        haystack.append(media.lower())
    joined = " ".join(haystack)
    aliases = {
        "image": ("image", "photo", "picture", "jpg", "jpeg", "png", "webp"),
        "video": ("video", "mp4", "mpeg"),
        "audio": ("audio", "voice", "music", "ogg", "mp3", "wav"),
        "document": ("document", "file", "application", "pdf", "zip"),
    }
    return any(alias in joined for alias in aliases.get(media_type, (media_type,)))


def find_messages_container(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], None
    if isinstance(payload, dict):
        for key in ("messages", "items", "data"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)], key
    return [], None


def message_matches(
    message: dict[str, Any],
    hashtag: str | None,
    media_type: str,
    search_text: str | None,
    date_from: date | None,
    date_to: date | None,
) -> bool:
    text = message_text(message)
    text_lower = text.lower()
    if hashtag and hashtag.lower() not in text_lower:
        return False
    if search_text and search_text.lower() not in text_lower:
        return False
    if not has_media_type(message, media_type):
        return False
    msg_date = parse_message_date(message)
    if date_from and (msg_date is None or msg_date < date_from):
        return False
    if date_to and (msg_date is None or msg_date > date_to):
        return False
    return True


def filter_export(
    input_path: Path,
    output_path: Path,
    hashtag: str | None = None,
    media_type: str = "all",
    search_text: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    messages, container_key = find_messages_container(payload)
    filtered = [
        message
        for message in messages
        if message_matches(message, hashtag, media_type, search_text, date_from, date_to)
    ]
    preserved = copy.deepcopy(payload)
    if container_key is None:
        preserved = filtered
    elif isinstance(preserved, dict):
        preserved[container_key] = filtered
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(preserved, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(filtered)

