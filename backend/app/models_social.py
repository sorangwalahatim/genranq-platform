"""Data model additions for the social modules (tables beyond the spec's Database Schema sheet).

Spec tables reused as-is: content_item / content_version (owned by the AI group's state machine),
schedule, publish_job, campaign, gbp_post, report, stored_file, job_schedule, approval, notification.
The tables below extend them 1:1 (``*_id unique``) or add what the spec does not define.
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import date, datetime  # noqa: F401

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

J = JSONB


# ------------------------------------------------------------------ 38 social accounts
class SocialAccount(Base):
    """A Facebook page / Instagram business account / LinkedIn page / X account mapped to a project.
    The org-level OAuth connection lives in integration_connection (providers meta / linkedin / x)."""
    __tablename__ = "social_account"
    __table_args__ = (UniqueConstraint("project_id", "channel", "external_id", name="uq_social_account_project_channel_ext"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # meta / linkedin / x
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # facebook / instagram / linkedin / x
    external_id: Mapped[str] = mapped_column(Text, nullable=False)  # page id / IG user id / organization id / X user id
    name: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(Text, server_default="active")  # active / error / disabled
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ 36 campaigns / rules / scheduling
class SocialCampaign(Base):
    """1:1 extension of the spec ``campaign`` table: project, window and description."""
    __tablename__ = "social_campaign"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaign.id", ondelete="CASCADE"), unique=True, nullable=False)
    project_id: Mapped[uuid.UUID] = fk("project.id")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")  # active / archived


class SocialRule(Base):
    """Recurring publishing rule: weekly days + local times in an IANA timezone.
    mode=queue: each queued approved item is used once, in order; mode=evergreen: items cycle."""
    __tablename__ = "social_rule"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True, ondelete="SET NULL")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    account_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    weekdays: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # 0=Mon … 6=Sun
    times: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # ["09:00", "17:30"] local
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(Text, server_default="queue")  # queue / evergreen
    item_ids: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # ordered content_item ids
    horizon_days: Mapped[int] = mapped_column(Integer, server_default="14")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class SocialSchedule(Base):
    """1:1 extension of the spec ``schedule`` row: target account, campaign, rule slot and status."""
    __tablename__ = "social_schedule"
    __table_args__ = (UniqueConstraint("rule_id", "slot_at", "social_account_id", name="uq_social_schedule_rule_slot"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    schedule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), unique=True, nullable=False)
    project_id: Mapped[uuid.UUID] = fk("project.id")
    content_item_id: Mapped[uuid.UUID] = fk("content_item.id")
    social_account_id: Mapped[uuid.UUID | None] = fk("social_account.id", nullable=True, ondelete="SET NULL")
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True, ondelete="SET NULL")
    rule_id: Mapped[uuid.UUID | None] = fk("social_rule.id", nullable=True, ondelete="SET NULL")
    slot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # original rule slot (dedupe key)
    status: Mapped[str] = mapped_column(Text, server_default="scheduled")  # scheduled/publishing/published/failed/cancelled
    publish_job_id: Mapped[uuid.UUID | None] = fk("publish_job.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class SocialPublish(Base):
    """1:1 extension of ``publish_job``: what was sent where and what the platform returned."""
    __tablename__ = "social_publish"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    publish_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publish_job.id", ondelete="CASCADE"), unique=True, nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = fk("schedule.id", nullable=True, ondelete="SET NULL")
    project_id: Mapped[uuid.UUID] = fk("project.id")
    social_account_id: Mapped[uuid.UUID | None] = fk("social_account.id", nullable=True, ondelete="SET NULL")
    provider: Mapped[str | None] = mapped_column(Text)
    external_post_id: Mapped[str | None] = mapped_column(Text, index=True)
    external_url: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)  # not_connected / platform_error / outcome_unknown / invalid
    body_sent: Mapped[str | None] = mapped_column(Text)
    sending_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class SocialPostMeta(Base):
    """Social extras for a content_item (link / image used by the publisher)."""
    __tablename__ = "social_post_meta"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    content_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_item.id", ondelete="CASCADE"), unique=True, nullable=False)
    link_url: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    campaign_id: Mapped[uuid.UUID | None] = fk("campaign.id", nullable=True, ondelete="SET NULL")


# ------------------------------------------------------------------ 33 content automation
class ContentBatch(Base):
    __tablename__ = "content_batch"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # drafts / csv
    channel: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class ContentBatchItem(Base):
    __tablename__ = "content_batch_item"
    __table_args__ = (UniqueConstraint("batch_id", "content_item_id", name="uq_content_batch_item"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    batch_id: Mapped[uuid.UUID] = fk("content_batch.id")
    content_item_id: Mapped[uuid.UUID] = fk("content_item.id")
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    gen_status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending/queued/generated/not_connected/failed
    gen_error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


# ------------------------------------------------------------------ 40 analytics
class SocialMetric(Base):
    """Engagement snapshot for one published post (cumulative counters at captured_at)."""
    __tablename__ = "social_metric"
    __table_args__ = (Index("ix_social_metric_post", "organisation_id", "channel", "external_post_id", "captured_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    social_account_id: Mapped[uuid.UUID | None] = fk("social_account.id", nullable=True, ondelete="SET NULL")
    publish_job_id: Mapped[uuid.UUID | None] = fk("publish_job.id", nullable=True, ondelete="SET NULL")
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    external_post_id: Mapped[str] = mapped_column(Text, nullable=False)
    post_title: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    likes: Mapped[int] = mapped_column(Integer, server_default="0")
    comments: Mapped[int] = mapped_column(Integer, server_default="0")
    shares: Mapped[int] = mapped_column(Integer, server_default="0")
    impressions: Mapped[int] = mapped_column(Integer, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, server_default="0")
    source: Mapped[str] = mapped_column(Text, nullable=False)  # api / csv


# ------------------------------------------------------------------ 37 report automation
class ReportSchedule(Base):
    __tablename__ = "report_schedule"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)  # weekly / monthly
    contents: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # audit_pdf/audit_xlsx/monitoring/rank_changes/social
    recipient_user_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    recipient_emails: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    channels: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # {in_app, teams, email}
    white_label: Mapped[bool] = mapped_column(Boolean, server_default="false")
    job_schedule_id: Mapped[uuid.UUID | None] = fk("job_schedule.id", nullable=True, ondelete="SET NULL")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class ReportDelivery(Base):
    __tablename__ = "report_delivery"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    report_schedule_id: Mapped[uuid.UUID | None] = fk("report_schedule.id", nullable=True, ondelete="SET NULL")
    project_id: Mapped[uuid.UUID] = fk("project.id")
    status: Mapped[str] = mapped_column(Text, server_default="running")  # running/delivered/partial/failed
    files: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # [{stored_file_id, filename, kind}]
    channels: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # per-channel outcome
    summary: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(Text, server_default="manual")
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ 46 Microsoft Teams
class TeamsChannel(Base):
    __tablename__ = "teams_channel"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True)  # null = org-wide
    name: Mapped[str] = mapped_column(Text, nullable=False)
    use_hub: Mapped[bool] = mapped_column(Boolean, server_default="false")  # reuse Integrations Hub ms_teams webhook
    webhook_ref: Mapped[str | None] = mapped_column(Text)  # vault ref (the URL embeds a secret)
    webhook_hint: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class TeamsDelivery(Base):
    __tablename__ = "teams_delivery"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    channel_id: Mapped[uuid.UUID | None] = fk("teams_channel.id", nullable=True, ondelete="SET NULL")
    event: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # sent / failed / not_connected
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class SocialOrgState(Base):
    """Per-organisation cursors for event sweeps (e.g. Teams: approvals / critical issues already sent)."""
    __tablename__ = "social_org_state"
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), primary_key=True)
    teams_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    portal_title: Mapped[str | None] = mapped_column(Text)
    support_email: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ 47 white-label domains
class CustomDomain(Base):
    __tablename__ = "custom_domain"
    __table_args__ = (UniqueConstraint("organisation_id", "domain", name="uq_custom_domain_org"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    verification_token: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending")  # pending / verified / failed
    txt_ok: Mapped[bool] = mapped_column(Boolean, server_default="false")
    cname_ok: Mapped[bool] = mapped_column(Boolean, server_default="false")
    method: Mapped[str | None] = mapped_column(Text)  # dns_txt / cname
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tls_status: Mapped[str] = mapped_column(Text, server_default="awaiting_operator")  # awaiting_operator / active (operator-set)
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ 23 Chrome extension
class ExtensionToken(Base):
    """Personal access token for the browser extension. Only the SHA-256 hash is stored."""
    __tablename__ = "extension_token"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = fk("user.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class SavedBusiness(Base):
    """A local business saved from Google Maps / Business Profile via the extension."""
    __tablename__ = "saved_business"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1))
    reviews: Mapped[int | None] = mapped_column(Integer)
    maps_url: Mapped[str | None] = mapped_column(Text)
    competitor_id: Mapped[uuid.UUID | None] = fk("competitor.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
