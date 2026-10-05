from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobStage(str, enum.Enum):
    pending = "pending"
    exporting = "exporting"
    filtering = "filtering"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class MediaType(str, enum.Enum):
    all = "all"
    video = "video"
    image = "image"
    audio = "audio"
    document = "document"


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    files: Mapped[list[DownloadFile]] = relationship(cascade="all, delete-orphan")
    rq_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rq_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashtag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType), default=MediaType.all, nullable=False)
    search_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_from = mapped_column(Date, nullable=True)
    date_to = mapped_column(Date, nullable=True)
    skip_same: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    refresh_export: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    export_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    output_subfolder: Mapped[str] = mapped_column(String(128), nullable=False)
    export_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    filtered_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[JobStage] = mapped_column(Enum(JobStage), default=JobStage.pending, nullable=False)
    total_filtered_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_downloaded_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_observed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_observed_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_speed_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    transfer_initialized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_failed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    export_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    export_upper_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DownloadFile(Base):
    __tablename__ = "download_files"
    __table_args__ = (UniqueConstraint("job_id", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("download_jobs.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64))
    file_name: Mapped[str] = mapped_column(Text)
    message_json: Mapped[str] = mapped_column(Text)
    destination_root: Mapped[str] = mapped_column(Text)
    output_dir: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DownloadTemplate(Base):
    __tablename__ = "download_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashtag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType), default=MediaType.all, nullable=False)
    search_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_from = mapped_column(Date, nullable=True)
    date_to = mapped_column(Date, nullable=True)
    skip_same: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    refresh_export: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    export_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    output_subfolder: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
