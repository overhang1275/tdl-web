import json
import subprocess
import time
from pathlib import Path

import pytest

from app.config import settings
from app.services.tdl import TdlError, TdlService


def test_export_preserves_previous_cache_on_failure(tmp_path, monkeypatch):
    target = tmp_path / 'export.json'
    target.write_text('{"id":123,"messages":[]}')
    def fail(self, args, **kwargs):
        Path(args[args.index('-o') + 1]).write_text('{"messages":[')
        raise TdlError('timeout')
    monkeypatch.setattr(TdlService, '_run', fail)
    with pytest.raises(TdlError):
        TdlService().export_chat('123', target)
    assert json.loads(target.read_text()) == {'id': 123, 'messages': []}


def test_download_uses_noninteractive_restart_and_message_folders(tmp_path, monkeypatch):
    commands = []
    def run(self, args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(TdlService, '_run', run)
    TdlService().download_from_file(tmp_path / 'batch.json', tmp_path)
    assert '--restart' in commands[0]
    assert '{{ .MessageID }}/{{ filenamify .FileName }}' == commands[0][commands[0].index('--template') + 1]


def test_idle_timeout_terminates_stuck_process(tmp_path, monkeypatch):
    binary = tmp_path / 'fake-tdl'
    binary.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
    binary.chmod(0o755)
    monkeypatch.setattr(settings, 'sessions_dir', tmp_path / 'sessions')
    service = TdlService()
    service.binary = str(binary)
    start = time.monotonic()
    with pytest.raises(TdlError, match='sin progreso'):
        service._run([], timeout=10, progress_bytes=lambda: 0, idle_timeout=0.1)
    assert time.monotonic() - start < 5


def test_progressing_process_can_run_longer_than_idle_limit(tmp_path, monkeypatch):
    marker = tmp_path / 'bytes'
    binary = tmp_path / 'fake-tdl'
    binary.write_text(
        '#!/usr/bin/env python3\nimport time\nfrom pathlib import Path\n'
        f'p = Path({str(marker)!r})\n'
        'for i in range(8):\n p.write_bytes(b"x" * (i + 1))\n time.sleep(0.4)\n'
    )
    binary.chmod(0o755)
    monkeypatch.setattr(settings, 'sessions_dir', tmp_path / 'sessions')
    service = TdlService()
    service.binary = str(binary)
    result = service._run([], timeout=10, idle_timeout=1.5,
                          progress_bytes=lambda: marker.stat().st_size if marker.exists() else 0)
    assert result.returncode == 0
    assert marker.read_bytes() == b'xxxxxxxx'
