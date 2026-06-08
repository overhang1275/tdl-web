from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MediaTypeLiteral = Literal["all", "video", "image", "audio", "document"]


class JobCreate(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)
    chat_title: str | None = Field(default=None, max_length=255)
    hashtag: str | None = Field(default=None, max_length=128)
    media_type: MediaTypeLiteral = "all"
    search_text: str | None = Field(default=None, max_length=255)
    date_from: date | None = None
    date_to: date | None = None
    skip_same: bool = True
    output_subfolder: str = Field(min_length=1, max_length=128)

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, value: str) -> str:
        value = value.strip()
        if not value.lstrip("-").isdigit() and not value.startswith("@"):
            raise ValueError("chat_id must be numeric or an @username")
        return value

    @field_validator("hashtag")
    @classmethod
    def normalize_hashtag(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip()
        if not value:
            return None
        return value if value.startswith("#") else f"#{value}"

    @field_validator("output_subfolder")
    @classmethod
    def validate_output_subfolder(cls, value: str) -> str:
        from app.services.paths import sanitize_subfolder

        return sanitize_subfolder(value)

    @model_validator(mode="after")
    def validate_date_range(self) -> "JobCreate":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before or equal to date_to")
        return self


class JobRead(BaseModel):
    id: int
    chat_id: str
    chat_title: str | None
    hashtag: str | None
    media_type: str
    search_text: str | None
    output_subfolder: str
    stage: str
    status: str
    total_filtered_messages: int
    total_downloaded_files: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}
