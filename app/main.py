from __future__ import annotations

from contextlib import asynccontextmanager
import base64
from datetime import date, datetime
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from rq import Worker
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import get_db, init_db
from app.models import DownloadJob, DownloadTemplate, JobStage, JobStatus, MediaType
from app.schemas import JobCreate
from app.services.chat_cache import ChatsRefreshInProgress, delete_chats_cache, read_chats_cache, refresh_chats_cache
from app.services.errors import friendly_error
from app.services.files import count_downloaded_files, directory_size, downloaded_file_path, file_kind, human_duration, human_size, job_download_root, list_downloaded_files
from app.services.interactive_login import interactive_login_service
from app.services.jobs import QueueUnavailableError, cancel_job, create_job, find_duplicate_active_job, list_jobs, list_jobs_for_chat, queue_position, retry_job, wipe_delete_job_by_id
from app.services.logs import append_job_log, job_events, job_log_path, read_job_log
from app.services.paths import chat_path_key, safe_child, sanitize_subfolder
from app.services.search import global_search
from app.services.tdl import TdlError, TdlService


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None
templates.env.globals["human_size"] = human_size
templates.env.globals["human_duration"] = human_duration
templates.env.globals["friendly_error"] = friendly_error
app.mount("/static", StaticFiles(directory="app/static"), name="static")


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SERVICE_PROCESSES: list[subprocess.Popen] = []


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code != 404:
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
    if request.url.path.startswith("/api"):
        return PlainTextResponse("Not found", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={"missing_path": request.url.path},
        status_code=404,
    )


def basic_auth_ok(request: Request) -> bool:
    if not settings.web_password or request.url.path == "/health":
        return True
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return False
    _, _, password = decoded.partition(":")
    return secrets.compare_digest(password, settings.web_password)


def same_origin_request(request: Request) -> bool:
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.netloc and parsed.netloc != host:
            return False
    return True


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if not basic_auth_ok(request):
        return PlainTextResponse("Authentication required.", status_code=401, headers={"WWW-Authenticate": 'Basic realm="tdl-web"'})
    if request.method in UNSAFE_METHODS and not same_origin_request(request):
        return PlainTextResponse("Cross-origin writes are blocked.", status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


def setup_context(request: Request, message: str | None = None) -> dict:
    tdl = TdlService()
    status = system_status(tdl)
    return {
        "request": request,
        "logged_in": status["session_active"],
        "status": status,
        "message": message,
        "tdl_binary": tdl.binary,
        "tdl_storage": str(tdl.storage_path),
        "tdl_login_command": tdl.cli_login_command(),
        "login_session": interactive_login_service.status(),
    }


def system_status(tdl: TdlService | None = None) -> dict[str, object]:
    tdl = tdl or TdlService()
    login_session = interactive_login_service.status()
    tdl_installed = bool(shutil.which(tdl.binary) or ("/" in tdl.binary and Path(tdl.binary).exists()))
    try:
        session_active = tdl.is_logged_in()
        session_error = None
    except Exception as exc:
        session_active = False
        session_error = str(exc)
    try:
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        redis.ping()
        redis_connected = True
        redis_error = None
        workers = Worker.all(connection=redis)
        worker_count = len(workers)
        worker_connected = worker_count > 0
    except (RedisError, Exception) as exc:
        redis_connected = False
        redis_error = str(exc)
        worker_count = 0
        worker_connected = False
    last_login_error = login_session.error
    if not last_login_error and login_session.returncode not in (None, 0):
        last_login_error = f"tdl login terminó con código {login_session.returncode}"
    return {
        "session_active": session_active,
        "session_error": session_error,
        "tdl_installed": tdl_installed,
        "tdl_binary": tdl.binary,
        "redis_connected": redis_connected,
        "redis_error": redis_error,
        "worker_connected": worker_connected,
        "worker_count": worker_count,
        "last_login_error": last_login_error,
        "storage": str(tdl.storage_path),
        "redis_url": settings.redis_url,
    }


def redis_ping() -> bool:
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5).ping()
        return True
    except RedisError:
        return False


