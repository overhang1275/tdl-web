from __future__ import annotations

import subprocess
import threading
import os
import pty
import signal
from dataclasses import dataclass, field
from datetime import datetime

from app.services.tdl import TdlService


@dataclass
class LoginSession:
    process: subprocess.Popen[str] | None = None
    master_fd: int | None = None
    output: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    returncode: int | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class InteractiveLoginService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = LoginSession()

    def start(self) -> LoginSession:
        with self._lock:
            if self._session.running:
                return self._session

            tdl = TdlService()
            args = [
                tdl.binary,
                "--ns",
                tdl.settings_namespace,
                "--storage",
                tdl.storage_arg,
                "login",
                "--type",
                "code",
            ]
            tdl.ensure_runtime_dirs()
            session = LoginSession(started_at=datetime.utcnow())
            try:
                master_fd, slave_fd = pty.openpty()
                process = subprocess.Popen(
                    args,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=tdl.env(),
                    close_fds=True,
                )
                os.close(slave_fd)
            except FileNotFoundError as exc:
                try:
                    os.close(master_fd)
                    os.close(slave_fd)
                except Exception:
                    pass
                session.error = f"tdl binary not found: {tdl.binary}"
                session.finished_at = datetime.utcnow()
                self._session = session
                raise RuntimeError(session.error) from exc

            session.process = process
            session.master_fd = master_fd
            session.output.append("$ " + " ".join(args))
            self._session = session
            threading.Thread(target=self._read_output, args=(session,), daemon=True).start()
            threading.Thread(target=self._wait_process, args=(session,), daemon=True).start()
            return session

    def cancel(self) -> LoginSession:
        with self._lock:
            session = self._session
            if session.running and session.process is not None:
                session.process.send_signal(signal.SIGTERM)
                session.output.append("Login process cancelled.")
            return session

    def send(self, value: str) -> LoginSession:
        value = value.rstrip("\r\n")
        with self._lock:
            session = self._session
            if not session.running or session.master_fd is None:
                raise RuntimeError("No active login process")
            os.write(session.master_fd, (value + "\n").encode("utf-8"))
            session.output.append("> " + ("********" if self._looks_sensitive(value) else value))
            return session

    def status(self) -> LoginSession:
        with self._lock:
            return self._session

    def _read_output(self, session: LoginSession) -> None:
        process = session.process
        if process is None or session.master_fd is None:
            return
        while True:
            try:
                chunk = os.read(session.master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            responses = self._cursor_position_responses(chunk)
            if responses:
                try:
                    os.write(session.master_fd, responses)
                except OSError:
                    pass
            text = chunk.decode("utf-8", errors="replace").replace("\r\n", "\n").rstrip("\r")
            with self._lock:
                session.output.append(text)

    def _wait_process(self, session: LoginSession) -> None:
        process = session.process
        if process is None:
            return
        returncode = process.wait()
        with self._lock:
            session.returncode = returncode
            session.finished_at = datetime.utcnow()
            if session.master_fd is not None:
                try:
                    os.close(session.master_fd)
                except OSError:
                    pass
                session.master_fd = None
            if returncode == 0:
                session.output.append("Login process finished successfully.")
            else:
                session.output.append(f"Login process exited with code {returncode}.")

    @staticmethod
    def _looks_sensitive(value: str) -> bool:
        return len(value) >= 6 and not value.startswith("+") and not value.isdigit()

    @staticmethod
    def _cursor_position_responses(chunk: bytes) -> bytes:
        responses: list[bytes] = []
        start = 0
        query = b"\x1b[6n"
        while True:
            index = chunk.find(query, start)
            if index == -1:
                break
            prefix = chunk[max(0, index - 40):index]
            if b"\x1b[999;999f" in prefix or b"[999;999f" in prefix:
                responses.append(b"\x1b[24;80R")
            else:
                responses.append(b"\x1b[1;1R")
            start = index + len(query)
        return b"".join(responses)


interactive_login_service = InteractiveLoginService()
