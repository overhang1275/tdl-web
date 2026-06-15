from __future__ import annotations

from app.main import app, health
from app.main import paginate_chats


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
