"""Audit Skills module: per-project runnable audit skills with stored findings.

  * skill_run      one execution of an audit skill for a project (pagespeed,
                   screaming_frog, ahrefs, semrush, schema, crawler_files, responsive)
  * skill_finding  individual findings from a run: category, severity, url,
                   title, detail, recommendation, status
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import datetime  # noqa: F401

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

SKILL_KEYS = ["pagespeed", "screaming_frog", "ahrefs", "semrush", "schema", "crawler_files", "responsive"]
RUN_STATUSES = ["queued", "running", "completed", "failed"]
SEVERITIES = ["critical", "high", "medium", "low", "info"]
FINDING_STATUSES = ["open", "ignored", "fixed"]


class SkillRun(Base):
    __tablename__ = "audit_skill_run"
    __table_args__ = (Index("ix_audit_skill_run_proj", "project_id", "skill_key"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    skill_key: Mapped[str] = mapped_column(Text, nullable=False)  # one of SKILL_KEYS
    status: Mapped[str] = mapped_column(Text, server_default="completed")  # completed / failed
    cost_estimate: Mapped[str | None] = mapped_column(Text)  # plain words, e.g. "Free" / "~$0.02 per page"
    summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SkillFinding(Base):
    __tablename__ = "skill_finding"
    __table_args__ = (Index("ix_skill_finding_run", "run_id", "severity"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    run_id: Mapped[uuid.UUID] = fk("audit_skill_run.id")
    category: Mapped[str] = mapped_column(Text, nullable=False)  # speed / seo / developer / schema / crawler / responsive
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="medium")  # critical/high/medium/low/info
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # metrics, raw values
    status: Mapped[str] = mapped_column(Text, server_default="open")  # open / ignored / fixed
    created_at: Mapped[datetime] = created()
