from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import DownloadJob
from app.services.jobs import count_jobs, list_jobs


def test_list_jobs_limits_in_sql_order(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    base = datetime(2024, 1, 1)
    for index in range(5):
        session.add(
            DownloadJob(
                chat_id=str(index),
                output_subfolder="download",
                created_at=base + timedelta(minutes=index),
            )
        )
    session.commit()

    jobs = list_jobs(session, limit=2, offset=1)

    assert count_jobs(session) == 5
    assert [job.chat_id for job in jobs] == ["3", "2"]
