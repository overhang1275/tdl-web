from __future__ import annotations

import pytest

from app.services.paths import chat_path_key, safe_child, sanitize_subfolder


def test_sanitize_subfolder_accepts_safe_name():
    assert sanitize_subfolder("client downloads-01") == "client_downloads-01"


def test_sanitize_subfolder_rejects_traversal():
    with pytest.raises(ValueError):
        sanitize_subfolder("../secret")


def test_safe_child_blocks_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        safe_child(tmp_path, "..", "outside")


def test_chat_path_key_normalizes_usernames_and_ids():
    assert chat_path_key("@my_channel") == "my_channel"
    assert chat_path_key("-1001234567890") == "1001234567890"