def worker_count() -> int:
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
    return len(Worker.all(connection=redis))


def wait_until(check, seconds: float = 4) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.25)
    return check()


def start_local_services() -> list[str]:
    messages: list[str] = []
    redis_url = urlparse(settings.redis_url)
    if not redis_ping():
        if redis_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            messages.append("Redis está configurado en remoto; inícialo fuera de la app.")
        elif not shutil.which("redis-server"):
            messages.append("redis-server no está instalado o no está en PATH.")
        else:
            subprocess.Popen(["redis-server", "--daemonize", "yes"])
            messages.append("Redis iniciado." if wait_until(redis_ping) else "Intenté iniciar Redis, pero aún no responde.")
    else:
        messages.append("Redis ya estaba conectado.")

    if redis_ping():
        try:
            if worker_count() > 0:
                messages.append("Worker ya estaba conectado.")
            else:
                settings.ensure_directories()
                log_path = settings.logs_dir / "worker-local.log"
                log = log_path.open("ab")
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.rq_worker"],
                    cwd=Path(__file__).resolve().parent.parent,
                    env=os.environ.copy(),
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                )
                SERVICE_PROCESSES.append(process)
                messages.append("Worker iniciado." if wait_until(lambda: worker_count() > 0) else "Worker lanzado; revisa logs si no aparece.")
        except Exception as exc:
            messages.append(f"No pude iniciar/verificar worker: {exc}")
    return messages


def job_prefill_from_request(request: Request) -> dict[str, object]:
    chat_id = request.query_params.get("chat_id", "").strip()
    chat_title = request.query_params.get("chat_title", "").strip()
    subfolder = request.query_params.get("output_subfolder", "").strip()
    if not subfolder and chat_title:
        try:
            from app.services.paths import sanitize_subfolder

            subfolder = sanitize_subfolder(chat_title[:80])
        except ValueError:
            subfolder = "download"
    export_path = None
    export_exists = False
    if chat_id:
        try:
            export_path = safe_child(settings.exports_dir, chat_path_key(chat_id), "export.json")
            export_exists = export_path.exists()
        except ValueError:
            export_path = None
    refresh_export_param = request.query_params.get("refresh_export")
    refresh_export = not export_exists
    if refresh_export_param is not None:
        refresh_export = refresh_export_param.lower() in {"1", "true", "yes", "on"}
    export_only_param = request.query_params.get("export_only")
    export_only = False
    if export_only_param is not None:
        export_only = export_only_param.lower() in {"1", "true", "yes", "on"}
    return {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "hashtag": "",
        "media_type": "all",
        "search_text": "",
        "date_from": "",
        "date_to": "",
        "skip_same": True,
        "output_subfolder": subfolder or "download",
        "export_exists": export_exists,
        "export_path": str(export_path) if export_path else "",
        "refresh_export": refresh_export,
        "export_only": export_only,
        "template_id": "",
    }


def apply_template_prefill(prefill: dict[str, object], template: DownloadTemplate | None) -> dict[str, object]:
    if template is None:
        return prefill
    values = dict(prefill)
    if template.chat_id:
        values["chat_id"] = template.chat_id
    if template.chat_title:
        values["chat_title"] = template.chat_title
    if template.hashtag:
        values["hashtag"] = template.hashtag
    values["media_type"] = template.media_type.value
    if template.search_text:
        values["search_text"] = template.search_text
    if template.date_from:
        values["date_from"] = template.date_from.isoformat()
    if template.date_to:
        values["date_to"] = template.date_to.isoformat()
    if template.output_subfolder:
        values["output_subfolder"] = template.output_subfolder
    values["skip_same"] = template.skip_same
    values["refresh_export"] = template.refresh_export
    values["export_only"] = template.export_only
    values["template_id"] = template.id
    refresh_prefill_export_info(values)
    return values


def refresh_prefill_export_info(prefill: dict[str, object]) -> None:
    chat_id = str(prefill.get("chat_id") or "").strip()
    export_path = None
    export_exists = False
    if chat_id:
        try:
            export_path = safe_child(settings.exports_dir, chat_path_key(chat_id), "export.json")
            export_exists = export_path.exists()
        except ValueError:
            export_path = None
    prefill["export_exists"] = export_exists
    prefill["export_path"] = str(export_path) if export_path else ""


