from __future__ import annotations

from redis import Redis
from rq import Queue, Worker

from app.config import settings
from app.database import init_db


def main() -> None:
    init_db()
    connection = Redis.from_url(settings.redis_url)
    worker = Worker([Queue("telegram-downloads", connection=connection)], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
