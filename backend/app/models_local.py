"""Data model additions for the local modules (tables beyond the spec's Database Schema sheet).

The spec tables (gbp_location, gbp_review, gbp_post, grid_scan, grid_point, competitor) are kept as-is;
everything the Local SEO modules need on top of them lives in 1:1 extension tables here so the
shared migration never has to ALTER a spec table.
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import date, datetime  # noqa: F401

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

LISTING_KINDS = ("own", "competitor")
POST_TYPES = ("update", "offer", "event")
POST_STATES = ("draft", "review", "approved", "publishing", "published", "rejected")
REPLY_STATES = ("none", "draft", "review", "approved", "publishing", "published", "rejected")
CTA_TYPES = ("", "BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL")
RUN_KINDS = ("gbp_audit", "categories", "website_gbp", "local_scan")


class LocalListing(Base):
    """1:1 profile extension of gbp_location (own business locations AND competitor listings)."""
    __tablename__ = "local_listing"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gbp_location.id", ondelete="CASCADE"),
                                                   unique=True, nullable=False)
    project_id: Mapped[uuid.UUID] = fk("project.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="own")  # own / competitor
    competitor_id: Mapped[uuid.UUID | None] = fk("competitor.id", nullable=True, ondelete="SET NULL")
    source: Mapped[str] = mapped_column(Text, server_default="manual")  # manual / import / gbp / places
    gbp_resource: Mapped[str | None] = mapped_column(Text)  # accounts/{a}/locations/{l} when synced from GBP
    primary_category: Mapped[str | None] = mapped_column(Text)
    secondary_categories: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    hours: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # {"mon": [["09:00","17:00"]], ...}
    description: Mapped[str | None] = mapped_column(Text)
    photos_count: Mapped[int] = mapped_column(Integer, server_default="0")
    has_logo: Mapped[bool] = mapped_column(Boolean, server_default="false")
    services: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    products: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    attributes: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    qa_questions: Mapped[int] = mapped_column(Integer, server_default="0")
    qa_answered: Mapped[int] = mapped_column(Integer, server_default="0")
    social_links: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    citations: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # [{source,name,address,phone}]
    keywords: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # target local keywords
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2))  # listing totals (when individual reviews unknown)
    review_count: Mapped[int | None] = mapped_column(Integer)
    posts_per_month: Mapped[float | None] = mapped_column(Numeric(6, 2))  # competitor cadence (manual / import)
    last_post_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified: Mapped[bool] = mapped_column(Boolean, server_default="false")
    discovered_for: Mapped[str | None] = mapped_column(Text)  # keyword that surfaced this competitor
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LocalReviewExt(Base):
    """1:1 extension of gbp_review: author, reply workflow (draft → review → approved → published), topics."""
    __tablename__ = "local_review_ext"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gbp_review.id", ondelete="CASCADE"),
                                                 unique=True, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)  # GBP review resource name
    author: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="manual")
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_draft: Mapped[str | None] = mapped_column(Text)
    reply_status: Mapped[str] = mapped_column(Text, server_default="none")
    reply_source: Mapped[str | None] = mapped_column(Text)  # ai / manual
    approval_id: Mapped[uuid.UUID | None] = fk("approval.id", nullable=True, ondelete="SET NULL")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    publish_error: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = created()


class LocalPostExt(Base):
    """1:1 extension of gbp_post: title, CTA, dates, image, workflow + publishing metadata."""
    __tablename__ = "local_post_ext"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gbp_post.id", ondelete="CASCADE"),
                                               unique=True, nullable=False)
    project_id: Mapped[uuid.UUID] = fk("project.id")
    title: Mapped[str | None] = mapped_column(Text)
    cta_type: Mapped[str | None] = mapped_column(Text)
    cta_url: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_code: Mapped[str | None] = mapped_column(Text)
    image_ref: Mapped[str | None] = mapped_column(Text)  # https URL or stored_file id
    source: Mapped[str] = mapped_column(Text, server_default="manual")  # manual / ai / import / gbp / automation
    automation_id: Mapped[uuid.UUID | None] = fk("local_automation.id", nullable=True, ondelete="SET NULL")
    approval_id: Mapped[uuid.UUID | None] = fk("approval.id", nullable=True, ondelete="SET NULL")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    external_name: Mapped[str | None] = mapped_column(Text)  # GBP localPost resource name
    publish_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LocalGridScanExt(Base):
    """1:1 extension of grid_scan: project/location binding, radius, mode, metrics."""
    __tablename__ = "local_grid_scan_ext"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    scan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("grid_scan.id", ondelete="CASCADE"),
                                               unique=True, nullable=False)
    project_id: Mapped[uuid.UUID] = fk("project.id")
    location_id: Mapped[uuid.UUID] = fk("gbp_location.id")
    radius_km: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    mode: Mapped[str] = mapped_column(Text, server_default="import")  # live / import
    depth: Mapped[int] = mapped_column(Integer, server_default="20")
    metrics: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    point_top: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # {idx: [top 3 names]}
    credits: Mapped[int] = mapped_column(Integer, server_default="0")
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocalRun(Base):
    """Saved audit runs (GBP audit, category finder, website ↔ GBP, local scan)."""
    __tablename__ = "local_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    location_id: Mapped[uuid.UUID | None] = fk("gbp_location.id", nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    params: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    result: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class LocalAutomation(Base):
    """GMB automation (module 35): post template × locations × recurring schedule → posts in review."""
    __tablename__ = "local_automation"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    post_type: Mapped[str] = mapped_column(Text, server_default="update")
    title_template: Mapped[str | None] = mapped_column(Text)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    cta_type: Mapped[str | None] = mapped_column(Text)
    cta_url_template: Mapped[str | None] = mapped_column(Text)
    image_ref: Mapped[str | None] = mapped_column(Text)
    location_ids: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    variables: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # {location_id: {var: value}}
    frequency: Mapped[str] = mapped_column(Text, server_default="once")  # once / weekly / monthly
    schedule_id: Mapped[uuid.UUID | None] = fk("job_schedule.id", nullable=True, ondelete="SET NULL")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    runs: Mapped[int] = mapped_column(Integer, server_default="0")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
