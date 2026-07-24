from __future__ import annotations

import base64
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.main import app, health, job_delete
from app.main import apply_template_prefill, paginate_chats, paginate_downloads, start_local_services
from app.models import DownloadTemplate, JobStage, JobStatus, MediaType
from app.services.errors import friendly_error
from app.services import chat_cache
from app.config import settings
from app.services.jobs import DeleteJobError, wipe_delete_job
from app.services.search import global_search


def test_health_endpoint():
    assert app.title == "Telegram Downloader"
    assert health() == {"status": "ok"}


def test_security_headers_are_set():
    response = TestClient(app).get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"


def test_cross_origin_writes_are_blocked(monkeypatch):
    monkeypatch.setattr(settings, "web_password", None)
    response = TestClient(app).post(
        "/setup/login/cancel",
        headers={"host": "127.0.0.1:8000", "origin": "https://example.com"},
    )

    assert response.status_code == 403


def test_web_password_protects_non_health_routes(monkeypatch):
    monkeypatch.setattr(settings, "web_password", "secret")
    client = TestClient(app)
    token = base64.b64encode(b"user:secret").decode()

    assert client.get("/chats").status_code == 401
    assert client.get("/chats", headers={"authorization": f"Basic {token}"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_notifications_page_handles_redis_down(monkeypatch):
    monkeypatch.setattr(settings, "web_password", None)
    monkeypatch.setattr("app.main.Redis.from_url", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("redis down")))
    monkeypatch.setattr("app.services.tdl.TdlService.is_logged_in", lambda self: False)

    response = TestClient(app).get("/notifications?per_page=5")

    assert response.status_code == 200
    assert "Notificaciones" in response.text


def test_missing_page_renders_404_with_chats_link(monkeypatch):
    monkeypatch.setattr(settings, "web_password", None)

    response = TestClient(app).get("/ruta-vieja")

    assert response.status_code == 404
    assert "Ruta no encontrada" in response.text
    assert 'href="/chats"' in response.text


def test_start_local_services_does_nothing_when_ready(monkeypatch):
    monkeypatch.setattr("app.main.redis_ping", lambda: True)
    monkeypatch.setattr("app.main.worker_count", lambda: 1)

    assert start_local_services() == ["Redis ya estaba conectado.", "Worker ya estaba conectado."]


def test_start_local_services_does_not_start_remote_redis(monkeypatch):
    monkeypatch.setattr("app.main.redis_ping", lambda: False)
    monkeypatch.setattr("app.main.settings.redis_url", "redis://redis.example.com:6379/0")

    assert start_local_services() == ["Redis está configurado en remoto; inícialo fuera de la app."]


def test_paginate_chats_filters_by_id_and_username():
    chats = [
        {"id": "100", "title": "General", "username": "main"},
        {"id": "200", "title": "Videos", "username": "media"},
    ]

    page = paginate_chats(chats, q="media", page=1, per_page=10)

    assert page["total"] == 1
    assert page["items"][0]["id"] == "200"


def test_paginate_chats_filters_and_sorts_by_type():
    chats = [
        {"id": "300", "title": "Privado", "type": "private"},
        {"id": "100", "title": "Canal", "type": "channel"},
        {"id": "200", "title": "Grupo", "type": "group"},
    ]

    filtered = paginate_chats(chats, q="", page=1, per_page=10, chat_type="group", sort="type")
    sorted_page = paginate_chats(chats, q="", page=1, per_page=10, sort="type")

    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == "200"
    assert [item["id"] for item in sorted_page["items"]] == ["100", "200", "300"]


def test_paginate_chats_can_keep_json_order():
    chats = [
        {"id": "300", "title": "C", "type": "private"},
        {"id": "100", "title": "A", "type": "channel"},
        {"id": "200", "title": "B", "type": "group"},
    ]

    page = paginate_chats(chats, q="", page=1, per_page=10, sort="json")

    assert [item["id"] for item in page["items"]] == ["300", "100", "200"]


def test_paginate_downloads_filters_sorts_and_pages():
    files = [
        {"relative_path": "b/video.mp4", "kind": "video", "size": 300, "modified_at": 2},
        {"relative_path": "a/photo.jpg", "kind": "image", "size": 100, "modified_at": 3},
        {"relative_path": "c/video-small.mp4", "kind": "video", "size": 50, "modified_at": 1},
    ]

    page = paginate_downloads(files, q="video", kind="video", sort="size-desc", page=1, per_page=12)

    assert page["total"] == 2
    assert [item["relative_path"] for item in page["items"]] == ["b/video.mp4", "c/video-small.mp4"]
    assert page["page_count"] == 1


def test_apply_template_prefill_overrides_defined_values_only():
    prefill = {
        "chat_id": "100",
        "chat_title": "Original",
        "hashtag": "",
        "media_type": "all",
        "search_text": "",
        "date_from": "",
        "date_to": "",
        "skip_same": True,
        "output_subfolder": "download",
        "export_exists": False,
        "export_path": "",
        "refresh_export": False,
        "template_id": "",
    }
    template = DownloadTemplate(
        id=7,
        name="Videos",
        media_type=MediaType.video,
        output_subfolder="videos",
        skip_same=False,
        refresh_export=True,
    )

    result = apply_template_prefill(prefill, template)

    assert result["chat_id"] == "100"
    assert result["media_type"] == "video"
    assert result["output_subfolder"] == "videos"
    assert result["skip_same"] is False
    assert result["template_id"] == 7


def test_chats_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_cache.settings, "base_dir", tmp_path)
    chats = [{"id": "100", "title": "General"}]

    updated_at = chat_cache.write_chats_cache(chats)
    cached_chats, cached_at = chat_cache.read_chats_cache()

    assert cached_chats == chats
    assert cached_at == updated_at


