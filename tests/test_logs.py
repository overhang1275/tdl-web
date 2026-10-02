from __future__ import annotations

from app.config import settings
from app.services.logs import read_job_log


def test_read_job_log_tails_50kb_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "logs_dir", tmp_path)
    (tmp_path / "job-1.log").write_text("x" * 60_000, encoding="utf-8")

    assert len(read_job_log(1)) == 50_000
