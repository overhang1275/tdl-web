from __future__ import annotations

from app.main import app, health
from app.main import apply_template_prefill, paginate_chats, paginate_downloads
from app.models import DownloadTemplate, MediaType
from app.services.errors import friendly_error
from app.services import chat_cache
from app.services.search import global_search


def test_health_endpoint():
    assert app.title == "Telegram Downloader"
    assert health() == {"status": "ok"}


def test_paginate_chats_filters_by_id_and_username():
    chats = [
        {"id": "100", "title": "General", "username": "main"},
        {"id": "200", "title": "Videos", "username": "media"},
    ]

    page = paginate_chats(chats, q="media", page=1, per_page=10)

    assert page["total"] == 1
    assert page["items"][0]["id"] == "200"


def test_paginate_downloads_filters_sorts_and_pages():
    files = [
        {"relative_path": "b/video.mp4", "kind": "video", "size": 300, "modified_at": 2},
        {"relative_path": "a/photo.jpg", "kind": "image", "size": 100, "modified_at": 3},
        {"relative_path": "c/video-small.mp4", "kind": "video", "size": 50, "modified_at": 1},
    ]

    page = paginate_downloads(files, q="video", kind="video", sort="size-desc", page=1, per_page=12)

    assert page["total"] == 2
    assert [item["relative_path"] for item in page["items"]] == ["b/video.mp4", "c/video-small.mp4"]
    assert page["page_count"] == 1


def test_apply_template_prefill_overrides_defined_values_only():
    prefill = {
        "chat_id": "100",
        "chat_title": "Original",
        "hashtag": "",
        "media_type": "all",
        "search_text": "",
        "date_from": "",
        "date_to": "",
        "skip_same": True,
        "output_subfolder": "download",
        "export_exists": False,
        "export_path": "",
        "refresh_export": False,
        "template_id": "",
    }
    template = DownloadTemplate(
        id=7,
        name="Videos",
        media_type=MediaType.video,
        output_subfolder="videos",
        skip_same=False,
        refresh_export=True,
    )

    result = apply_template_prefill(prefill, template)

    assert result["chat_id"] == "100"
    assert result["media_type"] == "video"
    assert result["output_subfolder"] == "videos"
    assert result["skip_same"] is False
    assert result["template_id"] == 7


def test_chats_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_cache.settings, "base_dir", tmp_path)
    chats = [{"id": "100", "title": "General"}]

    updated_at = chat_cache.write_chats_cache(chats)
    cached_chats, cached_at = chat_cache.read_chats_cache()

    assert cached_chats == chats
    assert cached_at == updated_at


def test_friendly_error_detects_common_cases():
    assert friendly_error("Error 61 connecting to 127.0.0.1:6379")["title"] == "Redis apagado o inaccesible"
    assert friendly_error("Filtered messages: 0")["title"] == "No hay mensajes con esos filtros"
    assert friendly_error("json decode error")["title"] == "Export corrupto o inválido"


def test_global_search_empty_query_returns_empty_groups():
    assert global_search(None, "   ") == {"chats": [], "jobs": [], "files": []}
