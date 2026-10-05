from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.config import settings


class TdlError(RuntimeError):
    pass


class TdlCancelled(TdlError):
    pass


class TdlService:
    def __init__(self) -> None:
        self.binary = self._resolve_binary(settings.tdl_binary)
        self.timeout = settings.command_timeout_seconds

    def _resolve_binary(self, configured: str) -> str:
        if "/" in configured:
            return configured
        return shutil.which(configured) or configured

    @property
    def storage_path(self) -> Path:
        return settings.sessions_dir / "tdl-data"

    @property
    def storage_arg(self) -> str:
        return f"type=bolt,path={self.storage_path}"

    @property
    def settings_namespace(self) -> str:
        return settings.tdl_namespace

    def ensure_runtime_dirs(self) -> None:
        settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict[str, str]:
        return self._env()

    def cli_login_command(self) -> str:
        return (
            f"HOME={settings.sessions_dir} {self.binary} "
            f"--ns {settings.tdl_namespace} --storage {self.storage_arg} login --type code"
        )

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(settings.sessions_dir)
        env["XDG_CONFIG_HOME"] = str(settings.sessions_dir)
        env["TDL_NS"] = settings.tdl_namespace
        env.setdefault("TERM", "xterm-256color")
        return env

    def _run(
        self,
        args: list[str],
        timeout: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
        progress_bytes: Callable[[], int] | None = None,
        idle_timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.ensure_runtime_dirs()
        full_args = [
            self.binary,
            "--ns",
            settings.tdl_namespace,
            "--storage",
            self.storage_arg,
            *args,
        ]
        started_at = time.monotonic()
        last_progress_at = started_at
        last_bytes = progress_bytes() if progress_bytes else 0
        limit = timeout or self.timeout
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file, tempfile.TemporaryFile(
            mode="w+", encoding="utf-8"
        ) as stderr_file:
            try:
                process = subprocess.Popen(
                    full_args,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=self._env(),
                    text=True,
                )
            except FileNotFoundError as exc:
                raise TdlError(f"tdl binary not found: {self.binary}") from exc
            try:
                while process.poll() is None:
                    if should_cancel and should_cancel():
                        raise TdlCancelled("tdl command cancelled")
                    now = time.monotonic()
                    if progress_bytes:
                        current_bytes = progress_bytes()
                        if current_bytes != last_bytes:
                            last_progress_at, last_bytes = now, current_bytes
                        elif idle_timeout and now - last_progress_at > idle_timeout:
                            raise TdlError(f"Descarga sin progreso durante {idle_timeout} segundos")
                    if now - started_at > limit:
                        raise TdlError(f"tdl command timed out after {limit} seconds")
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
        result = subprocess.CompletedProcess(full_args, process.returncode, stdout, stderr)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "unknown tdl error").strip()
            raise TdlError(details)
        return result

    def is_logged_in(self) -> bool:
        try:
            self._run(["chat", "ls"], timeout=30)
            return True
        except TdlError:
            return False

    def login(self, phone: str | None = None, code: str | None = None, password: str | None = None) -> str:
        raise TdlError(
            "This tdl version uses an interactive login flow. Run it once from a terminal: "
            f"{self.cli_login_command()}"
        )

    def list_chats(self) -> list[dict[str, Any]]:
        result = self._run(["chat", "ls", "-o", "json"], timeout=120)
        text = result.stdout.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [{"id": line.split(maxsplit=1)[0], "title": line} for line in text.splitlines() if line.strip()]
        if isinstance(payload, list):
            return [self._normalize_chat(chat) for chat in payload if isinstance(chat, dict)]
        if isinstance(payload, dict):
            for key in ("chats", "items", "data"):
                if isinstance(payload.get(key), list):
                    return [self._normalize_chat(chat) for chat in payload[key] if isinstance(chat, dict)]
        return []

    def _normalize_chat(self, chat: dict[str, Any]) -> dict[str, Any]:
        chat_id = chat.get("id") or chat.get("ID") or chat.get("chat_id") or chat.get("ChatID")
        title = (
            chat.get("visible_name")
            or chat.get("title")
            or chat.get("name")
            or chat.get("Title")
            or chat.get("Name")
            or ""
        )
        username = chat.get("username") or chat.get("Username") or ""
        return {
            **chat,
            "id": chat_id,
            "title": title,
            "username": username,
            "type": chat.get("type") or chat.get("Type") or "",
            "topics_count": len(chat.get("topics") or []),
        }

    def export_chat(
        self, chat_id: str, output_path: Path,
        should_cancel: Callable[[], bool] | None = None,
        id_range: tuple[int, int] | None = None,
        last: int | None = None,
    ) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(output_path.name + ".partial")
        args = ["chat", "export", "-c", chat_id, "--with-content", "--all", "-o", str(temporary)]
        if id_range:
            args.extend(["--type", "id", "--input", f"{id_range[0]},{id_range[1]}"])
        elif last:
            args.extend(["--type", "last", "--input", str(last)])
        result = self._run(args, should_cancel=should_cancel)
        json.loads(temporary.read_text(encoding="utf-8"))
        temporary.replace(output_path)
        return result.stdout.strip()

    def download_from_file(
        self,
        filtered_json: Path,
        output_dir: Path,
        skip_same: bool = True,
        should_cancel: Callable[[], bool] | None = None,
        progress_bytes: Callable[[], int] | None = None,
    ) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        args = ["download", "-f", str(filtered_json), "-d", str(output_dir), "--restart",
                "--template", "{{ .MessageID }}/{{ filenamify .FileName }}"]
        if skip_same:
            args.append("--skip-same")
        result = self._run(args, should_cancel=should_cancel, progress_bytes=progress_bytes,
                           idle_timeout=settings.download_idle_timeout_seconds)
        return result.stdout.strip()
