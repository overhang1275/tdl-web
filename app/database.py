from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401

    settings.ensure_directories()
    Base.metadata.create_all(bind=engine)
    migrate_sqlite()
    migrate_exports_to_chat_paths()


def migrate_sqlite() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "download_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("download_jobs")}
    with engine.begin() as connection:
        if "refresh_export" not in columns:
            connection.execute(text("ALTER TABLE download_jobs ADD COLUMN refresh_export BOOLEAN NOT NULL DEFAULT 1"))
        if "cancel_requested" not in columns:
            connection.execute(text("ALTER TABLE download_jobs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0"))
        if "download_observed_files" not in columns:
            connection.execute(text("ALTER TABLE download_jobs ADD COLUMN download_observed_files INTEGER NOT NULL DEFAULT 0"))
        if "download_observed_bytes" not in columns:
            connection.execute(text("ALTER TABLE download_jobs ADD COLUMN download_observed_bytes INTEGER NOT NULL DEFAULT 0"))
        if "download_speed_bps" not in columns:
            connection.execute(text("ALTER TABLE download_jobs ADD COLUMN download_speed_bps INTEGER NOT NULL DEFAULT 0"))
        if "download_eta_seconds" not in columns:
            connection.execute(text("ALTER TABLE download_jobs ADD COLUMN download_eta_seconds INTEGER"))


def migrate_exports_to_chat_paths() -> None:
    from app.services.paths import chat_path_key, safe_child

    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "download_jobs" not in inspector.get_table_names():
        return
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, chat_id, export_json_path "
                "FROM download_jobs "
                "WHERE export_json_path IS NOT NULL "
                "ORDER BY id DESC"
            )
        ).mappings()
        migrated_chats: set[str] = set()
        for row in rows:
            chat_id = str(row["chat_id"])
            if chat_id in migrated_chats:
                continue
            old_path = Path(str(row["export_json_path"]))
            if not old_path.exists():
                continue
            try:
                new_path = safe_child(settings.exports_dir, chat_path_key(chat_id), "export.json")
            except ValueError:
                continue
            if not new_path.exists():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_path, new_path)
            migrated_chats.add(chat_id)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
