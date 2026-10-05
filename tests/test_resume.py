import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import worker, models
from app.config import settings
from app.database import Base
from app.models import DownloadJob, JobStatus
from app.schemas import JobCreate
from app.services import jobs
from app.services.tdl import TdlError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    for name in ('exports_dir', 'downloads_dir', 'logs_dir'):
        monkeypatch.setattr(settings, name, tmp_path / name)
    monkeypatch.setattr(worker, 'engine', engine)
    monkeypatch.setattr(worker, 'SessionLocal', sessions)
    monkeypatch.setattr(worker, 'init_db', lambda: None)
    queue = SimpleNamespace(connection=SimpleNamespace(ping=lambda: True), tasks=[])

    def enqueue(function, *args, **kwargs):
        queue.tasks.append((function, args, kwargs))
        return SimpleNamespace(id=kwargs['job_id'])

    queue.enqueue = enqueue
    queue.enqueue_in = lambda delay, function, *args, **kwargs: enqueue(function, *args, **kwargs)
    monkeypatch.setattr(jobs, 'get_queue', lambda: queue)
    with sessions() as db:
        yield db, queue
    engine.dispose()


def make_job(db, messages):
    job = jobs.create_job(db, JobCreate(chat_id='123', output_subfolder='download', refresh_export=False), enqueue=False)
    source = Path(job.export_json_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({'id': 123, 'messages': messages}))
    return job


def run_next(queue):
    _, args, _ = queue.tasks.pop(0)
    worker.run_download_job(*args)


def successful_download(self, source, destination, **kwargs):
    for message in json.loads(source.read_text())['messages']:
        folder = destination / str(message['id'])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / message['file']).write_bytes(b'complete')
    if kwargs.get('should_cancel'):
        kwargs['should_cancel']()
    return ''


def test_interruption_records_only_final_files_and_retry_keeps_same_job(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}, {'id': 2, 'file': 'b.mp4'}])

    def interrupted(self, source, destination, **kwargs):
        for ident, name in [(1, 'a.mp4'), (2, 'b.mp4.tmp')]:
            folder = destination / str(ident)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b'complete')
        raise TdlError('tdl command timed out')

    monkeypatch.setattr(worker.TdlService, 'download_from_file', interrupted)
    worker.run_download_job(job.id)
    # Preparation is independently queued; execute its first download batch.
    run_next(queue)
    db.expire_all()
    items = list(db.scalars(select(models.DownloadFile).order_by(models.DownloadFile.message_id)))
    assert [item.status for item in items] == ['completed', 'pending']
    assert db.get(DownloadJob, job.id).total_downloaded_files == 1

    # Simulate the subsequent worker being lost before it executes.
    queue.tasks.clear()
    job = db.get(DownloadJob, job.id)
    job.status = JobStatus.failed
    db.commit()
    resumed = jobs.retry_job(db, job)
    again = jobs.retry_job(db, resumed)
    assert resumed.id == again.id == job.id
    assert len(queue.tasks) == 1

    seen = []
    def finish(self, source, destination, **kwargs):
        seen.extend(m['id'] for m in json.loads(source.read_text())['messages'])
        return successful_download(self, source, destination, **kwargs)
    monkeypatch.setattr(worker.TdlService, 'download_from_file', finish)
    run_next(queue)
    db.expire_all()
    assert seen == [2]
    assert db.get(DownloadJob, job.id).status == JobStatus.completed
    assert db.get(DownloadJob, job.id).total_downloaded_files == 2


def test_batches_are_separate_queue_jobs(runtime, monkeypatch):
    db, queue = runtime
    monkeypatch.setattr(settings, 'download_batch_size', 2, raising=False)
    job = make_job(db, [{'id': n, 'file': f'{n}.mp4'} for n in range(1, 6)])
    batches = []
    def download(self, source, destination, **kwargs):
        batches.append(len(json.loads(source.read_text())['messages']))
        return successful_download(self, source, destination, **kwargs)
    monkeypatch.setattr(worker.TdlService, 'download_from_file', download)
    worker.run_download_job(job.id)
    while queue.tasks:
        run_next(queue)
    db.expire_all()
    assert batches == [2, 2, 1]
    assert db.get(DownloadJob, job.id).status == JobStatus.completed
    assert db.get(DownloadJob, job.id).total_downloaded_files == 5