def list_templates(db: Session) -> list[DownloadTemplate]:
    return list(db.scalars(select(DownloadTemplate).order_by(DownloadTemplate.created_at.desc())).all())


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def job_form_prefill(
    chat_id: str,
    chat_title: str | None,
    hashtag: str | None,
    media_type: str,
    search_text: str | None,
    date_from: date | None,
    date_to: date | None,
    skip_same: bool,
    refresh_export: bool,
    export_only: bool,
    output_subfolder: str | None,
) -> dict[str, object]:
    return {
        "chat_id": chat_id,
        "chat_title": chat_title or "",
        "hashtag": hashtag or "",
        "media_type": media_type,
        "search_text": search_text or "",
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "skip_same": skip_same,
        "export_only": export_only,
        "output_subfolder": output_subfolder or "download",
        "export_exists": False,
        "export_path": "",
        "refresh_export": refresh_export,
    }


def get_job_or_404(db: Session, job_id: int) -> DownloadJob:
    job = db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def dashboard_metrics(db: Session) -> dict[str, object]:
    active_count = db.scalar(select(func.count()).select_from(DownloadJob).where(DownloadJob.status.in_([JobStatus.pending, JobStatus.running]))) or 0
    failed_count = db.scalar(select(func.count()).select_from(DownloadJob).where(DownloadJob.status == JobStatus.failed)) or 0
    downloaded_files = db.scalar(select(func.coalesce(func.sum(DownloadJob.total_downloaded_files), 0))) or 0
    latest_errors = list(
        db.scalars(
            select(DownloadJob)
            .where(DownloadJob.status == JobStatus.failed)
            .order_by(DownloadJob.finished_at.desc().nullslast(), DownloadJob.created_at.desc())
            .limit(5)
        ).all()
    )
    disk_bytes = directory_size(settings.downloads_dir)
    cached_chats, cached_at = read_chats_cache()
    chat_counts = {"channels": 0, "groups": 0, "private": 0, "total": len(cached_chats), "cached_at": cached_at}
    for chat in cached_chats:
        chat_type = str(chat.get("type") or "").lower()
        if "channel" in chat_type:
            chat_counts["channels"] += 1
        elif "group" in chat_type:
            chat_counts["groups"] += 1
        elif "private" in chat_type or "user" in chat_type:
            chat_counts["private"] += 1
    return {
        "active_jobs": active_count,
        "failed_jobs": failed_count,
        "downloaded_files": downloaded_files,
        "disk_usage": human_size(disk_bytes),
        "latest_errors": latest_errors,
        "chat_counts": chat_counts,
    }


def remove_directory_contents(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for item in path.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


def reset_application_state(db: Session, keep_session: bool = True) -> dict[str, int | bool]:
    jobs = list(db.scalars(select(DownloadJob)).all())
    cancelled = 0
    for job in jobs:
        if job.status in {JobStatus.pending, JobStatus.running}:
            cancel_job(db, job)
            cancelled += 1
    try:
        from app.services.jobs import get_queue

        queue = get_queue()
        queue.empty()
        redis_cleared = True
    except Exception:
        redis_cleared = False
    deleted_jobs = db.scalar(select(func.count()).select_from(DownloadJob)) or 0
    deleted_templates = db.scalar(select(func.count()).select_from(DownloadTemplate)) or 0
    db.query(DownloadJob).delete(synchronize_session=False)
    db.query(DownloadTemplate).delete(synchronize_session=False)
    db.commit()
    remove_directory_contents(settings.exports_dir)
    remove_directory_contents(settings.downloads_dir)
    remove_directory_contents(settings.logs_dir)
    delete_chats_cache()
    if not keep_session:
        remove_directory_contents(settings.sessions_dir)
    settings.ensure_directories()
    return {
        "cancelled_jobs": cancelled,
        "deleted_jobs": deleted_jobs,
        "deleted_templates": deleted_templates,
        "redis_cleared": redis_cleared,
        "kept_session": keep_session,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    jobs = list_jobs(db)[:8]
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"logged_in": TdlService().is_logged_in(), "jobs": jobs, "metrics": dashboard_metrics(db)},
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    query = q.strip()
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={"q": query, "results": global_search(db, query)},
    )


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, page: int = 1, per_page: int = 15, db: Session = Depends(get_db)):
    per_page = per_page if per_page in (5, 15) else 15
    all_jobs = list_jobs(db)
    total = len(all_jobs)
    page_count = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), page_count)
    start = (page - 1) * per_page
    end = start + per_page
    jobs = all_jobs[start:end]
    status = system_status()
    pagination = {
        "page": page,
        "per_page": per_page,
        "page_count": page_count,
        "total": total,
        "start": start + 1 if total else 0,
        "end": min(end, total),
        "has_prev": page > 1,
        "has_next": page < page_count,
    }
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"jobs": jobs, "status": status, "pagination": pagination},
    )


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    return templates.TemplateResponse(request=request, name="setup.html", context=setup_context(request))


