from __future__ import annotations

from pathlib import Path

from app.services.tdl import TdlService


class DownloadService:
    def __init__(self, tdl: TdlService | None = None) -> None:
        self.tdl = tdl or TdlService()

    def download_from_file(self, filtered_json: Path, output_dir: Path, skip_same: bool = True) -> str:
        return self.tdl.download_from_file(filtered_json, output_dir, skip_same=skip_same)

