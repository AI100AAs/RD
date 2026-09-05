from __future__ import annotations

import re
import uuid


HISTORY_ID_RE = re.compile(r"^[a-f0-9-]{16,64}$")


def normalize_history_id(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value if HISTORY_ID_RE.fullmatch(value) else None


def new_history_id() -> str:
    return str(uuid.uuid4())
