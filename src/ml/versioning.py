"""Versioning and timestamp utilities for ML pipeline."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    """Return the current UTC time as an aware datetime."""
    return datetime.now(timezone.utc)


def stable_hash(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a JSON-serialisable dict (sorted keys)."""
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def model_version_tag(model_name: str, version: str) -> str:
    """Human-readable model version label."""
    return f"{model_name}:{version}"
