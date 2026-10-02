from __future__ import annotations

import json
from datetime import date

from app.services.filtering import exclude_message_ids, filter_export, message_ids_from_export


def test_filter_export_preserves_messages_container(tmp_path):
    source = tmp_path / "export.json"
    target = tmp_path / "filtered.json"
    source.write_text(
        json.dumps(
            {
                "messages": [
                    {"id": 1, "text": "Hello #demo", "date": "2024-01-10T12:00:00Z", "media": {"type": "photo"}},
                    {"id": 2, "text": "Other", "date": "2024-01-11T12:00:00Z", "media": {"type": "video"}},
                ]
            }
        ),
        encoding="utf-8",
    )

    count = filter_export(source, target, hashtag="#demo", media_type="image")

    assert count == 1
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "messages" in data
    assert data["messages"][0]["id"] == 1


def test_filter_export_supports_text_lists_and_dates(tmp_path):
    source = tmp_path / "export.json"
    target = tmp_path / "filtered.json"
    source.write_text(
        json.dumps(
            [
                {"id": 1, "text": [{"text": "Quarterly"}, " report"], "date": "2024-02-01", "mime_type": "application/pdf"},
                {"id": 2, "text": "Quarterly video", "date": "2024-03-01", "mime_type": "video/mp4"},
            ]
        ),
        encoding="utf-8",
    )

    count = filter_export(
        source,
        target,
        media_type="document",
        search_text="quarterly",
        date_from=date(2024, 1, 1),
        date_to=date(2024, 2, 28),
    )

    assert count == 1
    assert json.loads(target.read_text(encoding="utf-8"))[0]["id"] == 1


def test_exclude_message_ids_preserves_container(tmp_path):
    target = tmp_path / "filtered.json"
    target.write_text(
        json.dumps({"messages": [{"id": 1, "text": "done"}, {"id": 2, "text": "new"}]}),
        encoding="utf-8",
    )

    remaining, skipped = exclude_message_ids(target, target, {"1"})

    assert (remaining, skipped) == (1, 1)
    assert message_ids_from_export(target) == {"2"}
    assert json.loads(target.read_text(encoding="utf-8"))["messages"][0]["id"] == 2