def test_missing_file_is_downloaded_again_and_unrelated_files_do_not_count(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    Path(job.download_path).mkdir(parents=True)
    (Path(job.download_path) / 'unrelated.mp4').write_bytes(b'other')
    monkeypatch.setattr(worker.TdlService, 'download_from_file', successful_download)
    worker.run_download_job(job.id)
    run_next(queue)
    db.expire_all()
    item = db.scalar(select(models.DownloadFile))
    Path(item.local_path).unlink()
    job = db.get(DownloadJob, job.id)
    job.status = JobStatus.failed
    db.commit()
    jobs.retry_job(db, job)
    run_next(queue)
    db.expire_all()
    assert Path(db.scalar(select(models.DownloadFile)).local_path).is_file()
    assert db.get(DownloadJob, job.id).total_downloaded_files == 1


def test_failed_message_does_not_block_other_files(runtime, monkeypatch):
    db, queue = runtime
    monkeypatch.setattr(settings, 'download_batch_size', 1, raising=False)
    job = make_job(db, [{'id': 1, 'file': 'bad.mp4'}, {'id': 2, 'file': 'good.mp4'}])
    def download(self, source, destination, **kwargs):
        if json.loads(source.read_text())['messages'][0]['id'] == 1:
            raise TdlError('network timeout')
        return successful_download(self, source, destination, **kwargs)
    monkeypatch.setattr(worker.TdlService, 'download_from_file', download)
    worker.run_download_job(job.id)
    for _ in range(10):
        if not queue.tasks:
            break
        run_next(queue)
    db.expire_all()
    items = list(db.scalars(select(models.DownloadFile).order_by(models.DownloadFile.message_id)))
    assert [(item.status, item.attempts) for item in items] == [('failed', 3), ('completed', 1)]
    assert db.get(DownloadJob, job.id).status == JobStatus.failed
    assert db.get(DownloadJob, job.id).total_downloaded_files == 1


def test_non_media_messages_are_not_counted_as_downloads(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': '', 'text': 'hello'}])
    monkeypatch.setattr(worker.TdlService, 'download_from_file', lambda *a, **k: pytest.fail('No media'))
    worker.run_download_job(job.id)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.completed
    assert db.get(DownloadJob, job.id).total_downloaded_files == 0
    assert not queue.tasks


def test_export_ranges_resume_and_publish_only_when_complete(runtime, monkeypatch):
    db, queue = runtime
    monkeypatch.setattr(settings, 'export_batch_size', 2, raising=False)
    job = jobs.create_job(db, JobCreate(chat_id='123', output_subfolder='download', export_only=True), enqueue=False)
    ranges = []
    def export(self, chat, target, **kwargs):
        if kwargs.get('last'):
            messages = [{'id': 5, 'file': '5.mp4'}]
        else:
            low, high = kwargs['id_range']
            ranges.append((low, high))
            messages = [{'id': n, 'file': f'{n}.mp4'} for n in range(low, high + 1)]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'id': 123, 'messages': messages}))
        return ''
    monkeypatch.setattr(worker.TdlService, 'export_chat', export)
    worker.run_download_job(job.id)
    assert not Path(job.export_json_path).exists()
    # Discovering the upper ID and exporting a range each get their own budget.
    assert ranges == []
    run_next(queue)
    assert not Path(job.export_json_path).exists()
    db.expire_all()
    assert db.get(DownloadJob, job.id).export_cursor == 3
    # Retry after a server interruption must retain its completed ID ranges.
    queue.tasks.clear()
    job = db.get(DownloadJob, job.id)
    job.status = JobStatus.failed
    db.commit()
    jobs.retry_job(db, job)
    while queue.tasks:
        run_next(queue)
    assert ranges == [(4, 5), (2, 3), (1, 1)]
    payload = json.loads(Path(job.export_json_path).read_text())
    assert [message['id'] for message in payload['messages']] == [5, 4, 3, 2, 1]


def test_retry_adopts_legacy_completed_files_and_keeps_filtered_selection(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}, {'id': 2, 'file': 'b.mp4'}])
    Path(job.filtered_json_path).write_text(json.dumps({'id': 123, 'messages': [{'id': 1, 'file': 'a.mp4'}]}))
    root = Path(job.download_path)
    root.mkdir(parents=True)
    (root / '123_1_a.mp4').write_bytes(b'complete')
    job.status = JobStatus.failed
    job.refresh_export = True
    db.commit()
    monkeypatch.setattr(worker.TdlService, 'export_chat', lambda *a, **k: pytest.fail('Retry must reuse selection'))
    monkeypatch.setattr(worker.TdlService, 'download_from_file', lambda *a, **k: pytest.fail('File is already complete'))
    jobs.retry_job(db, job)
    run_next(queue)
    db.expire_all()
    job = db.get(DownloadJob, job.id)
    assert job.total_filtered_messages == job.total_downloaded_files == 1
    assert job.status == JobStatus.completed


