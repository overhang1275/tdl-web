from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file()


class Settings:
    app_name: str = "Telegram Downloader"
    environment: str
    secret_key: str
    database_url: str
    redis_url: str
    base_dir: Path
    sessions_dir: Path
    exports_dir: Path
    downloads_dir: Path
    logs_dir: Path
    tdl_binary: str
    tdl_namespace: str
    command_timeout_seconds: int
    web_password: str | None

    def __init__(self) -> None:
        self.environment = os.getenv("APP_ENV", "development")
        self.secret_key = os.getenv("SECRET_KEY", "change-me")
        self.base_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
        self.sessions_dir = Path(os.getenv("TDL_SESSIONS_DIR", self.base_dir / "sessions")).resolve()
        self.exports_dir = Path(os.getenv("EXPORTS_DIR", self.base_dir / "exports")).resolve()
        self.downloads_dir = Path(os.getenv("DOWNLOADS_DIR", self.base_dir / "downloads")).resolve()
        self.logs_dir = Path(os.getenv("LOGS_DIR", self.base_dir / "logs")).resolve()
        self.database_url = os.getenv("DATABASE_URL", f"sqlite:///{self.base_dir / 'telegram_downloader.sqlite3'}")
        self.redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self.tdl_binary = os.getenv("TDL_BINARY", "tdl")
        self.tdl_namespace = os.getenv("TDL_NAMESPACE", "default")
        self.command_timeout_seconds = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "7200"))
        self.web_password = os.getenv("WEB_PASSWORD") or None

    def ensure_directories(self) -> None:
        for path in (self.base_dir, self.sessions_dir, self.exports_dir, self.downloads_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