@app.post("/setup/reset", response_class=HTMLResponse)
def setup_reset(
    request: Request,
    confirmation: str = Form(""),
    keep_session: bool = Form(False),
    db: Session = Depends(get_db),
):
    if confirmation.strip() != "REINICIAR":
        context = setup_context(request, "Escribe REINICIAR para confirmar el reinicio total.")
        context["reset_error"] = True
        return templates.TemplateResponse(request=request, name="setup.html", context=context, status_code=400)
    result = reset_application_state(db, keep_session=keep_session)
    session_text = "Se conservó la sesión de Telegram." if result["kept_session"] else "También se borró la sesión de Telegram."
    redis_text = "La cola Redis se vació." if result["redis_cleared"] else "Redis no respondió; se limpió la base y archivos locales."
    message = (
        f"Reinicio completo: {result['deleted_jobs']} jobs borrados, "
        f"{result['deleted_templates']} plantillas borradas, "
        f"{result['cancelled_jobs']} jobs activos cancelados. {redis_text} {session_text}"
    )
    return templates.TemplateResponse(request=request, name="setup.html", context=setup_context(request, message))


@app.post("/setup/services/start", response_class=HTMLResponse)
def setup_services_start(request: Request):
    message = " ".join(start_local_services())
    return templates.TemplateResponse(request=request, name="setup.html", context=setup_context(request, message))


@app.get("/setup/login", response_class=HTMLResponse)
def setup_login_get() -> RedirectResponse:
    return RedirectResponse(url="/setup", status_code=303)


@app.post("/setup/login/start", response_class=HTMLResponse)
def setup_login_start(request: Request):
    try:
        interactive_login_service.start()
    except RuntimeError as exc:
        return templates.TemplateResponse(
            request=request,
            name="partials/login_console.html",
            context={"login_session": interactive_login_service.status(), "login_error": str(exc)},
            status_code=400,
        )
    return templates.TemplateResponse(
        request=request,
        name="partials/login_console.html",
        context={"login_session": interactive_login_service.status(), "login_error": None},
    )


@app.post("/setup/login/input", response_class=HTMLResponse)
def setup_login_input(request: Request, value: str = Form(...)):
    try:
        interactive_login_service.send(value)
        login_error = None
    except RuntimeError as exc:
        login_error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="partials/login_output.html",
        context={"login_session": interactive_login_service.status(), "login_error": login_error},
    )


@app.get("/setup/login/console", response_class=HTMLResponse)
def setup_login_console(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="partials/login_console.html",
        context={"login_session": interactive_login_service.status(), "login_error": None},
    )


@app.get("/setup/login/output", response_class=HTMLResponse)
def setup_login_output(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="partials/login_output.html",
        context={"login_session": interactive_login_service.status(), "login_error": None},
    )


@app.post("/setup/login/cancel", response_class=HTMLResponse)
def setup_login_cancel(request: Request):
    interactive_login_service.cancel()
    return templates.TemplateResponse(
        request=request,
        name="partials/login_console.html",
        context={"login_session": interactive_login_service.status(), "login_error": None},
    )


