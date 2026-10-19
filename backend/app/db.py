"""Database access with tenant isolation.

``tenant_session(org_id)`` opens a transaction on the RLS-restricted app role and sets
``app.org_id`` for that transaction only. Every tenant table has a policy
``organisation_id = current_setting('app.org_id')::uuid`` so a query cannot see
another organisation's rows even if application code forgets a filter.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_s = get_settings()
app_engine = create_engine(_s.database_url_app, pool_pre_ping=True, pool_size=10, max_overflow=20)
system_engine = create_engine(_s.database_url_system, pool_pre_ping=True, pool_size=5, max_overflow=10)
AppSession = sessionmaker(app_engine, expire_on_commit=False)
SystemSession = sessionmaker(system_engine, expire_on_commit=False)


def set_tenant(db: Session, org_id: uuid.UUID | str | None, user_id=None, role: str | None = None):
    db.execute(text("select set_config('app.org_id', :o, true), set_config('app.user_id', :u, true), "
                    "set_config('app.role', :r, true)"),
               {"o": str(org_id) if org_id else "", "u": str(user_id) if user_id else "", "r": role or ""})


@contextmanager
def tenant_session(org_id, user_id=None, role=None) -> Iterator[Session]:
    db = AppSession()
    try:
        with db.begin():
            set_tenant(db, org_id, user_id, role)
            yield db
    finally:
        db.close()


@contextmanager
def system_session() -> Iterator[Session]:
    db = SystemSession()
    try:
        with db.begin():
            yield db
    finally:
        db.close()
