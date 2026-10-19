"""Autonomous monitoring data model (monitoring brief: per-project sources, teams,
time-series metrics, issues/incidents, change log, content watch, gap reports).

All tables are tenant-owned (organisation_id + RLS) and project-scoped so data from
different projects is never mixed.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, created, fk, org_fk, pk

TEAMS = ["seo", "developer", "content", "backlink", "analytics", "pm"]
CATEGORIES = ["developer", "seo", "content", "backlink", "analytics"]


class ProjectSource(Base):
    """A data source bound to ONE project (e.g. this project's GSC property)."""
    __tablename__ = "project_source"
    __table_args__ = (UniqueConstraint("project_id", "provider", name="uq_project_source"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # gsc/ga4/ahrefs/semrush/screaming_frog
    property: Mapped[str | None] = mapped_column(Text)  # sc-domain:x.com / properties/123 / x.com
    config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    mode: Mapped[str] = mapped_column(Text, server_default="api")  # api / import
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(Text)  # ok / error / not_connected
    last_error: Mapped[str | None] = mapped_column(Text)
    rows_last_sync: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = created()


class ProjectMember(Base):
    """Who is on which team for a project — the routing target for issues."""
    __tablename__ = "project_member"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "team", name="uq_project_member"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    user_id: Mapped[uuid.UUID] = fk("user.id")
    team: Mapped[str] = mapped_column(Text, nullable=False)  # seo/developer/content/backlink/analytics/pm
    is_lead: Mapped[bool] = mapped_column(Boolean, server_default="false")


class MonitorConfig(Base):
    __tablename__ = "monitor_config"
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    thresholds: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    severity_overrides: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # kind -> severity
    routing: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # category -> team
    channels: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # slack_webhook, teams, email
    important_urls: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # revenue/priority pages


# ------------------------------------------------------------------ time series
class SearchMetric(Base):
    """GSC performance rows. grain: total (date/device/country), page (date/page/device/country), detail (+query)."""
    __tablename__ = "search_metric"
    __table_args__ = (Index("ix_search_metric_proj_date", "project_id", "date"),
                      Index("ix_search_metric_proj_page", "project_id", "page"),
                      Index("ix_search_metric_proj_query", "project_id", "query"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    grain: Mapped[str] = mapped_column(Text, nullable=False, server_default="detail")
    query: Mapped[str | None] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(Text)
    device: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    clicks: Mapped[int] = mapped_column(Integer, server_default="0")
    impressions: Mapped[int] = mapped_column(Integer, server_default="0")
    position: Mapped[float | None] = mapped_column(Numeric(8, 2))  # impression-weighted when aggregated
    source: Mapped[str] = mapped_column(Text, server_default="gsc")


class AnalyticsMetric(Base):
    """GA4 rows by date × landing page × device × country × channel."""
    __tablename__ = "analytics_metric"
    __table_args__ = (Index("ix_analytics_metric_proj_date", "project_id", "date"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    landing_page: Mapped[str | None] = mapped_column(Text)
    device: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    sessions: Mapped[int] = mapped_column(Integer, server_default="0")
    users: Mapped[int] = mapped_column(Integer, server_default="0")
    engaged_sessions: Mapped[int] = mapped_column(Integer, server_default="0")
    conversions: Mapped[float] = mapped_column(Numeric(12, 2), server_default="0")
    revenue: Mapped[float] = mapped_column(Numeric(14, 2), server_default="0")
    source: Mapped[str] = mapped_column(Text, server_default="ga4")


class LinkMetric(Base):
    """Domain-level link/visibility snapshot (Ahrefs/Semrush)."""
    __tablename__ = "link_metric"
    __table_args__ = (Index("ix_link_metric_proj_date", "project_id", "date"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    refdomains: Mapped[int | None] = mapped_column(Integer)
    backlinks: Mapped[int | None] = mapped_column(BigInteger)
    domain_rating: Mapped[float | None] = mapped_column(Numeric(6, 2))
    organic_keywords: Mapped[int | None] = mapped_column(Integer)
    organic_traffic: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class RefdomainSnapshot(Base):
    """Referring-domain list per snapshot — lost/new referring domains are diffs of these."""
    __tablename__ = "refdomain_snapshot"
    __table_args__ = (Index("ix_refdomain_proj_date", "project_id", "snapshot_date"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    target: Mapped[str] = mapped_column(Text, server_default="own")  # "own" or the competitor domain whose links these are
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    domain_rating: Mapped[float | None] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(Text, nullable=False)


class CompetitorKeyword(Base):
    """Keywords a domain ranks for (own domain has is_own=true) — input to keyword/content gap."""
    __tablename__ = "competitor_keyword"
    __table_args__ = (Index("ix_competitor_keyword_proj", "project_id", "domain"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    is_own: Mapped[bool] = mapped_column(Boolean, server_default="false")
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    imported_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ change log
class SiteChange(Base):
    """A detected change on a page between two crawls (old → new)."""
    __tablename__ = "site_change"
    __table_args__ = (Index("ix_site_change_proj", "project_id", "detected_at"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    site_id: Mapped[uuid.UUID] = fk("site.id")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)  # status/title/meta/h1/canonical/robots/indexable/page_added/page_removed/word_count
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    from_crawl_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_crawl_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    changed_by: Mapped[str | None] = mapped_column(Text)  # crawl / cms / user
    impact: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # filled by impact analysis
    detected_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ issues / incidents
class MonitorIssue(Base):
    __tablename__ = "monitor_issue"
    __table_args__ = (Index("ix_monitor_issue_proj_status", "project_id", "status"),
                      Index("ix_monitor_issue_fp", "project_id", "fingerprint"))
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id", index=False)
    site_id: Mapped[uuid.UUID | None] = fk("site.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)  # developer/seo/content/backlink/analytics
    categories: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # all teams involved
    severity: Mapped[str] = mapped_column(Text, nullable=False)  # critical/high/medium/low
    is_opportunity: Mapped[bool] = mapped_column(Boolean, server_default="false")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str | None] = mapped_column(Text)
    dimension: Mapped[str | None] = mapped_column(Text)  # e.g. device=mobile
    evidence: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    root_cause: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    recommendation: Mapped[str | None] = mapped_column(Text)
    verify: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # how the fix is verified
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="open")  # open/in_progress/verifying/resolved/reopened/ignored
    task_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    occurrences: Mapped[int] = mapped_column(Integer, server_default="1")
    first_seen: Mapped[datetime] = created()
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class MonitorRun(Base):
    __tablename__ = "monitor_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    started_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    summary: Mapped[str | None] = mapped_column(Text)  # plain-language project briefing


class ContentWatch(Base):
    """A published page (blog etc.) checked at 7/14/30/60/90 days."""
    __tablename__ = "content_watch"
    __table_args__ = (UniqueConstraint("project_id", "url", name="uq_content_watch"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    target_keyword: Mapped[str | None] = mapped_column(Text)
    competitor_urls: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    content_item_id: Mapped[uuid.UUID | None] = fk("content_item.id", nullable=True, ondelete="SET NULL")
    published_at: Mapped[date] = mapped_column(Date, nullable=False)
    checkpoints: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # "7": {...}
    status: Mapped[str] = mapped_column(Text, server_default="watching")  # watching/performing/underperforming/done
    source: Mapped[str] = mapped_column(Text, server_default="manual")  # manual/auto_gsc/cms


class GapReport(Base):
    __tablename__ = "gap_report"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # keyword/content/structure/competitor/full
    target_url: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    result: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    issue_id: Mapped[uuid.UUID | None] = fk("monitor_issue.id", nullable=True, ondelete="SET NULL")
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ workstreams, strategies, activities
WORKSTREAMS = ["strategy", "on_page", "technical", "off_page", "content", "aio", "aeo", "geo", "sxo", "local", "analytics",
               "social", "paid_social"]


class StrategyTemplate(Base):
    """An SEO strategy: a named bundle of recurring activities. organisation_id NULL = built-in."""
    __tablename__ = "strategy_template"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID | None] = fk("organisation.id", nullable=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    activities: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    is_builtin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = created()


class ProjectStrategy(Base):
    __tablename__ = "project_strategy"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    template_id: Mapped[uuid.UUID | None] = fk("strategy_template.id", nullable=True, ondelete="SET NULL")
    template_key: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    owner_user_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    goals: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created()


class ProjectActivity(Base):
    """A recurring piece of work in a project workstream, done by a person, a team or an AI agent."""
    __tablename__ = "project_activity"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_project_activity_key"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    strategy_id: Mapped[uuid.UUID | None] = fk("project_strategy.id", nullable=True, ondelete="SET NULL")
    workstream: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text)
    cadence: Mapped[str] = mapped_column(Text, server_default="weekly")  # once/daily/weekly/biweekly/monthly
    assignee_type: Mapped[str] = mapped_column(Text, server_default="team")  # user / team / ai_agent
    assignee_user_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    team: Mapped[str | None] = mapped_column(Text)  # team that does / reviews the work
    agent_key: Mapped[str | None] = mapped_column(Text)
    approval_required: Mapped[bool] = mapped_column(Boolean, server_default="true")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    next_due: Mapped[date | None] = mapped_column(Date)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created()


class ActivityRun(Base):
    """One execution of an activity (AI agent output or the task handed to a person)."""
    __tablename__ = "activity_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    activity_id: Mapped[uuid.UUID | None] = fk("project_activity.id", nullable=True, ondelete="SET NULL")
    agent_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="RUNNING")  # COMPLETED/FAILED/NEEDS_CONNECTION/NEEDS_DATA/HANDED_TO_HUMAN
    output: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    summary: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[uuid.UUID | None] = fk("task.id", nullable=True, ondelete="SET NULL")
    triggered_by: Mapped[str] = mapped_column(Text, server_default="schedule")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ project profile + prompt skills
class ProjectProfile(Base):
    """Business context every skill fills its placeholders from (industry, product, audience, USP, tone, keywords…)."""
    __tablename__ = "project_profile"
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PromptSkill(Base):
    """An organisation's own prompt skill (built-in skills live in code). Every edit bumps the version; history keeps old templates."""
    __tablename__ = "prompt_skill"
    __table_args__ = (UniqueConstraint("organisation_id", "key", name="uq_prompt_skill_key"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, server_default="custom")
    workstream: Mapped[str] = mapped_column(Text, server_default="content")
    description: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # [{key,label,required}]
    evidence: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # collectors: site_pages, competitor_pages, gsc_queries…
    output_columns: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # if set, the AI must return a table
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    history: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


class SkillRun(Base):
    """One run of a skill on a project: inputs, the evidence collected, the exact prompt, and the result."""
    __tablename__ = "skill_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    skill_key: Mapped[str] = mapped_column(Text, nullable=False)
    skill_version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[str] = mapped_column(Text, server_default="RUNNING")  # COMPLETED / NEEDS_DATA / FAILED
    mode: Mapped[str | None] = mapped_column(Text)  # ai / data / data+template
    inputs: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    evidence: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    prompt: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # {text, tables:{name:[rows]}, notes}
    model: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    stored_file_id: Mapped[uuid.UUID | None] = fk("stored_file.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailOutbox(Base):
    """Every email the platform tries to send (task assignments, alerts). Status is honest:
    sent / not_connected (no SMTP configured) / failed — nothing is silently dropped."""
    __tablename__ = "email_outbox"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    to_email: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, server_default="task_assigned")
    entity: Mapped[str | None] = mapped_column(Text)  # task:<id> / monitor_issue:<id>
    status: Mapped[str] = mapped_column(Text, server_default="queued")  # queued/sent/not_connected/failed
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = created()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
