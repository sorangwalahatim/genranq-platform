"""Data model for the Unified Error Center.

One deduplicated inbox for errors from every audit source
(gsc / pagespeed / screaming_frog / ahrefs / semrush / audit).
Every table is tenant-owned (organisation_id -> RLS).
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import datetime  # noqa: F401

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

SOURCES = ["gsc", "pagespeed", "screaming_frog", "ahrefs", "semrush", "audit"]
SEVERITIES = ["critical", "warning", "info"]
STATUSES = ["open", "in_progress", "fixed", "verified", "ignored"]
BUCKETS = ["seo", "developer"]


class ErrorItem(Base):
    """One deduplicated error. fingerprint is unique per (project, fingerprint)
    so re-syncs bump times_seen instead of creating duplicates."""
    __tablename__ = "error_item"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_error_item_project_fp"),
        Index("ix_error_item_proj_status", "project_id", "status"),
        Index("ix_error_item_proj_bucket", "project_id", "bucket"),
    )
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    source: Mapped[str] = mapped_column(Text, nullable=False)  # gsc / pagespeed / screaming_frog / ahrefs / semrush / audit
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)  # sha256(source|category|normalised title)
    category: Mapped[str] = mapped_column(Text, nullable=False)  # broken_link / page_speed / missing_meta / ...
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="warning")  # critical / warning / info
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    urls: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # affected URLs
    solution: Mapped[str | None] = mapped_column(Text)  # plain-words step-by-step fix
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")  # open / in_progress / fixed / verified / ignored
    ignore_reason: Mapped[str | None] = mapped_column(Text)
    ignored_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # user id or null
    skill_key: Mapped[str | None] = mapped_column(Text)  # e.g. seo-technical-audit
    bucket: Mapped[str] = mapped_column(Text, nullable=False, server_default="seo")  # seo / developer
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
