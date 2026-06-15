from __future__ import annotations

import pytest

from app.services.files import downloaded_file_path, human_size, list_downloaded_files


def test_list_downloaded_files_includes_kind_and_human_size(tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"x" * 2048)
    hidden = tmp_path / ".DS_Store"
    hidden.write_bytes(b"system")
    document = tmp_path / "docs" / "readme.txt"
    document.parent.mkdir()
    document.write_text("hello", encoding="utf-8")

    files = list_downloaded_files(tmp_path)

    assert len(files) == 2
    assert files[0]["relative_path"] == "docs/readme.txt"
    assert files[0]["kind"] == "text"
    assert files[0]["can_preview"] is True
    assert files[1]["relative_path"] == "photo.jpg"
    assert files[1]["kind"] == "image"
    assert files[1]["human_size"] == "2.0 KB"


def test_downloaded_file_path_blocks_traversal(tmp_path):
    with pytest.raises(ValueError):
        downloaded_file_path(tmp_path, "../secret.txt")


def test_downloaded_file_path_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        downloaded_file_path(tmp_path, "missing.txt")


def test_human_size_formats_bytes():
    assert human_size(12) == "12 B"
    assert human_size(1536) == "1.5 KB"