@app.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request):
    return templates.TemplateResponse(request=request, name="chats.html")


def chat_search_text(chat: dict) -> str:
    values = [
        chat.get("id"),
        chat.get("title"),
        chat.get("visible_name"),
        chat.get("username"),
        chat.get("type"),
    ]
    return " ".join(str(value).lower() for value in values if value)


def chat_type_group(chat: dict) -> str:
    chat_type = str(chat.get("type") or "").lower()
    if "channel" in chat_type:
        return "channel"
    if "group" in chat_type:
        return "group"
    if "private" in chat_type or "user" in chat_type:
        return "private"
    return "other"


def chat_display_name(chat: dict) -> str:
    return str(chat.get("title") or chat.get("visible_name") or chat.get("username") or chat.get("id") or "").lower()


def paginate_chats(chats: list[dict], q: str, page: int, per_page: int, chat_type: str = "", sort: str = "json") -> dict:
    per_page_options = [10, 25, 50, 100]
    per_page = per_page if per_page in per_page_options else 25
    chat_type = chat_type if chat_type in {"", "channel", "group", "private"} else ""
    sort = sort if sort in {"json", "name", "type", "id"} else "json"
    q = q.strip()
    search_filtered = [chat for chat in chats if q.lower() in chat_search_text(chat)] if q else chats
    type_counts = {"channel": 0, "group": 0, "private": 0, "other": 0}
    for chat in search_filtered:
        group = chat_type_group(chat)
        type_counts[group] = type_counts.get(group, 0) + 1
    filtered = search_filtered
    if chat_type:
        filtered = [chat for chat in filtered if chat_type_group(chat) == chat_type]
    if sort == "type":
        type_order = {"channel": 0, "group": 1, "private": 2, "other": 3}
        filtered = sorted(filtered, key=lambda chat: (type_order.get(chat_type_group(chat), 9), chat_display_name(chat)))
    elif sort == "id":
        filtered = sorted(filtered, key=lambda chat: str(chat.get("id") or ""))
    elif sort == "name":
        filtered = sorted(filtered, key=chat_display_name)
    total = len(filtered)
    page_count = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), page_count)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": filtered[start:end],
        "q": q,
        "chat_type": chat_type,
        "sort": sort,
        "type_counts": type_counts,
        "page": page,
        "per_page": per_page,
        "per_page_options": per_page_options,
        "total": total,
        "page_count": page_count,
        "start": start + 1 if total else 0,
        "end": min(end, total),
        "has_prev": page > 1,
        "has_next": page < page_count,
    }


def paginate_downloads(files: list[dict], q: str, kind: str, sort: str, page: int, per_page: int) -> dict:
    per_page_options = [12, 24, 48, 96]
    per_page = per_page if per_page in per_page_options else 24
    q = q.strip().lower()
    kind = kind if kind in {"", "image", "video", "audio", "pdf", "text", "file"} else ""
    sort = sort if sort in {"name", "date-desc", "size-desc", "size-asc"} else "name"
    filtered = files
    if q:
        filtered = [file for file in filtered if q in str(file["relative_path"]).lower()]
    if kind:
        filtered = [file for file in filtered if file["kind"] == kind]
    if sort == "date-desc":
        filtered = sorted(filtered, key=lambda file: (int(file["modified_at"]), str(file["relative_path"]).lower()), reverse=True)
    elif sort == "size-desc":
        filtered = sorted(filtered, key=lambda file: (int(file["size"]), str(file["relative_path"]).lower()), reverse=True)
    elif sort == "size-asc":
        filtered = sorted(filtered, key=lambda file: (int(file["size"]), str(file["relative_path"]).lower()))
    else:
        filtered = sorted(filtered, key=lambda file: str(file["relative_path"]).lower())
    total = len(filtered)
    page_count = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), page_count)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": filtered[start:end],
        "q": q,
        "kind": kind,
        "sort": sort,
        "page": page,
        "per_page": per_page,
        "per_page_options": per_page_options,
        "total": total,
        "page_count": page_count,
        "start": start + 1 if total else 0,
        "end": min(end, total),
        "has_prev": page > 1,
        "has_next": page < page_count,
    }


