import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_functions(tmp_path, script_name, commands, *, env_file):
    source = (ROOT / 'scripts' / script_name).read_text()
    marker = '\napt-get update\n' if script_name == 'install.sh' else '\nensure_wipe\n'
    prefix = source.split(marker, 1)[0]
    binaries = tmp_path / 'bin'
    binaries.mkdir(exist_ok=True)
    (binaries / 'id').write_text('#!/bin/sh\necho 0\n')
    (binaries / 'id').chmod(0o755)
    # Only the privilege boundary is replaced; Python runs the real migration.
    (binaries / 'runuser').write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    (binaries / 'runuser').chmod(0o755)
    for name in ('chown', 'chmod'):
        (binaries / name).write_text('#!/bin/sh\nexit 0\n')
        (binaries / name).chmod(0o755)
    program = tmp_path / 'script-test.sh'
    program.write_text(prefix + '\nENV_FILE="$1"\nAPP_DIR="$2"\nVENV_DIR="$2/.venv"\n' + commands)
    env = dict(os.environ, PATH=f'{binaries}:{os.environ["PATH"]}')
    return subprocess.run(['bash', str(program), str(env_file), str(ROOT)],
                          env=env, capture_output=True, text=True)


@pytest.mark.parametrize('script', ['install.sh', 'update.sh'])
def test_transfer_defaults_preserve_custom_values(tmp_path, script):
    env_file = tmp_path / 'service.env'
    env_file.write_text('DOWNLOAD_BATCH_SIZE=25\nEXPORT_BATCH_SIZE=900\n')
    result = run_functions(tmp_path, script, 'ensure_transfer_settings\n', env_file=env_file)
    assert result.returncode == 0, result.stderr
    lines = env_file.read_text().splitlines()
    assert 'DOWNLOAD_BATCH_SIZE=25' in lines
    assert 'EXPORT_BATCH_SIZE=900' in lines
    assert 'DOWNLOAD_IDLE_TIMEOUT_SECONDS=600' in lines


@pytest.mark.parametrize('script', ['install.sh', 'update.sh'])
def test_script_migration_uses_service_environment(tmp_path, script):
    env_file = tmp_path / 'service.env'
    data = tmp_path / 'service-data'
    env_file.write_text(f'DATA_DIR={data}\nDATABASE_URL=sqlite:///{data}/jobs.sqlite3\n')
    result = run_functions(tmp_path, script, 'migrate_database\n', env_file=env_file)
    assert result.returncode == 0, result.stderr
    import sqlite3
    with sqlite3.connect(data / 'jobs.sqlite3') as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'download_jobs', 'download_files'} <= tables


def test_update_backup_includes_uncheckpointed_wal(tmp_path):
    import sqlite3
    data = tmp_path / 'data'
    data.mkdir()
    source = data / 'jobs.sqlite3'
    connection = sqlite3.connect(source)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.execute('CREATE TABLE evidence (value TEXT)')
    connection.execute("INSERT INTO evidence VALUES ('saved progress')")
    connection.commit()
    env_file = tmp_path / 'service.env'
    env_file.write_text(f'DATA_DIR={data}\nDATABASE_URL=sqlite:///{source}\n')
    try:
        result = run_functions(tmp_path, 'update.sh', 'DATA_DIR="$(env_value DATA_DIR)"\nbackup_before_update\n', env_file=env_file)
        assert result.returncode == 0, result.stderr
        backups = list((data / 'backups').glob('*.sqlite3'))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute('SELECT value FROM evidence').fetchone() == ('saved progress',)
    finally:
        connection.close()
