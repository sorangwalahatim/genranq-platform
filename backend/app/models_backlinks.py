"""Data model additions for the backlinks modules (tables beyond the spec's Database Schema sheet).

The spec tables (supplier, backlink, backlink_check_history, backlink_file, credential) are used as-is;
columns the modules need beyond them live in 1:1 extension tables here, because spec tables are
never ALTERed. Every table is tenant-owned (organisation_id → RLS).

  * backlink_meta             1:1 with backlink — classification, placement date, re-check schedule
  * backlink_check_evidence   1:1 with backlink_check_history — what the verifier actually saw
  * supplier_profile          1:1 with supplier — contact person, website, terms, status
  * supplier_price            supplier price list rows (domain × service)
  * supplier_note             communication history
  * backlink_file_meta        1:1 with backlink_file — project attachment + note
  * backlink_toxic            imported toxic scores (Semrush Backlink Audit CSV), matched to backlinks when possible
  * disavow_list              disavow drafts → approval → generated Google disavow .txt
  * ext_backlink              provider backlink index rows (Ahrefs / Semrush API or CSV)
  * keyword_metric            keyword difficulty / volume / CPC from Ahrefs or Semrush
  * relevink_run              ReleVink placement-finder runs
  * page_comparison           page-to-page competitor comparison runs
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import date, datetime  # noqa: F401

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

LINK_TYPES = ["dofollow", "nofollow", "sponsored", "ugc"]
PAID = ["paid", "free"]
SCOPES = ["project", "client", "site"]
STATUSES = ["pending", "live", "removed", "changed", "noindex", "could_not_verify"]
FREQUENCIES = ["daily", "weekly", "monthly", "off"]
FILE_KINDS = ["invoice", "report", "screenshot", "contract", "other"]


class BacklinkMeta(Base):
    __tablename__ = "backlink_meta"
    backlink_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("backlink.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    link_scope: Mapped[str] = mapped_column(Text, server_default="project")  # project / client / site link
    placement_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    verify_frequency: Mapped[str] = mapped_column(Text, server_default="weekly")
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="manual")  # manual / csv / ahrefs / semrush
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class BacklinkCheckEvidence(Base):
    __tablename__ = "backlink_check_evidence"
    __table_args__ = (Index("ix_bl_evidence_bl", "backlink_id", "checked_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    history_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("backlink_check_history.id", ondelete="CASCADE"), nullable=False)
    backlink_id: Mapped[uuid.UUID] = fk("backlink.id", index=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    previous_status: Mapped[str | None] = mapped_column(Text)
    link_found: Mapped[bool | None] = mapped_column(Boolean)
    anchor_found: Mapped[str | None] = mapped_column(Text)
    anchor_matches: Mapped[bool | None] = mapped_column(Boolean)
    rel_found: Mapped[str | None] = mapped_column(Text)
    noindex: Mapped[bool | None] = mapped_column(Boolean)
    robots_blocked: Mapped[bool | None] = mapped_column(Boolean)
    final_url: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # snippet, redirect chain, timing


class SupplierProfile(Base):
    __tablename__ = "supplier_profile"
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    contact_name: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")  # active / paused / blacklisted
    tags: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SupplierPrice(Base):
    __tablename__ = "supplier_price"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    supplier_id: Mapped[uuid.UUID] = fk("supplier.id")
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(Text, server_default="guest_post")  # guest_post / link_insertion / niche_edit / other
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, server_default="USD")
    link_type: Mapped[str | None] = mapped_column(Text)
    dr: Mapped[float | None] = mapped_column(Numeric(6, 2))
    turnaround_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class SupplierNote(Base):
    __tablename__ = "supplier_note"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    supplier_id: Mapped[uuid.UUID] = fk("supplier.id")
    kind: Mapped[str] = mapped_column(Text, server_default="note")  # note / email / call / meeting / chat
    direction: Mapped[str | None] = mapped_column(Text)  # inbound / outbound
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class BacklinkFileMeta(Base):
    __tablename__ = "backlink_file_meta"
    backlink_file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("backlink_file.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class BacklinkToxic(Base):
    __tablename__ = "backlink_toxic"
    __table_args__ = (Index("ix_bl_toxic_proj", "project_id", "score"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    backlink_id: Mapped[uuid.UUID | None] = fk("backlink.id", nullable=True, ondelete="SET NULL")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    markers: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="semrush_csv")
    imported_at: Mapped[datetime] = created()


class DisavowList(Base):
    __tablename__ = "disavow_list"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="draft")  # draft / pending_approval / approved / rejected / generated
    entries: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # [{type: domain|url, value, reason, score}]
    approval_id: Mapped[uuid.UUID | None] = fk("approval.id", nullable=True, ondelete="SET NULL")
    stored_file_id: Mapped[uuid.UUID | None] = fk("stored_file.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtBacklink(Base):
    __tablename__ = "ext_backlink"
    __table_args__ = (Index("ix_ext_backlink_proj", "project_id", "provider"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # ahrefs / semrush
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[str | None] = mapped_column(Text)
    rel: Mapped[str | None] = mapped_column(Text)  # dofollow / nofollow / sponsored / ugc
    authority: Mapped[float | None] = mapped_column(Numeric(6, 2))  # Ahrefs DR of source / Semrush page AS
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    via: Mapped[str] = mapped_column(Text, server_default="csv")  # csv / api
    imported_at: Mapped[datetime] = created()


class KeywordMetric(Base):
    __tablename__ = "keyword_metric"
    __table_args__ = (UniqueConstraint("project_id", "keyword", "source", name="uq_keyword_metric"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[float | None] = mapped_column(Numeric(5, 1))
    volume: Mapped[int | None] = mapped_column(Integer)
    cpc: Mapped[float | None] = mapped_column(Numeric(10, 2))
    source: Mapped[str] = mapped_column(Text, nullable=False)  # ahrefs / semrush
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RelevinkRun(Base):
    __tablename__ = "relevink_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    anchor: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, server_default="both")  # internal / external / both
    candidates: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    include_db: Mapped[bool] = mapped_column(Boolean, server_default="false")
    use_ai: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    result: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PageComparison(Base):
    __tablename__ = "page_comparison"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    our_url: Mapped[str] = mapped_column(Text, nullable=False)
    competitor_urls: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    keyword: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    result: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
