from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
import mimetypes
from pathlib import Path
import shutil

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from rq import Worker
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, init_db
from app.models import DownloadJob
from app.schemas import JobCreate, JobRead
from app.services.files import downloaded_file_path, file_kind, human_size, job_download_root, list_downloaded_files
from app.services.interactive_login import interactive_login_service
from app.services.jobs import QueueUnavailableError, create_job, list_jobs, list_jobs_for_chat
from app.services.logs import read_job_log
from app.services.paths import chat_path_key, safe_child
from app.services.session import SessionService
from app.services.tdl import TdlError, TdlService


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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
        session_active = SessionService(tdl).is_logged_in()
        session_error = None
    except Exception as exc:
        session_active = False
        session_error = str(exc)
    try:
        redis = Redis.from_url(settings.redis_url)
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
    return {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "output_subfolder": subfolder or "download",
        "export_exists": export_exists,
        "export_path": str(export_path) if export_path else "",
        "refresh_export": refresh_export,
    }


def get_job_or_404(db: Session, job_id: int) -> DownloadJob:
    job = db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


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
        context={"logged_in": SessionService().is_logged_in(), "jobs": jobs},
    )


@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    return templates.TemplateResponse(request=request, name="setup.html", context=setup_context(request))


@app.get("/setup/login", response_class=HTMLResponse)
def setup_login_get() -> RedirectResponse:
    return RedirectResponse(url="/setup", status_code=303)


def login_session_payload() -> dict:
    session = interactive_login_service.status()
    return {
        "running": session.running,
        "started_at": session.started_at,
        "finished_at": session.finished_at,
        "returncode": session.returncode,
        "error": session.error,
        "output": "\n".join(session.output[-300:]),
    }


@app.post("/api/session/login/start")
def api_login_start():
    try:
        interactive_login_service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return login_session_payload()


@app.post("/api/session/login/input")
def api_login_input(value: str = Form(...)):
    try:
        interactive_login_service.send(value)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return login_session_payload()


@app.get("/api/session/login/status")
def api_login_status():
    return login_session_payload()


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


@app.post("/api/session/login")
def api_session_login(
    phone: str | None = Form(default=None),
    code: str | None = Form(default=None),
    password: str | None = Form(default=None),
):
    try:
        output = SessionService().login(phone=phone, code=code, password=password)
        return {"ok": True, "output": output}
    except TdlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/setup/login", response_class=HTMLResponse)
def setup_login(
    request: Request,
    phone: str | None = Form(default=None),
    code: str | None = Form(default=None),
    password: str | None = Form(default=None),
):
    try:
        message = SessionService().login(phone=phone, code=code, password=password) or "Login command completed."
    except TdlError as exc:
        message = f"tdl login requires CLI setup: {exc}"
    return templates.TemplateResponse(request=request, name="setup.html", context=setup_context(request, message))


@app.get("/api/session/status")
def api_session_status() -> dict[str, bool]:
    return {"logged_in": SessionService().is_logged_in()}


@app.get("/api/setup/status")
def api_setup_status():
    return system_status()


@app.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request):
    return templates.TemplateResponse(request=request, name="chats.html")


@app.get("/api/chats")
def api_chats():
    try:
        return {"chats": TdlService().list_chats()}
    except TdlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def chat_search_text(chat: dict) -> str:
    values = [
        chat.get("id"),
        chat.get("title"),
        chat.get("visible_name"),
        chat.get("username"),
        chat.get("type"),
    ]
    return " ".join(str(value).lower() for value in values if value)