@app.get("/chats/list", response_class=HTMLResponse)
def chats_list(request: Request, q: str = "", page: int = 1, per_page: int = 25, refresh: bool = False, chat_type: str = "", sort: str = "json"):
    cached_at = None
    refreshed = False
    try:
        if refresh:
            chats, cached_at = refresh_chats_cache(wait=False)
            refreshed = True
        else:
            chats, cached_at = read_chats_cache()
            if not cached_at:
                chats, cached_at = refresh_chats_cache()
                refreshed = True
        error = None
    except TdlError as exc:
        chats, cached_at = read_chats_cache()
        error = str(exc)
    except ChatsRefreshInProgress as exc:
        chats, cached_at = read_chats_cache()
        error = str(exc)
    pagination = paginate_chats(chats, q=q, page=page, per_page=per_page, chat_type=chat_type, sort=sort)
    return templates.TemplateResponse(
        request=request,
        name="partials/chats_table.html",
        context={
            "chats": pagination["items"],
            "error": error,
            "pagination": pagination,
            "cached_at": cached_at,
            "refreshed": refreshed,
            "has_cache": cached_at is not None,
        },
    )


@app.get("/chats/{chat_id}", response_class=HTMLResponse)
def chat_profile(request: Request, chat_id: str, db: Session = Depends(get_db)):
    jobs = list_jobs_for_chat(db, chat_id)
    chat_title = jobs[0].chat_title if jobs and jobs[0].chat_title else ""
    chat = None
    cached_chats, _ = read_chats_cache()
    for candidate in cached_chats:
        if str(candidate.get("id")) == chat_id:
            chat = candidate
            chat_title = candidate.get("title") or candidate.get("visible_name") or chat_title
            break
    export_path = None
    export_exists = False
    export_updated_at = None
    try:
        export_path = safe_child(settings.exports_dir, chat_path_key(chat_id), "export.json")
        export_exists = export_path.exists()
        if export_exists:
            export_updated_at = datetime.fromtimestamp(export_path.stat().st_mtime)
    except ValueError:
        export_path = None
    try:
        downloads_root = safe_child(settings.downloads_dir, chat_path_key(chat_id))
    except ValueError:
        downloads_root = settings.downloads_dir / "__invalid__"
    downloaded_files = count_downloaded_files(downloads_root)
    downloaded_bytes = directory_size(downloads_root)
    return templates.TemplateResponse(
        request=request,
        name="chat_profile.html",
        context={
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat": chat,
            "jobs": jobs,
            "export_path": export_path,
            "export_exists": export_exists,
            "export_updated_at": export_updated_at,
            "downloads_root": downloads_root,
            "downloaded_files": downloaded_files,
            "downloaded_size": human_size(downloaded_bytes),
        },
    )


@app.post("/chats/{chat_id}/clean-downloads")
def chat_clean_downloads(chat_id: str):
    try:
        downloads_root = safe_child(settings.downloads_dir, chat_path_key(chat_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid chat id") from exc
    if downloads_root.exists():
        shutil.rmtree(downloads_root, ignore_errors=True)
    return RedirectResponse(url=f"/chats/{chat_id}", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, template_id: int | None = None, db: Session = Depends(get_db)):
    template = db.get(DownloadTemplate, template_id) if template_id else None
    prefill = apply_template_prefill(job_prefill_from_request(request), template)
    return templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "jobs": list_jobs(db),
            "templates": list_templates(db),
            "selected_template": template,
            "error": None,
            "template_error": None,
            "prefill": prefill,
        },
    )