def test_friendly_error_detects_common_cases():
    assert friendly_error("Error 61 connecting to 127.0.0.1:6379")["title"] == "Redis apagado o inaccesible"
    assert friendly_error("Filtered messages: 0")["title"] == "No hay mensajes con esos filtros"
    assert friendly_error("json decode error")["title"] == "Export corrupto o inválido"


def test_global_search_empty_query_returns_empty_groups():
    assert global_search(None, "   ") == {"chats": [], "jobs": [], "files": []}


def test_wipe_delete_job_uses_wipe_before_db_delete(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    logs = tmp_path / "logs"
    job_dir = downloads / "chat" / "job"
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "downloads_dir", downloads)
    monkeypatch.setattr(settings, "logs_dir", logs)
    monkeypatch.setattr("app.services.jobs.shutil.which", lambda name: f"/usr/bin/{name}")
    calls = []

    class FakeProcess:
        stdout = ["wipe file1\n", "wipe file2\n"]

        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr("app.services.jobs.subprocess.Popen", fake_popen)

    class FakeDb:
        deleted = False
        committed = False

        def delete(self, job):
            self.deleted = True

        def commit(self):
            self.committed = True

    job = SimpleNamespace(id=7, download_path=str(job_dir))
    db = FakeDb()

    wipe_delete_job(db, job)

    assert calls[0][0] == ["wipe", "-rfiq", str(job_dir.resolve())]
    assert db.deleted is True
    assert db.committed is True
    assert "wipe" in (logs / "job-7.log").read_text()
    assert "wipe file1" in (logs / "job-7.log").read_text()
    assert "Job #7: eliminado con wipe" in (logs / "deleted-jobs.log").read_text()


def test_wipe_delete_job_keeps_db_when_wipe_fails(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    job_dir = downloads / "chat" / "job"
    job_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "downloads_dir", downloads)
    monkeypatch.setattr(settings, "logs_dir", tmp_path / "logs")
    monkeypatch.setattr("app.services.jobs.shutil.which", lambda name: f"/usr/bin/{name}")

    class FailedProcess:
        stdout = ["denied\n"]

        def wait(self):
            return 1

    monkeypatch.setattr("app.services.jobs.subprocess.Popen", lambda *args, **kwargs: FailedProcess())
    db = SimpleNamespace(delete=lambda job: pytest.fail("db.delete should not run"), commit=lambda: pytest.fail("commit should not run"))
    job = SimpleNamespace(id=8, download_path=str(job_dir))

    with pytest.raises(DeleteJobError, match="denied"):
        wipe_delete_job(db, job)
    assert "Job #8: falló eliminación segura" in (tmp_path / "logs" / "deleted-jobs.log").read_text()


def test_wipe_delete_job_deletes_db_when_folder_is_missing(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    logs = tmp_path / "logs"
    missing_dir = downloads / "chat" / "missing"
    monkeypatch.setattr(settings, "downloads_dir", downloads)
    monkeypatch.setattr(settings, "logs_dir", logs)
    monkeypatch.setattr("app.services.jobs.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("app.services.jobs.subprocess.Popen", lambda *args, **kwargs: pytest.fail("wipe should not run"))

    class FakeDb:
        deleted = False
        committed = False

        def delete(self, job):
            self.deleted = True

        def commit(self):
            self.committed = True

    db = FakeDb()
    job = SimpleNamespace(id=10, download_path=str(missing_dir))

    wipe_delete_job(db, job)

    assert db.deleted is True
    assert db.committed is True
    assert "carpeta inexistente" in (logs / "deleted-jobs.log").read_text()


def test_job_delete_returns_before_wipe(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "logs_dir", tmp_path / "logs")
    job = SimpleNamespace(id=9, status=JobStatus.completed, stage=JobStage.completed, error_message=None)

    class FakeDb:
        committed = False

        def get(self, model, job_id):
            return job

        def commit(self):
            self.committed = True

    class FakeBackground:
        tasks = []

        def add_task(self, fn, *args):
            self.tasks.append((fn, args))

    background = FakeBackground()
    response = job_delete(9, background, FakeDb())

    assert response.status_code == 303
    assert response.headers["location"] == "/jobs/9"
    assert background.tasks[0][1] == (9,)
    assert "Eliminación segura en progreso" in job.error_message
