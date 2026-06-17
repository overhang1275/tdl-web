from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.models import DownloadJob
from app.services.chat_cache import read_chats_cache
from app.services.files import job_download_root, list_downloaded_files


def global_search(db: Session, query: str, limit: int = 8) -> dict[str, list[dict]]:
    q = query.strip().lower()
    if not q:
        return {"chats": [], "jobs": [], "files": []}
    chats = search_chats(q, limit)
    jobs = search_jobs(db, q, limit)
    files = search_files(db, q, limit)
    return {"chats": chats, "jobs": jobs, "files": files}


def search_chats(q: str, limit: int) -> list[dict]:
    chats, _ = read_chats_cache()
    results = []
    for chat in chats:
        haystack = " ".join(
            str(chat.get(key) or "") for key in ("id", "title", "visible_name", "username", "type")
        ).lower()
        if q in haystack:
            title = chat.get("title") or chat.get("visible_name") or "Sin nombre"
            results.append(
                {
                    "id": chat.get("id"),
                    "title": title,
                    "subtitle": f"@{chat.get('username')}" if chat.get("username") else str(chat.get("type") or "chat"),
                    "url": f"/chats/{chat.get('id')}",
                }
            )
        if len(results) >= limit:
            break
    return results


def search_jobs(db: Session, q: str, limit: int) -> list[dict]:
    like = f"%{q}%"
    jobs = list(
        db.scalars(
            select(DownloadJob)
            .where(
                or_(
                    DownloadJob.chat_id.ilike(like),
                    DownloadJob.chat_title.ilike(like),
                    DownloadJob.hashtag.ilike(like),
                    DownloadJob.search_text.ilike(like),
                    DownloadJob.output_subfolder.ilike(like),
                    DownloadJob.error_message.ilike(like),
                )
            )
            .order_by(DownloadJob.created_at.desc())
            .limit(limit)
        ).all()
    )
    return [
        {
            "id": job.id,
            "title": f"Job #{job.id} · {job.chat_title or job.chat_id}",
            "subtitle": f"{job.status.value} · {job.media_type.value} · {job.total_downloaded_files} archivos",
            "url": f"/jobs/{job.id}",
        }
        for job in jobs
    ]


def search_files(db: Session, q: str, limit: int) -> list[dict]:
    results = []
    jobs = list(db.scalars(select(DownloadJob).order_by(DownloadJob.created_at.desc())).all())
    for job in jobs:
        root = job_download_root(job.id, job.download_path)
        for file in list_downloaded_files(root):
            relative = str(file["relative_path"])
            if q not in relative.lower() and q not in str(file["kind"]).lower():
                continue
            results.append(
                {
                    "job_id": job.id,
                    "title": file["name"],
                    "subtitle": f"{job.chat_title or job.chat_id} · {file['human_size']} · {file['kind']}",
                    "url": f"/downloads/{job.id}/preview?path={quote(relative)}",
                    "download_url": f"/downloads/{job.id}/file?path={quote(relative)}&download=true",
                }
            )
            if len(results) >= limit:
                return results
    return results