def test_cancelled_job_does_not_schedule_continuation(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}, {'id': 2, 'file': 'b.mp4'}])
    monkeypatch.setattr(settings, 'download_batch_size', 1)
    worker.run_download_job(job.id)
    def download(self, source, destination, **kwargs):
        successful_download(self, source, destination, **kwargs)
        db.expire_all()
        current = db.get(DownloadJob, job.id)
        current.cancel_requested = True
        db.commit()
    monkeypatch.setattr(worker.TdlService, 'download_from_file', download)
    run_next(queue)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.cancelled
    assert db.get(DownloadJob, job.id).total_downloaded_files == 1
    assert not queue.tasks


def test_stale_queue_token_cannot_process_a_retried_job(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    jobs.enqueue_job(db, job)
    old_args = queue.tasks.pop()[1]
    job.status = JobStatus.failed
    db.commit()
    jobs.retry_job(db, job)
    worker.run_download_job(*old_args)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.pending
    assert db.get(DownloadJob, job.id).transfer_initialized is False


def test_status_partial_shows_retry_button_after_live_failure(runtime, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    job.status = JobStatus.failed
    db.commit()
    monkeypatch.setattr(settings, 'web_password', None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get(f'/jobs/{job.id}/status')
        assert response.status_code == 200
        assert 'Reintentar pendientes' in response.text
        assert f'action="/jobs/{job.id}/retry"' in response.text
        response = TestClient(app).post(f'/jobs/{job.id}/retry', follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == f'/jobs/{job.id}'
    finally:
        app.dependency_overrides.pop(get_db)


def test_cancelling_during_enqueue_does_not_revive_job(runtime):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    job.status = JobStatus.running
    db.commit()
    # Simulate a separate web request after a worker loaded its job object.
    with worker.SessionLocal() as control:
        current = control.get(DownloadJob, job.id)
        current.cancel_requested = True
        control.commit()
    jobs.enqueue_job(db, job)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.cancelled
    assert not queue.tasks


def test_reconciliation_does_not_fail_new_continuation(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    job.rq_job_id, job.status = 'old', JobStatus.running
    db.commit()
    monkeypatch.setattr(jobs.Redis, 'from_url', lambda *a, **k: queue.connection)
    def fetch(*args, **kwargs):
        with worker.SessionLocal() as control:
            current = control.get(DownloadJob, job.id)
            current.rq_job_id, current.status = 'new', JobStatus.pending
            control.commit()
        return SimpleNamespace(get_status=lambda: 'finished')
    monkeypatch.setattr(jobs.Job, 'fetch', fetch)
    jobs.sync_pending_jobs_with_queue(db)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.pending
    assert db.get(DownloadJob, job.id).rq_job_id == 'new'


def test_missing_queue_job_becomes_resumable(runtime, monkeypatch):
    from rq.exceptions import NoSuchJobError
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    job.rq_job_id = 'missing'
    db.commit()
    monkeypatch.setattr(jobs.Redis, 'from_url', lambda *a, **k: queue.connection)
    def fetch(*args, **kwargs):
        raise NoSuchJobError('missing')
    monkeypatch.setattr(jobs.Job, 'fetch', fetch)
    jobs.sync_pending_jobs_with_queue(db)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.failed


def test_exhausted_interrupted_file_is_not_reported_completed(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    worker.run_download_job(job.id)
    item = db.scalar(select(models.DownloadFile))
    item.status, item.attempts = 'downloading', 3
    db.commit()
    monkeypatch.setattr(worker.TdlService, 'download_from_file', lambda *a, **k: pytest.fail('Exhausted'))
    run_next(queue)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.failed
    assert db.get(DownloadJob, job.id).total_downloaded_files == 0


def test_retry_never_confirms_truncated_final_file_after_download_failure(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    monkeypatch.setattr(worker.TdlService, 'download_from_file', successful_download)
    worker.run_download_job(job.id)
    run_next(queue)
    db.expire_all()
    item = db.scalar(select(models.DownloadFile))
    Path(item.local_path).write_bytes(b'bad')
    job = db.get(DownloadJob, job.id)
    job.status = JobStatus.failed
    db.commit()
    jobs.retry_job(db, job)
    def fail(*args, **kwargs):
        raise TdlError('timeout')
    monkeypatch.setattr(worker.TdlService, 'download_from_file', fail)
    while queue.tasks:
        run_next(queue)
    db.expire_all()
    assert db.get(DownloadJob, job.id).total_downloaded_files == 0
    assert db.get(DownloadJob, job.id).status == JobStatus.failed


@pytest.mark.parametrize('container', ['list', 'items', 'data'])
def test_legacy_export_shapes_are_normalized_for_tdl(runtime, monkeypatch, container):
    db, queue = runtime
    messages = [{'id': 1, 'file': 'a.mp4'}]
    job = make_job(db, messages)
    payload = messages if container == 'list' else {'id': 123, container: messages}
    Path(job.export_json_path).write_text(json.dumps(payload))
    def download(self, source, destination, **kwargs):
        batch = json.loads(source.read_text())
        assert batch['id'] == 123
        assert set(batch) == {'id', 'messages'}
        assert batch['messages'][0]['type'] == 'message'
        return successful_download(self, source, destination, **kwargs)
    monkeypatch.setattr(worker.TdlService, 'download_from_file', download)
    worker.run_download_job(job.id)
    run_next(queue)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.completed


def test_original_filename_ending_in_tmp_is_verified(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'source.tmp'}])
    monkeypatch.setattr(worker.TdlService, 'download_from_file', successful_download)
    worker.run_download_job(job.id)
    while queue.tasks:
        run_next(queue)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.completed


def test_old_worker_cannot_finish_new_execution(runtime):
    db, queue = runtime
    job = make_job(db, [])
    job.rq_job_id, job.status = 'old', JobStatus.running
    db.commit()
    with worker.SessionLocal() as control:
        current = control.get(DownloadJob, job.id)
        current.rq_job_id, current.status = 'new', JobStatus.pending
        control.commit()
    worker.finish_job(db, job)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.pending
    assert db.get(DownloadJob, job.id).rq_job_id == 'new'


def test_new_job_reuses_verified_files_at_same_destination(runtime, monkeypatch):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    monkeypatch.setattr(worker.TdlService, 'download_from_file', successful_download)
    worker.run_download_job(job.id)
    run_next(queue)
    other = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    monkeypatch.setattr(worker.TdlService, 'download_from_file', lambda *a, **k: pytest.fail('Already verified'))
    worker.run_download_job(other.id)
    db.expire_all()
    assert db.get(DownloadJob, other.id).status == JobStatus.completed
    assert db.get(DownloadJob, other.id).total_downloaded_files == 1


def test_failed_continuation_enqueue_keeps_progress_and_can_retry(runtime, monkeypatch):
    from redis.exceptions import ConnectionError
    from app.services.jobs import QueueUnavailableError
    db, queue = runtime
    monkeypatch.setattr(settings, 'download_batch_size', 1)
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}, {'id': 2, 'file': 'b.mp4'}])
    monkeypatch.setattr(worker.TdlService, 'download_from_file', successful_download)
    worker.run_download_job(job.id)
    _, args, _ = queue.tasks.pop(0)
    original = queue.enqueue
    def fail(*args, **kwargs):
        raise ConnectionError('Redis unavailable')
    queue.enqueue = fail
    with pytest.raises(QueueUnavailableError):
        worker.run_download_job(*args)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.failed
    assert db.get(DownloadJob, job.id).total_downloaded_files == 1
    queue.enqueue = original
    jobs.retry_job(db, db.get(DownloadJob, job.id))
    while queue.tasks:
        run_next(queue)
    db.expire_all()
    assert db.get(DownloadJob, job.id).total_downloaded_files == 2


def test_sqlite_migration_preserves_existing_jobs(tmp_path, monkeypatch):
    from sqlalchemy import inspect, text
    from app import database
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(settings, 'database_url', f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE download_jobs (id INTEGER PRIMARY KEY, chat_id TEXT, export_json_path TEXT)'))
        connection.execute(text("INSERT INTO download_jobs VALUES (7, '123', NULL)"))
    database.migrate_sqlite()
    database.migrate_sqlite()  # Restart must be idempotent.
    columns = {column['name'] for column in inspect(engine).get_columns('download_jobs')}
    assert {'transfer_initialized', 'total_failed_files', 'export_cursor', 'export_upper_id', 'rq_enqueued_at'} <= columns
    with engine.connect() as connection:
        assert connection.execute(text('SELECT id, chat_id, transfer_initialized FROM download_jobs')).one() == (7, '123', 0)
    engine.dispose()


def test_terminal_transition_honors_late_cancellation(runtime):
    db, queue = runtime
    job = make_job(db, [])
    job.status = JobStatus.running
    db.commit()
    with worker.SessionLocal() as control:
        current = control.get(DownloadJob, job.id)
        current.cancel_requested = True
        control.commit()
    assert worker.mark_terminal(db, job, JobStatus.completed) is False
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.cancelled


def test_retry_preparation_failure_does_not_leave_job_pending(runtime):
    db, queue = runtime
    job = make_job(db, [{'id': 1, 'file': 'a.mp4'}])
    Path(job.filtered_json_path).write_text('{broken')
    job.status = JobStatus.failed
    db.commit()
    with pytest.raises(ValueError):
        jobs.retry_job(db, job)
    db.expire_all()
    assert db.get(DownloadJob, job.id).status == JobStatus.failed
    assert not queue.tasks