def paginate_chats(chats: list[dict], q: str, page: int, per_page: int) -> dict:
    per_page_options = [10, 25, 50, 100]
    per_page = per_page if per_page in per_page_options else 25
    q = q.strip()
    filtered = [chat for chat in chats if q.lower() in chat_search_text(chat)] if q else chats
    total = len(filtered)
    page_count = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 1), page_count)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": filtered[start:end],
        "q": q,
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
def chats_list(request: Request, q: str = "", page: int = 1, per_page: int = 25):
    try:
        chats = TdlService().list_chats()
        error = None
    except TdlError as exc:
        chats = []
        error = str(exc)
    pagination = paginate_chats(chats, q=q, page=page, per_page=per_page)
    return templates.TemplateResponse(
        request=request,
        name="partials/chats_table.html",
        context={"chats": pagination["items"], "error": error, "pagination": pagination},
    )


@app.get("/chats/{chat_id}", response_class=HTMLResponse)
def chat_profile(request: Request, chat_id: str, db: Session = Depends(get_db)):
    jobs = list_jobs_for_chat(db, chat_id)
    chat_title = jobs[0].chat_title if jobs and jobs[0].chat_title else ""
    chat = None
    try:
        for candidate in TdlService().list_chats():
            if str(candidate.get("id")) == chat_id:
                chat = candidate
                chat_title = candidate.get("title") or candidate.get("visible_name") or chat_title
                break
    except TdlError:
        chat = None
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
        },
    )


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={"jobs": list_jobs(db), "error": None, "prefill": job_prefill_from_request(request)},
    )


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
    output_subfolder: str = Form(...),
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
            output_subfolder=output_subfolder,
        )
        job = create_job(db, payload)
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
    except QueueUnavailableError as exc:
        return templates.TemplateResponse(
            request=request,
            name="jobs.html",
            context={
                "jobs": list_jobs(db),
                "error": f"{exc}. Instala/inicia Redis y vuelve a intentar.",
                "prefill": {
                    "chat_id": chat_id,
                    "chat_title": chat_title or "",
                    "output_subfolder": output_subfolder,
                    "export_exists": False,
                    "export_path": "",
                    "refresh_export": refresh_export,
                },
            },
            status_code=503,
        )
    except (ValidationError, ValueError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="jobs.html",
            context={
                "jobs": list_jobs(db),
                "error": str(exc),
                "prefill": {
                    "chat_id": chat_id,
                    "chat_title": chat_title or "",
                    "output_subfolder": output_subfolder,
                    "export_exists": False,
                    "export_path": "",
                    "refresh_export": refresh_export,
                },
            },
            status_code=400,
        )


@app.post("/api/jobs", response_model=JobRead)
def api_create_job(payload: JobCreate, db: Session = Depends(get_db)):
    try:
        return create_job(db, payload)
    except QueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/jobs", response_model=list[JobRead])
def api_list_jobs(db: Session = Depends(get_db)):
    return list_jobs(db)


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


@app.get("/api/jobs/{job_id}", response_model=JobRead)
def api_get_job(job_id: int, db: Session = Depends(get_db)):
    return get_job_or_404(db, job_id)


@app.get("/api/jobs/{job_id}/logs", response_class=PlainTextResponse)
def api_job_logs(job_id: int, db: Session = Depends(get_db)):
    get_job_or_404(db, job_id)
    return read_job_log(job_id)


@app.get("/api/jobs/{job_id}/files")
def api_job_files(job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    root = job_download_root(job.id, job.download_path)
    return {"files": list_downloaded_files(root)}


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    return templates.TemplateResponse(request=request, name="job_detail.html", context={"job": job})


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_partial(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    return templates.TemplateResponse(request=request, name="partials/job_status.html", context={"job": job})


@app.get("/jobs/{job_id}/logs", response_class=HTMLResponse)
def job_logs_partial(request: Request, job_id: int, db: Session = Depends(get_db)):
    get_job_or_404(db, job_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/job_logs.html",
        context={"logs": read_job_log(job_id)},
    )


@app.get("/downloads/{job_id}", response_class=HTMLResponse)
def downloads_page(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    root = job_download_root(job.id, job.download_path)
    return templates.TemplateResponse(
        request=request,
        name="downloads.html",
        context={"job": job, "files": list_downloaded_files(root), "download_root": root},
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
