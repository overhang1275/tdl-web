from __future__ import annotations

import pytest

from app.services.paths import safe_child, sanitize_subfolder


def test_sanitize_subfolder_accepts_safe_name():
    assert sanitize_subfolder("client downloads-01") == "client_downloads-01"


def test_sanitize_subfolder_rejects_traversal():
    with pytest.raises(ValueError):
        sanitize_subfolder("../secret")


def test_safe_child_blocks_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        safe_child(tmp_path, "..", "outside")

