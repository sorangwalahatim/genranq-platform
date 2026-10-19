"""Checklists & redirects module tables (project-wise, tenant-owned).

  * checklist        one row per kind (pre_launch / post_launch / tech_audit) per project
  * checklist_item   individual checks with status, owner role, evidence
  * redirect_rule    301 redirect plan rows (old_url -> new_url)

Every table carries organisation_id (RLS) and project_id (project scoping).
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import datetime  # noqa: F401

from sqlalchemy import Integer, Text, UniqueConstraint, text  # noqa: F401
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

CHECKLIST_KINDS = ["pre_launch", "post_launch", "tech_audit"]
KIND_LABELS = {"pre_launch": "Pre-Launch", "post_launch": "Post-Launch", "tech_audit": "Tech Audit"}
ITEM_STATUSES = ["pending", "pass", "fail", "partial"]
STATUS_LABELS = {"pending": "Not checked", "pass": "Pass", "fail": "Fail", "partial": "Partial"}
REDIRECT_STATUSES = ["pending", "done", "error"]


class Checklist(Base):
    __tablename__ = "checklist"
    __table_args__ = (UniqueConstraint("project_id", "kind", name="uq_checklist_project_kind"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # pre_launch / post_launch / tech_audit
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class ChecklistItem(Base):
    __tablename__ = "checklist_item"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    checklist_id: Mapped[uuid.UUID] = fk("checklist.id")
    project_id: Mapped[uuid.UUID] = fk("project.id")  # denormalised for project scoping
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    owner_role: Mapped[str | None] = mapped_column(Text)  # Developer / Content Writer / SEO Lead / ...
    priority: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending/pass/fail/partial
    evidence: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    task_id: Mapped[uuid.UUID | None] = fk("task.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


class RedirectRule(Base):
    __tablename__ = "redirect_rule"
    __table_args__ = (UniqueConstraint("project_id", "old_url", name="uq_redirect_project_old"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    old_url: Mapped[str] = mapped_column(Text, nullable=False)
    new_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending/done/error
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()
