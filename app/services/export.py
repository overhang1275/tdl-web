from __future__ import annotations

from pathlib import Path

from app.services.tdl import TdlService


class ExportService:
    def __init__(self, tdl: TdlService | None = None) -> None:
        self.tdl = tdl or TdlService()

    def export_chat(self, chat_id: str, output_path: Path) -> str:
        return self.tdl.export_chat(chat_id, output_path)

