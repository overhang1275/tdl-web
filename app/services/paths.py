from __future__ import annotations

import re
from pathlib import Path


SAFE_SUBFOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def sanitize_subfolder(value: str) -> str:
    value = value.strip().replace(" ", "_")
    if not SAFE_SUBFOLDER_RE.fullmatch(value):
        raise ValueError("Use only letters, numbers, dot, underscore and hyphen")
    if value in {".", ".."}:
        raise ValueError("Invalid folder name")
    return value


def safe_child(base: Path, *parts: str) -> Path:
    base = base.resolve()
    candidate = base.joinpath(*parts).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("Path traversal blocked")
    return candidate

