from __future__ import annotations

from app.services.tdl import TdlService


class SessionService:
    def __init__(self, tdl: TdlService | None = None) -> None:
        self.tdl = tdl or TdlService()

    def is_logged_in(self) -> bool:
        return self.tdl.is_logged_in()

    def login(self, phone: str | None = None, code: str | None = None, password: str | None = None) -> str:
        return self.tdl.login(phone=phone, code=code, password=password)

