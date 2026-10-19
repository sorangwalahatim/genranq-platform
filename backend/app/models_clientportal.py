"""Data model for the Client Portal (client-facing dashboard, messages, internal space).

Every table is tenant-owned (organisation_id → RLS) and project-scoped, so a
client can only ever see rows of projects they are assigned to.

  * portal_settings      per-project rights for one client user (view_reports,
                         view_tasks, comment, approve_content)
  * portal_message       client <-> agency thread (is_internal=false) and
                         team-only notes (is_internal=true, never shown to clients)
  * dashboard_delivery   log of dashboard email shares (log only, no sending here)
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import date, datetime  # noqa: F401

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401


class PortalSettings(Base):
    """Which portal features one client user may use on one project."""
    __tablename__ = "portal_settings"
    __table_args__ = (UniqueConstraint("project_id", "client_user_id", name="uq_portal_settings_project_user"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    client_user_id: Mapped[uuid.UUID] = fk("user.id", ondelete="CASCADE")
    rights: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{\"view_reports\": true, \"view_tasks\": true, \"comment\": true, \"approve_content\": false}'::jsonb"))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PortalMessage(Base):
    """One message in a project's portal thread.

    is_internal=false → visible to the client (client <-> agency thread).
    is_internal=true  → team only, NEVER returned by client endpoints.
    """
    __tablename__ = "portal_message"
    __table_args__ = (Index("ix_portal_message_proj", "project_id", "is_internal", "created_at"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    author_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_at: Mapped[datetime] = created()


class DashboardDelivery(Base):
    """Log of dashboard shares emailed to the client (record only)."""
    __tablename__ = "dashboard_delivery"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    sent_to: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str | None] = mapped_column(Text)  # e.g. last_30_days
    sent_at: Mapped[datetime] = created()
