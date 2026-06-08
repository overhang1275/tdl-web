from __future__ import annotations

from app.main import app, health


def test_health_endpoint():
    assert app.title == "Telegram Downloader"
    assert health() == {"status": "ok"}
