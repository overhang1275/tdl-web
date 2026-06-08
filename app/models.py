from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobStage(str, enum.Enum):
    pending = "pending"
    exporting = "exporting"
    filtering = "filtering"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"


class MediaType(str, enum.Enum):
    all = "all"
    video = "video"
    image = "image"
    audio = "audio"
    document = "document"


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    rq_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashtag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType), default=MediaType.all, nullable=False)
    search_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_from = mapped_column(Date, nullable=True)
    date_to = mapped_column(Date, nullable=True)
    skip_same: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    output_subfolder: Mapped[str] = mapped_column(String(128), nullable=False)
    export_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    filtered_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[JobStage] = mapped_column(Enum(JobStage), default=JobStage.pending, nullable=False)
    total_filtered_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_downloaded_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

