from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.services.tdl import TdlService


class ExportService:
    def __init__(self, tdl: TdlService | None = None) -> None:
        self.tdl = tdl or TdlService()

    def export_chat(self, chat_id: str, output_path: Path, should_cancel: Callable[[], bool] | None = None) -> str:
        return self.tdl.export_chat(chat_id, output_path, should_cancel=should_cancel)