@app.post("/templates")
def templates_create(
    request: Request,
    name: str = Form(...),
    chat_id: str | None = Form(default=None),
    chat_title: str | None = Form(default=None),
    hashtag: str | None = Form(default=None),
    media_type: str = Form("all"),
    search_text: str | None = Form(default=None),
    date_from: date | None = Form(default=None),
    date_to: date | None = Form(default=None),
    skip_same: bool = Form(False),
    refresh_export: bool = Form(False),
    export_only: bool = Form(False),
    output_subfolder: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    try:
        name = name.strip()
        if not name:
            raise ValueError("La plantilla necesita un nombre.")
        chat_id = normalize_optional_text(chat_id)
        if chat_id and not chat_id.lstrip("-").isdigit() and not chat_id.startswith("@"):
            raise ValueError("chat_id debe ser numérico o empezar con @.")
        hashtag = normalize_optional_text(hashtag)
        if hashtag and not hashtag.startswith("#"):
            hashtag = f"#{hashtag}"
        if date_from and date_to and date_from > date_to:
            raise ValueError("La fecha Desde debe ser menor o igual que Hasta.")
        output_subfolder = normalize_optional_text(output_subfolder)
        if output_subfolder:
            output_subfolder = sanitize_subfolder(output_subfolder)
        template = DownloadTemplate(
            name=name[:120],
            chat_id=chat_id,
            chat_title=normalize_optional_text(chat_title),
            hashtag=hashtag,
            media_type=MediaType(media_type),
            search_text=normalize_optional_text(search_text),
            date_from=date_from,
            date_to=date_to,
            skip_same=skip_same,
            refresh_export=refresh_export,
            export_only=export_only,
            output_subfolder=output_subfolder,
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        return RedirectResponse(url=f"/jobs?template_id={template.id}", status_code=303)
    except (ValueError, ValidationError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="jobs.html",
            context={
                "jobs": list_jobs(db),
                "templates": list_templates(db),
                "selected_template": None,
                "error": None,
                "template_error": str(exc),
                "prefill": {**job_form_prefill(chat_id or "", chat_title, hashtag, media_type, search_text, date_from, date_to, skip_same, refresh_export, export_only, output_subfolder), "template_id": ""},
            },
            status_code=400,
        )


@app.post("/templates/{template_id}/delete")
def templates_delete(template_id: int, db: Session = Depends(get_db)):
    template = db.get(DownloadTemplate, template_id)
    if template is not None:
        db.delete(template)
        db.commit()
    return RedirectResponse(url="/jobs", status_code=303)


@app.post("/jobs", response_class=HTMLResponse)
def jobs_create_form(
    request: Request,
    chat_id: str = Form(...),
    chat_title: str | None = Form(default=None),
    hashtag: str | None = Form(default=None),
    media_type: str = Form("all"),
    search_text: str | None = Form(default=None),
    date_from: date | None = Form(default=None),
    date_to: date | None = Form(default=None),
    skip_same: bool = Form(False),
    refresh_export: bool = Form(False),
    export_only: bool = Form(False),
    output_subfolder: str = Form(...),
    force: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        payload = JobCreate(
            chat_id=chat_id,
            chat_title=chat_title,
            hashtag=hashtag,
            media_type=media_type,
            search_text=search_text,
            date_from=date_from,
            date_to=date_to,
            skip_same=skip_same,
            refresh_export=refresh_export,
            export_only=export_only,
            output_subfolder=output_subfolder,
        )
        duplicate_job = find_duplicate_active_job(db, payload)
        if duplicate_job and not force:
            return templates.TemplateResponse(
                request=request,
                name="jobs.html",
                context={
                    "jobs": list_jobs(db),
                    "templates": list_templates(db),
                    "selected_template": None,
                    "error": None,
                    "template_error": None,
                    "duplicate_job": duplicate_job,
                    "duplicate_payload": payload,
                    "prefill": job_form_prefill(chat_id, chat_title, hashtag, media_type, search_text, date_from, date_to, skip_same, refresh_export, export_only, output_subfolder),
                },
                status_code=409,
            )
        job = create_job(db, payload)
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
    except QueueUnavailableError as exc:
        return templates.TemplateResponse(
            request=request,
            name="jobs.html",
            context={
                "jobs": list_jobs(db),
                "templates": list_templates(db),
                "selected_template": None,
                "error": f"{exc}. Instala/inicia Redis y vuelve a intentar.",
                "template_error": None,
                "prefill": job_form_prefill(chat_id, chat_title, hashtag, media_type, search_text, date_from, date_to, skip_same, refresh_export, export_only, output_subfolder),
            },
            status_code=503,
        )
    except (ValidationError, ValueError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="jobs.html",
            context={
                "jobs": list_jobs(db),
                "templates": list_templates(db),
                "selected_template": None,
                "error": str(exc),
                "template_error": None,
                "prefill": job_form_prefill(chat_id, chat_title, hashtag, media_type, search_text, date_from, date_to, skip_same, refresh_export, export_only, output_subfolder),
            },
            status_code=400,
        )


@app.get("/api/jobs/notifications")
def api_job_notifications(db: Session = Depends(get_db)):
    jobs = list_jobs(db)[:20]
    return {
        "jobs": [
            {
                "id": job.id,
                "chat_id": job.chat_id,
                "chat_title": job.chat_title,
                "status": job.status.value,
                "stage": job.stage.value,
                "error_message": job.error_message,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            }
            for job in jobs
        ]
    }


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    return templates.TemplateResponse(
        request=request,
        name="job_detail.html",
        context={"job": job, "queue_position": queue_position(job), "events": job_events(job.id)},
    )


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_partial(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/job_status.html",
        context={"job": job, "queue_position": queue_position(job)},
    )


@app.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    cancel_job(db, job)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/retry")
def job_retry(job_id: int, db: Session = Depends(get_db)):
    old_job = get_job_or_404(db, job_id)
    try:
        new_job = retry_job(db, old_job)
    except QueueUnavailableError as exc:
        old_job.error_message = f"{exc}. Instala/inicia Redis y vuelve a intentar."
        db.commit()
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
    return RedirectResponse(url=f"/jobs/{new_job.id}", status_code=303)


@app.post("/jobs/{job_id}/delete")
def job_delete(job_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    if job.status in {JobStatus.pending, JobStatus.running}:
        cancel_job(db, job)
    job.status = JobStatus.cancelled
    job.stage = JobStage.cancelled
    job.error_message = "Eliminación segura en progreso. La carpeta se está borrando con wipe."
    append_job_log(job.id, job.error_message)
    db.commit()
    background_tasks.add_task(wipe_delete_job_by_id, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}/logs", response_class=HTMLResponse)
def job_logs_partial(request: Request, job_id: int, db: Session = Depends(get_db)):
    get_job_or_404(db, job_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/job_logs.html",
        context={"logs": read_job_log(job_id)},
    )


@app.get("/jobs/{job_id}/events", response_class=HTMLResponse)
def job_events_partial(request: Request, job_id: int, db: Session = Depends(get_db)):
    get_job_or_404(db, job_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/job_events.html",
        context={"events": job_events(job_id)},
    )


@app.get("/downloads/{job_id}", response_class=HTMLResponse)
def downloads_page(
    request: Request,
    job_id: int,
    q: str = "",
    kind: str = "",
    sort: str = "name",
    page: int = 1,
    per_page: int = 24,
    db: Session = Depends(get_db),
):
    job = get_job_or_404(db, job_id)
    root = job_download_root(job.id, job.download_path)
    all_files = list_downloaded_files(root)
    pagination = paginate_downloads(all_files, q=q, kind=kind, sort=sort, page=page, per_page=per_page)
    return templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context={
            "job": job,
            "files": pagination["items"],
            "all_files_count": len(all_files),
            "pagination": pagination,
            "download_root": root,
        },
    )


@app.get("/downloads/{job_id}/file")
def download_file(job_id: int, path: str, download: bool = False, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    root = job_download_root(job.id, job.download_path)
    try:
        file_path = downloaded_file_path(root, path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=file_path.name if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/downloads/{job_id}/preview", response_class=HTMLResponse)
def preview_file(request: Request, job_id: int, path: str, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    root = job_download_root(job.id, job.download_path)
    try:
        file_path = downloaded_file_path(root, path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    size = file_path.stat().st_size
    file = {
        "name": file_path.name,
        "relative_path": path,
        "size": size,
        "human_size": human_size(size),
        "kind": file_kind(file_path),
    }
    return templates.TemplateResponse(
        request=request,
        name="download_preview.html",
        context={"job": job, "file": file},
    )
