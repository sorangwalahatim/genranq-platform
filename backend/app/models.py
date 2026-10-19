"""Genranq database model.

Every table and column from the *Database Schema* sheet is represented here with
the sheet's entity and field names. Compound spec columns such as
``name/email/phone`` are split into one column per field.

Additions beyond the sheet are marked ``# [ASSUMPTION]`` and listed in
docs/ASSUMPTIONS.md. The main ones:
  * ``organisation_id`` is denormalised onto every tenant-owned table so that
    Postgres row-level security can enforce isolation with a single predicate.
  * Platform tables the spec references but does not define (task, time_entry,
    approval, job, notification, integration_connection, …).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def fk(target, nullable=False, ondelete="CASCADE", index=True):
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


def org_fk():
    # [ASSUMPTION] tenant key on every tenant-owned table (RLS predicate)
    return mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"),
                         nullable=False, index=True)


def created():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


J = JSONB


# =========================================================== tenancy & users
class Organisation(Base):
    __tablename__ = "organisation"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False, server_default="essentials")  # essentials/advanced/enterprise
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # [ASSUMPTION]
    branding: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # [ASSUMPTION] White-Label module 47
    addons: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # [ASSUMPTION] e.g. accounting
    created_at: Mapped[datetime] = created()


class User(Base):
    __tablename__ = "user"
    __table_args__ = (UniqueConstraint("organisation_id", "email", name="uq_user_org_email"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = fk("organisation.id")
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)  # unique per org
    role: Mapped[str] = mapped_column(Text, nullable=False)  # owner/admin/manager/staff/client
    name: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mfa_secret_ref: Mapped[str | None] = mapped_column(Text)  # vault ref, never plaintext
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, server_default="false")  # Genranq operator staff only
    failed_auth: Mapped[int] = mapped_column(Integer, server_default="0")  # brute-force lockout counter
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger)  # TOTP replay protection
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class RefreshToken(Base):  # [ASSUMPTION] F-002 refresh rotation
    __tablename__ = "refresh_token"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = fk("user.id")
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class OAuthState(Base):  # [ASSUMPTION] single-use OAuth state bound to the initiating browser
    __tablename__ = "oauth_state"
    nonce: Mapped[str] = mapped_column(Text, primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = fk("user.id")
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    browser_hash: Mapped[str] = mapped_column(Text, nullable=False)
    code_verifier: Mapped[str | None] = mapped_column(Text)  # PKCE
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invite(Base):  # [ASSUMPTION] Team module
    __tablename__ = "invite"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created()


# =========================================================== projects / work
class Project(Base):
    __tablename__ = "project"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = fk("organisation.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    client_name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    is_template: Mapped[bool] = mapped_column(Boolean, server_default="false")
    client_user_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")  # projects visible to Client role
    created_at: Mapped[datetime] = created()


class Task(Base):  # [ASSUMPTION] Tasks module 2 / seo_plan → task
    __tablename__ = "task"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="todo")  # todo/in_progress/review/done
    priority: Mapped[str] = mapped_column(Text, server_default="medium")  # low/medium/high/critical
    assignee_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    source: Mapped[str | None] = mapped_column(Text)  # e.g. seo_plan:<id> / monitor_issue:<id> / activity:<id>
    workstream: Mapped[str | None] = mapped_column(Text)  # on_page/technical/off_page/content/aio/aeo/geo/sxo/local/analytics/social
    assignee_type: Mapped[str] = mapped_column(Text, server_default="user")  # user / team / ai_agent
    agent_key: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # AI agent output attached to the task
    labels: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TimeEntry(Base):  # [ASSUMPTION] Time Tracking module 3
    __tablename__ = "time_entry"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = fk("user.id")
    project_id: Mapped[uuid.UUID] = fk("project.id")
    task_id: Mapped[uuid.UUID | None] = fk("task.id", nullable=True, ondelete="SET NULL")
    description: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null = running timer
    billable: Mapped[bool] = mapped_column(Boolean, server_default="true")
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = created()


class Approval(Base):  # [ASSUMPTION] Approvals module 45 / F-028
    __tablename__ = "approval"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)  # content_item/task/permission/credential_access
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending/approved/rejected
    chain: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # ordered roles required
    step: Mapped[int] = mapped_column(Integer, server_default="0")
    requested_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    decisions: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class Notification(Base):  # [ASSUMPTION] Notifications service
    __tablename__ = "notification"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True)  # null = all org users
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class ActivityLog(Base):
    __tablename__ = "activity_log"
    __table_args__ = (Index("ix_activity_org_created", "organisation_id", "created_at"),)
    id: Mapped[uuid.UUID] = pk()  # immutable audit
    organisation_id: Mapped[uuid.UUID] = org_fk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


# =========================================================== jobs (F-004)
class Job(Base):  # [ASSUMPTION] queue table (Postgres SKIP LOCKED queue)
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_claim", "status", "run_at"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED", nullable=False)  # QUEUED/RUNNING/COMPLETED/FAILED/DEAD
    payload: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    result: Mapped[dict | None] = mapped_column(J)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    progress: Mapped[int] = mapped_column(Integer, server_default="0")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class JobSchedule(Base):  # [ASSUMPTION] scheduler for SEO Automation (module 34) / report automation
    __tablename__ = "job_schedule"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    frequency: Mapped[str] = mapped_column(Text, nullable=False)  # daily/weekly/monthly
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class RateLimitBucket(Base):  # [ASSUMPTION] F-004 "rate-limit tokens" per provider per org
    __tablename__ = "rate_limit_bucket"
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    tokens: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    capacity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    refill_per_sec: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# =========================================================== SEO core
class Site(Base):
    __tablename__ = "site"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    gsc_property: Mapped[str | None] = mapped_column(Text)
    ga4_property: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class Keyword(Base):
    __tablename__ = "keyword"
    __table_args__ = (UniqueConstraint("project_id", "phrase", "country", "lang", name="uq_keyword_locale"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    site_id: Mapped[uuid.UUID | None] = fk("site.id", nullable=True, ondelete="SET NULL")
    phrase: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(Text, nullable=False, server_default="us")  # locale for SERP
    lang: Mapped[str] = mapped_column(Text, nullable=False, server_default="en")
    target_url: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    search_volume: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[int | None] = mapped_column(Integer)
    frequency: Mapped[str] = mapped_column(Text, server_default="weekly")  # daily/weekly/monthly
    created_at: Mapped[datetime] = created()


class RankHistory(Base):  # ClickHouse (time-series) in production; Postgres here
    __tablename__ = "rank_history"
    __table_args__ = (Index("ix_rank_kw_time", "keyword_id", "checked_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # [ASSUMPTION]
    organisation_id: Mapped[uuid.UUID] = org_fk()
    keyword_id: Mapped[uuid.UUID] = fk("keyword.id", index=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # partition key
    position: Mapped[int | None] = mapped_column(Integer)  # null = not ranking in checked depth
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # gsc/semrush/manual/import
    serp_features: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    competitor_positions: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))


class CrawlRun(Base):
    __tablename__ = "crawl_run"
    id: Mapped[uuid.UUID] = pk()  # scope/depth/robots
    organisation_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = fk("site.id")
    start_url: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, server_default="subdomain")
    max_depth: Mapped[int] = mapped_column(Integer, server_default="5")
    max_pages: Mapped[int] = mapped_column(Integer, server_default="250")
    respect_robots: Mapped[bool] = mapped_column(Boolean, server_default="true")
    engine: Mapped[str] = mapped_column(Text, server_default="builtin")  # builtin/screaming_frog
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    pages_crawled: Mapped[int] = mapped_column(Integer, server_default="0")
    site_info: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # robots, sitemaps, http/https probes
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class CrawlPage(Base):  # [ASSUMPTION] per-URL crawl output (link graph & on-page facts)
    __tablename__ = "crawl_page"
    __table_args__ = (UniqueConstraint("crawl_run_id", "url", name="uq_crawl_page_url"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    crawl_run_id: Mapped[uuid.UUID] = fk("crawl_run.id")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer, server_default="0")
    content_type: Mapped[str | None] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default="true")
    data: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # title, meta, h1s, links, …


class AuditChecklistItem(Base):  # global catalog seeded from Audit Checklist sheet
    __tablename__ = "audit_checklist_item"
    id: Mapped[uuid.UUID] = pk()  # catalog of checks
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # e.g. CHK-01
    category: Mapped[str] = mapped_column(Text, nullable=False)
    check: Mapped[str] = mapped_column(Text, nullable=False)
    data_source: Mapped[str] = mapped_column(Text, nullable=False)
    auto: Mapped[str] = mapped_column(Text, nullable=False)  # Y / P
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    default_owner: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class AuditRun(Base):
    __tablename__ = "audit_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = fk("site.id")
    crawl_run_id: Mapped[uuid.UUID | None] = fk("crawl_run.id", nullable=True, ondelete="SET NULL")
    competitor_id: Mapped[uuid.UUID | None] = fk("competitor.id", nullable=True, ondelete="SET NULL")
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))  # 0-100 weighted
    category_scores: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    summary: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    sources: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # which data sources were connected
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    max_pages: Mapped[int] = mapped_column(Integer, server_default="250")
    triggered_by: Mapped[str] = mapped_column(Text, server_default="manual")  # manual/schedule
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class AuditResult(Base):
    __tablename__ = "audit_result"
    id: Mapped[uuid.UUID] = pk()  # result per run
    organisation_id: Mapped[uuid.UUID] = org_fk()
    audit_run_id: Mapped[uuid.UUID] = fk("audit_run.id")
    checklist_item_id: Mapped[uuid.UUID] = fk("audit_checklist_item.id")
    grade: Mapped[str] = mapped_column(Text, nullable=False)  # pass/fail/partial/not_run/manual
    count: Mapped[int] = mapped_column(Integer, server_default="0")
    affected: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # affected URLs
    source_payload: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # raw evidence
    message: Mapped[str | None] = mapped_column(Text)


class AuditIssue(Base):
    __tablename__ = "audit_issue"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    audit_run_id: Mapped[uuid.UUID] = fk("audit_run.id")
    rule: Mapped[str] = mapped_column(Text, nullable=False)  # checklist code
    category: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    priority_score: Mapped[float] = mapped_column(Numeric(8, 3), server_default="0")  # + priority score
    title: Mapped[str] = mapped_column(Text, nullable=False)
    affected_count: Mapped[int] = mapped_column(Integer, server_default="0")
    affected_urls: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    evidence: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    recommendation: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)


class PagespeedResult(Base):  # time-series
    __tablename__ = "pagespeed_result"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = fk("site.id")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    device: Mapped[str] = mapped_column(Text, nullable=False)  # desktop/mobile
    lcp: Mapped[float | None] = mapped_column(Numeric(10, 3))  # seconds
    cls: Mapped[float | None] = mapped_column(Numeric(10, 4))
    inp: Mapped[float | None] = mapped_column(Numeric(10, 3))  # ms
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    audits: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    checked_at: Mapped[datetime] = created()


class GscIssue(Base):
    __tablename__ = "gsc_issue"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = fk("site.id")
    type: Mapped[str] = mapped_column(Text, nullable=False)  # 404/soft404/noindex/redirect/crawled-not-indexed…
    count: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="open")
    sample_urls: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    synced_at: Mapped[datetime] = created()


class Competitor(Base):
    __tablename__ = "competitor"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class SeoPlan(Base):
    __tablename__ = "seo_plan"
    id: Mapped[uuid.UUID] = pk()  # generated action plan
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    audit_run_id: Mapped[uuid.UUID] = fk("audit_run.id")
    items: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # prioritized tasks → task
    tasks_created: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = created()


# =========================================================== backlinks (Phase 2)
class Supplier(Base):
    __tablename__ = "supplier"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = fk("organisation.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    niche: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1))  # contact + rating
    price_list: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created()


class Backlink(Base):
    __tablename__ = "backlink"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    target_site_id: Mapped[uuid.UUID] = fk("site.id")
    supplier_id: Mapped[uuid.UUID | None] = fk("supplier.id", nullable=True, ondelete="SET NULL")  # nullable if free
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_text: Mapped[str | None] = mapped_column(Text)
    link_type: Mapped[str | None] = mapped_column(Text)  # dofollow/nofollow/sponsored/ugc
    paid_or_free: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # live/removed/changed/noindex/…
    cost: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    dr: Mapped[float | None] = mapped_column(Numeric(6, 2))
    da: Mapped[float | None] = mapped_column(Numeric(6, 2))
    authority: Mapped[float | None] = mapped_column(Numeric(6, 2))  # cached metrics
    toxicity_score: Mapped[float | None] = mapped_column(Numeric(5, 2))  # 0-100 advisory
    first_detected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BacklinkCheckHistory(Base):  # ClickHouse (time-series) in production
    __tablename__ = "backlink_check_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    backlink_id: Mapped[uuid.UUID] = fk("backlink.id")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    rel: Mapped[str | None] = mapped_column(Text)
    is_indexed: Mapped[bool | None] = mapped_column(Boolean)  # verification snapshot


class StoredFile(Base):  # [ASSUMPTION] file storage (quota: File Storage row of Pricing Matrix)
    __tablename__ = "stored_file"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, server_default="upload")  # upload/report
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class BacklinkFile(Base):
    __tablename__ = "backlink_file"
    id: Mapped[uuid.UUID] = pk()  # invoice/report/screenshot
    organisation_id: Mapped[uuid.UUID] = org_fk()
    backlink_id: Mapped[uuid.UUID | None] = fk("backlink.id", nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = fk("supplier.id", nullable=True)
    kind: Mapped[str] = mapped_column(Text, server_default="invoice")
    stored_file_id: Mapped[uuid.UUID] = fk("stored_file.id")


class Credential(Base):
    __tablename__ = "credential"
    id: Mapped[uuid.UUID] = pk()  # vault reference only
    organisation_id: Mapped[uuid.UUID] = org_fk()
    supplier_id: Mapped[uuid.UUID | None] = fk("supplier.id", nullable=True)
    site_id: Mapped[uuid.UUID | None] = fk("site.id", nullable=True)
    integration: Mapped[str | None] = mapped_column(Text)  # [ASSUMPTION] provider credentials also live here
    label: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    vault_ref: Mapped[str] = mapped_column(Text, nullable=False)  # pointer to KMS/Vault; NO plaintext
    masked_hint: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VaultSecret(Base):  # [ASSUMPTION] local envelope-encrypted Vault backend (separate schema)
    __tablename__ = "secret"
    __table_args__ = {"schema": "vault"}
    ref: Mapped[str] = mapped_column(Text, primary_key=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = created()


# =========================================================== integrations hub (module 49)
class IntegrationConnection(Base):  # [ASSUMPTION]
    __tablename__ = "integration_connection"
    __table_args__ = (UniqueConstraint("organisation_id", "provider_key", name="uq_integration_org_provider"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="NOT_CONNECTED")  # NOT_CONNECTED/CONNECTED/ERROR/RATE_LIMITED
    credential_id: Mapped[uuid.UUID | None] = fk("credential.id", nullable=True, ondelete="SET NULL")
    config: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    error_count: Mapped[int] = mapped_column(Integer, server_default="0")
    connected_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================== local SEO (Phase 3)
class GbpLocation(Base):
    __tablename__ = "gbp_location"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    place_id: Mapped[str | None] = mapped_column(Text)
    cid: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    nap: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))


class GbpReview(Base):
    __tablename__ = "gbp_review"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    location_id: Mapped[uuid.UUID] = fk("gbp_location.id")
    rating: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[float | None] = mapped_column(Numeric(4, 3))
    reply: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GbpPost(Base):
    __tablename__ = "gbp_post"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    location_id: Mapped[uuid.UUID] = fk("gbp_location.id")
    post_type: Mapped[str | None] = mapped_column(Text)  # offer/update/event
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GridScan(Base):
    __tablename__ = "grid_scan"
    id: Mapped[uuid.UUID] = pk()  # geo-grid run
    organisation_id: Mapped[uuid.UUID] = org_fk()
    keyword_id: Mapped[uuid.UUID] = fk("keyword.id")
    center_lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    center_lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    grid_size: Mapped[int] = mapped_column(Integer, server_default="7")
    spacing_km: Mapped[float] = mapped_column(Numeric(6, 2), server_default="1")
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    created_at: Mapped[datetime] = created()


class GridPoint(Base):
    __tablename__ = "grid_point"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    scan_id: Mapped[uuid.UUID] = fk("grid_scan.id")
    lat: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    lng: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)


# =========================================================== content / AI studio (Phase 4)
class ContentItem(Base):
    __tablename__ = "content_item"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    type: Mapped[str] = mapped_column(Text, nullable=False)  # blog/news/social/gmb/landing/…
    state: Mapped[str] = mapped_column(Text, server_default="draft")  # draft→generated→review→approve→publish
    channel: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class ContentVersion(Base):
    __tablename__ = "content_version"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    content_item_id: Mapped[uuid.UUID] = fk("content_item.id")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class Schedule(Base):
    __tablename__ = "schedule"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    content_item_id: Mapped[uuid.UUID] = fk("content_item.id")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recurrence: Mapped[str | None] = mapped_column(Text)


class PublishJob(Base):
    __tablename__ = "publish_job"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    content_item_id: Mapped[uuid.UUID] = fk("content_item.id")
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    channel: Mapped[str | None] = mapped_column(Text)


class Campaign(Base):
    __tablename__ = "campaign"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class AiProvider(Base):
    __tablename__ = "ai_provider"
    id: Mapped[uuid.UUID] = pk()  # Claude/OpenAI/Perplexity/Gemini/custom
    organisation_id: Mapped[uuid.UUID] = fk("organisation.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)  # routing config
    credential_id: Mapped[uuid.UUID | None] = fk("credential.id", nullable=True, ondelete="SET NULL")  # key in vault
    default_model: Mapped[str | None] = mapped_column(Text)
    fallback_id: Mapped[uuid.UUID | None] = fk("ai_provider.id", nullable=True, ondelete="SET NULL")  # routing & fallback


class Prompt(Base):
    __tablename__ = "prompt"
    id: Mapped[uuid.UUID] = pk()  # embedded or custom
    organisation_id: Mapped[uuid.UUID | None] = fk("organisation.id", nullable=True)  # null = built-in/embedded
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # variables {{...}}
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    is_custom: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Skill(Base):
    __tablename__ = "skill"
    id: Mapped[uuid.UUID] = pk()  # inbuilt or custom
    organisation_id: Mapped[uuid.UUID | None] = fk("organisation.id", nullable=True)  # null = inbuilt
    name: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    output_schema: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    is_custom: Mapped[bool] = mapped_column(Boolean, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Agent(Base):
    __tablename__ = "agent"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = fk("organisation.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)  # researcher/writer/editor
    provider_id: Mapped[uuid.UUID | None] = fk("ai_provider.id", nullable=True, ondelete="SET NULL")
    skill_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")  # → skill (many)


class AgentTeam(Base):
    __tablename__ = "agent_team"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    agent_team_id: Mapped[uuid.UUID] = fk("agent_team.id")
    status: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    input: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    output: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # trace, tokens, cost


class AiUsage(Base):  # ClickHouse in production
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    provider_id: Mapped[uuid.UUID] = fk("ai_provider.id")
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    cost: Mapped[float] = mapped_column(Numeric(12, 6), server_default="0")
    at: Mapped[datetime] = created()


class TopicalMap(Base):
    __tablename__ = "topical_map"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str | None] = mapped_column(Text)


class TopicNode(Base):
    __tablename__ = "topic_node"
    id: Mapped[uuid.UUID] = pk()  # pillar or cluster node
    organisation_id: Mapped[uuid.UUID] = org_fk()
    topical_map_id: Mapped[uuid.UUID] = fk("topical_map.id")
    parent_id: Mapped[uuid.UUID | None] = fk("topic_node.id", nullable=True)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[int | None] = mapped_column(Integer)


class ContentPlan(Base):
    __tablename__ = "content_plan"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    topical_map_id: Mapped[uuid.UUID] = fk("topical_map.id")
    internal_link_map: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))


class ContentBrief(Base):
    __tablename__ = "content_brief"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    content_plan_id: Mapped[uuid.UUID] = fk("content_plan.id")
    topic_node_id: Mapped[uuid.UUID] = fk("topic_node.id")
    content_item_id: Mapped[uuid.UUID | None] = fk("content_item.id", nullable=True)
    brief: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))


# =========================================================== leads & forms (Phase 6)
class Form(Base):
    __tablename__ = "form"
    id: Mapped[uuid.UUID] = pk()  # source form
    organisation_id: Mapped[uuid.UUID] = org_fk()  # spec: org_id
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True)
    name: Mapped[str | None] = mapped_column(Text)
    schema: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    embed_code: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))


class Lead(Base):
    __tablename__ = "lead"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    form_id: Mapped[uuid.UUID | None] = fk("form.id", nullable=True)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)  # dedupe keys
    utm_source: Mapped[str | None] = mapped_column(Text)
    utm_medium: Mapped[str | None] = mapped_column(Text)
    utm_campaign: Mapped[str | None] = mapped_column(Text)  # attribution
    crm_contact_id: Mapped[str | None] = mapped_column(Text)  # HubSpot id (nullable)
    status: Mapped[str] = mapped_column(Text, server_default="new")
    created_at: Mapped[datetime] = created()


class FormSubmission(Base):
    __tablename__ = "form_submission"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    form_id: Mapped[uuid.UUID] = fk("form.id")
    payload: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # → lead
    lead_id: Mapped[uuid.UUID | None] = fk("lead.id", nullable=True)
    created_at: Mapped[datetime] = created()


class FreeToolLead(Base):
    __tablename__ = "free_tool_lead"
    id: Mapped[uuid.UUID] = pk()  # captured via free tool
    organisation_id: Mapped[uuid.UUID | None] = fk("organisation.id", nullable=True)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


# =========================================================== marketplace (Phase 6)
class Service(Base):  # global catalog
    __tablename__ = "service"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # most-popular/premium/etc.


class MarketplaceListing(Base):  # cross-tenant publisher inventory
    __tablename__ = "marketplace_listing"
    id: Mapped[uuid.UUID] = pk()
    publisher_org_id: Mapped[uuid.UUID | None] = fk("organisation.id", nullable=True)  # [ASSUMPTION]
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    da: Mapped[float | None] = mapped_column(Numeric(6, 2))
    dr: Mapped[float | None] = mapped_column(Numeric(6, 2))
    as_: Mapped[float | None] = mapped_column("as", Numeric(6, 2))
    cf: Mapped[float | None] = mapped_column(Numeric(6, 2))
    tf: Mapped[float | None] = mapped_column(Numeric(6, 2))
    spam: Mapped[float | None] = mapped_column(Numeric(6, 2))  # authority & risk metrics
    org_traffic: Mapped[int | None] = mapped_column(BigInteger)
    ref_domains: Mapped[int | None] = mapped_column(Integer)
    guest_post_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    link_ins_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    link_type: Mapped[str | None] = mapped_column(Text)
    link_validity: Mapped[str | None] = mapped_column(Text)
    tat: Mapped[int | None] = mapped_column(Integer)  # order terms (days)


class Order(Base):
    __tablename__ = "order"
    id: Mapped[uuid.UUID] = pk()  # marketplace/service order
    organisation_id: Mapped[uuid.UUID] = fk("organisation.id")
    listing_id: Mapped[uuid.UUID | None] = fk("marketplace_listing.id", nullable=True)
    service_id: Mapped[uuid.UUID | None] = fk("service.id", nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default="cart")  # cart→paid→in-progress→delivered
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    tat_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backlink_id: Mapped[uuid.UUID | None] = fk("backlink.id", nullable=True)  # on delivery


class Wallet(Base):
    __tablename__ = "wallet"
    id: Mapped[uuid.UUID] = pk()  # [ASSUMPTION]
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), unique=True, nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), server_default="0")


class WalletTxn(Base):
    __tablename__ = "wallet_txn"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    wallet_id: Mapped[uuid.UUID] = fk("wallet.id")
    type: Mapped[str] = mapped_column(Text, nullable=False)  # add-funds/charge/payout/invoice
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = created()


# =========================================================== billing & spec
class UsageCounter(Base):  # [ASSUMPTION] metered quotas from Pricing Matrix
    __tablename__ = "usage_counter"
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    period: Mapped[str] = mapped_column(Text, primary_key=True)  # YYYY-MM
    used: Mapped[int] = mapped_column(BigInteger, server_default="0")


class Report(Base):  # [ASSUMPTION] generated report files
    __tablename__ = "report"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    audit_run_id: Mapped[uuid.UUID | None] = fk("audit_run.id", nullable=True)
    format: Mapped[str] = mapped_column(Text, nullable=False)  # pdf/xlsx
    stored_file_id: Mapped[uuid.UUID | None] = fk("stored_file.id", nullable=True)
    white_label: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    created_at: Mapped[datetime] = created()


class SpecSnapshot(Base):  # [ASSUMPTION] global: loaded product specification versions
    __tablename__ = "spec_snapshot"
    id: Mapped[uuid.UUID] = pk()
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # bundled/upload/google_drive
    spec: Mapped[dict] = mapped_column(J, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # active/pending_review/rejected/superseded
    diff: Mapped[dict | None] = mapped_column(J)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


from . import models_monitor  # noqa: E402,F401  (registers monitoring tables on Base)
from . import models_backlinks, models_local, models_ai, models_social, models_growth, models_access  # noqa: E402,F401
from . import models_checklists, models_auditskills, models_errorcenter, models_clientportal, models_linksintel  # noqa: E402,F401
