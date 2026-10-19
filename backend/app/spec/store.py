"""Active product specification (loaded from the Build Plan workbook)."""
from __future__ import annotations

import threading

from sqlalchemy import select

from .. import models as m
from ..db import system_session
from .parser import parse_workbook

_lock = threading.Lock()
_cache: dict = {}


def active_snapshot() -> m.SpecSnapshot | None:
    with system_session() as db:
        return db.scalar(select(m.SpecSnapshot).where(m.SpecSnapshot.status == "active")
                         .order_by(m.SpecSnapshot.created_at.desc()).limit(1))


def active_spec() -> dict:
    with _lock:
        if "spec" in _cache:
            return _cache["spec"]
    snap = active_snapshot()
    spec = snap.spec if snap else parse_workbook()
    with _lock:
        _cache["spec"] = spec
    return spec


def invalidate():
    with _lock:
        _cache.clear()
