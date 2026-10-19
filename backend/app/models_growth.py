"""Data model additions for the growth modules (tables beyond the spec's Database Schema sheet).

Phase 6 — Leads, Marketplace & Integrations. Spec tables used as-is: lead, form, form_submission,
free_tool_lead, service, marketplace_listing, order, wallet, wallet_txn. Everything here is a
1:1 extension or a supporting table, and every table carries ``organisation_id`` so RLS applies.
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import date, datetime  # noqa: F401

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

J = JSONB


def jdict():
    return mapped_column(J, server_default=text("'{}'::jsonb"), nullable=False)


def jlist():
    return mapped_column(J, server_default=text("'[]'::jsonb"), nullable=False)


def updated():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# =========================================================== leads (module 41)
class GrowthLead(Base):  # 1:1 extension of the spec `lead` row
    __tablename__ = "growth_lead"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lead.id", ondelete="CASCADE"), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    email_norm: Mapped[str | None] = mapped_column(Text, index=True)
    phone_key: Mapped[str | None] = mapped_column(Text, index=True)
    assignee_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    score: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    segment: Mapped[str | None] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    source: Mapped[str | None] = mapped_column(Text)  # form/webhook/import/manual/calendly/hubspot/free_tool
    referrer: Mapped[str | None] = mapped_column(Text)
    landing_url: Mapped[str | None] = mapped_column(Text)
    custom: Mapped[dict] = jdict()
    possible_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lost_reason: Mapped[str | None] = mapped_column(Text)
    won_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hubspot_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crm_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated()


class GrowthLeadNote(Base):
    __tablename__ = "growth_lead_note"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = fk("lead.id")
    author_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class GrowthLeadActivity(Base):  # activity timeline
    __tablename__ = "growth_lead_activity"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = fk("lead.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = jdict()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class GrowthTouchpoint(Base):  # attribution touchpoints (module 42)
    __tablename__ = "growth_touchpoint"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = fk("lead.id")
    project_id: Mapped[uuid.UUID] = fk("project.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # form/webhook/booking/import/manual/free_tool/hubspot
    source: Mapped[str] = mapped_column(Text, nullable=False)
    medium: Mapped[str] = mapped_column(Text, nullable=False)
    campaign: Mapped[str | None] = mapped_column(Text)
    term: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    referrer: Mapped[str | None] = mapped_column(Text)
    landing_url: Mapped[str | None] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# =========================================================== forms (module 64)
class GrowthFormMeta(Base):
    __tablename__ = "growth_form_meta"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    form_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("form.id", ondelete="CASCADE"), unique=True, nullable=False)
    public_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)  # active/paused
    kind: Mapped[str] = mapped_column(Text, server_default="contact", nullable=False)  # contact/custom
    settings: Mapped[dict] = jdict()  # spam guard, assignees, questionnaire, redirect
    rr_index: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)  # round-robin assignment cursor
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class GrowthSpamEvent(Base):
    __tablename__ = "growth_spam_event"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    form_id: Mapped[uuid.UUID | None] = fk("form.id", nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)  # honeypot/too_fast/rate_limited/blocked_word/bad_token
    ip_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class GrowthRateEvent(Base):  # per-IP rate limiting of public endpoints
    __tablename__ = "growth_rate_event"
    __table_args__ = (Index("ix_growth_rate_lookup", "organisation_id", "bucket", "key_hash", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class GrowthOrgPublic(Base):  # per-org public key for free tools / public monitor
    __tablename__ = "growth_org_public"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    free_tools_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")  # free-tool emails → leads here
    created_at: Mapped[datetime] = created()


# =========================================================== connectors (module 65)
class GrowthConnector(Base):
    __tablename__ = "growth_connector"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    preset: Mapped[str] = mapped_column(Text, nullable=False)  # generic/typeform/gravity_forms/webflow/facebook_lead_ads
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)  # vault reference to the HMAC secret
    secret_hint: Mapped[str | None] = mapped_column(Text)
    verify_token: Mapped[str | None] = mapped_column(Text)  # Facebook subscription challenge token
    project_id: Mapped[uuid.UUID] = fk("project.id")
    form_id: Mapped[uuid.UUID | None] = fk("form.id", nullable=True, ondelete="SET NULL")
    mapping: Mapped[dict] = jdict()
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthConnectorDelivery(Base):
    __tablename__ = "growth_connector_delivery"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    connector_id: Mapped[uuid.UUID] = fk("growth_connector.id")
    status: Mapped[str] = mapped_column(Text, nullable=False)  # accepted/rejected/error/test/not_connected
    signature_ok: Mapped[bool | None] = mapped_column(Boolean)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = jdict()
    mapped: Mapped[dict] = jdict()
    lead_id: Mapped[uuid.UUID | None] = fk("lead.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


# =========================================================== CRM: HubSpot (43) & Calendly (44)
class GrowthHubspotSettings(Base):
    __tablename__ = "growth_hubspot_settings"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), unique=True, nullable=False)
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")
    field_map: Mapped[dict] = jdict()
    auto_sync: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthSyncLog(Base):
    __tablename__ = "growth_sync_log"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # push/pull/two_way/webhook
    status: Mapped[str] = mapped_column(Text, nullable=False)  # ok/error/not_connected/partial
    counts: Mapped[dict] = jdict()
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[list] = jlist()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class GrowthCalendlySettings(Base):
    __tablename__ = "growth_calendly_settings"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), unique=True, nullable=False)
    webhook_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    signing_ref: Mapped[str | None] = mapped_column(Text)  # vault ref of the webhook signing key
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")
    create_leads: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    subscription_uri: Mapped[str | None] = mapped_column(Text)
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthBookingLink(Base):
    __tablename__ = "growth_booking_link"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_at: Mapped[datetime] = created()


class GrowthBooking(Base):
    __tablename__ = "growth_booking"
    __table_args__ = (UniqueConstraint("organisation_id", "invitee_uri", name="uq_growth_booking_invitee"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID | None] = fk("lead.id", nullable=True, ondelete="SET NULL")
    booking_link_id: Mapped[uuid.UUID | None] = fk("growth_booking_link.id", nullable=True, ondelete="SET NULL")
    invitee_uri: Mapped[str] = mapped_column(Text, nullable=False)
    event_uri: Mapped[str | None] = mapped_column(Text)
    event_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)  # active/canceled
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tracking: Mapped[dict] = jdict()
    signature_verified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    source: Mapped[str] = mapped_column(Text, server_default="webhook", nullable=False)  # webhook/api
    created_at: Mapped[datetime] = created()
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# =========================================================== onboarding / profiling (66)
class GrowthQuestionnaire(Base):
    __tablename__ = "growth_questionnaire"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    questions: Mapped[list] = jlist()
    rules: Mapped[list] = jlist()
    segments: Mapped[list] = jlist()
    attach_signup: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class GrowthQuestionnaireResponse(Base):
    __tablename__ = "growth_questionnaire_response"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    questionnaire_id: Mapped[uuid.UUID] = fk("growth_questionnaire.id")
    lead_id: Mapped[uuid.UUID | None] = fk("lead.id", nullable=True, ondelete="SET NULL")
    user_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    answers: Mapped[dict] = jdict()
    score: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    segment: Mapped[str | None] = mapped_column(Text)
    breakdown: Mapped[list] = jlist()
    created_at: Mapped[datetime] = created()


# =========================================================== marketplace (14, 74, 75)
class GrowthListingExt(Base):  # publisher-owned extension of a marketplace_listing (organisation_id = publisher)
    __tablename__ = "growth_listing_ext"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("marketplace_listing.id", ondelete="CASCADE"), unique=True, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    rules: Mapped[str | None] = mapped_column(Text)
    sample_urls: Mapped[list] = jlist()
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)  # active/paused
    metrics_source: Mapped[dict] = jdict()  # metric → {"source": "self"|"ahrefs"|"semrush", "verified_at": iso}
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class GrowthCartItem(Base):
    __tablename__ = "growth_cart_item"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = fk("user.id")
    listing_id: Mapped[uuid.UUID | None] = fk("marketplace_listing.id", nullable=True)
    service_id: Mapped[uuid.UUID | None] = fk("service.id", nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # guest_post/link_insertion/service
    tier: Mapped[str | None] = mapped_column(Text)
    addons: Mapped[list] = jlist()
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")
    target_url: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class GrowthOrder(Base):  # advertiser-side extension of the spec `order` row
    __tablename__ = "growth_order"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order.id", ondelete="CASCADE"), unique=True, nullable=False)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    publisher_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    target_url: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[str | None] = mapped_column(Text)
    brief: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list] = jlist()  # stored_file ids (advertiser org)
    tier: Mapped[str | None] = mapped_column(Text)
    addons: Mapped[list] = jlist()
    link_type: Mapped[str | None] = mapped_column(Text)
    live_url: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[dict] = jdict()
    verified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    fee_pct: Mapped[float] = mapped_column(Numeric(5, 2), server_default="0", nullable=False)
    fee_amount: Mapped[float] = mapped_column(Numeric(12, 2), server_default="0", nullable=False)
    publisher_amount: Mapped[float] = mapped_column(Numeric(12, 2), server_default="0", nullable=False)
    timeline: Mapped[list] = jlist()
    note: Mapped[str | None] = mapped_column(Text)  # reject / dispute reason, delivery notes
    placed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    placed_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class GrowthPublisherOrder(Base):  # publisher-side mirror (organisation_id = publisher org)
    __tablename__ = "growth_publisher_order"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order.id", ondelete="CASCADE"), unique=True, nullable=False)
    number: Mapped[str] = mapped_column(Text, nullable=False)
    advertiser_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)  # internal: never returned by the API
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    domain: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[str | None] = mapped_column(Text)
    brief: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list] = jlist()
    link_type: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # publisher's net earning
    gross: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    live_url: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[dict] = jdict()
    verified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    tat_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeline: Mapped[list] = jlist()
    note: Mapped[str | None] = mapped_column(Text)
    placed_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = updated()


class GrowthEscrow(Base):
    __tablename__ = "growth_escrow"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order.id", ondelete="CASCADE"), unique=True, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="held", nullable=False)  # held/released/refunded
    created_at: Mapped[datetime] = created()
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthPublisherReport(Base):
    __tablename__ = "growth_publisher_report"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    publisher_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class GrowthBlockedPublisher(Base):
    __tablename__ = "growth_blocked_publisher"
    __table_args__ = (UniqueConstraint("organisation_id", "publisher_org_id", name="uq_growth_block"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    publisher_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


# =========================================================== wallet (76)
class GrowthWalletTxnMeta(Base):
    __tablename__ = "growth_wallet_txn_meta"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    txn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallet_txn.id", ondelete="CASCADE"), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ref: Mapped[str | None] = mapped_column(Text)
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class GrowthWalletAdjustment(Base):  # manual top-up/correction — needs platform-admin approval
    __tablename__ = "growth_wallet_adjustment"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)  # pending/applied/rejected
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_by_email: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_by_email: Mapped[str | None] = mapped_column(Text)
    decision_notes: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    txn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()


class GrowthTopup(Base):  # Stripe Checkout top-ups
    __tablename__ = "growth_topup"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, server_default="usd", nullable=False)
    stripe_session_id: Mapped[str | None] = mapped_column(Text, unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="created", nullable=False)  # created/paid/expired/failed
    txn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthPayout(Base):
    __tablename__ = "growth_payout"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    destination: Mapped[str | None] = mapped_column(Text)  # free-text payout destination note (no bank numbers stored)
    status: Mapped[str] = mapped_column(Text, server_default="pending_approval", nullable=False)
    # pending_approval → approved (transfer NOT CONNECTED, awaiting operator) → paid | rejected (funds released)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    hold_txn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    release_txn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    paid_reference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthInvoice(Base):
    __tablename__ = "growth_invoice"
    __table_args__ = (UniqueConstraint("organisation_id", "number", name="uq_growth_invoice_number"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    number: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # topup/order/credit_note/payout
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, server_default="usd", nullable=False)
    lines: Mapped[list] = jlist()
    ref_type: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stored_file_id: Mapped[uuid.UUID | None] = fk("stored_file.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


# =========================================================== free tools (67–72)
class GrowthToolRun(Base):
    __tablename__ = "growth_tool_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[dict] = jdict()
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED", nullable=False)
    result: Mapped[dict] = jdict()
    error: Mapped[str | None] = mapped_column(Text)
    public: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrowthPublicMonitor(Base):  # module 70 — public backlink monitor registrations
    __tablename__ = "growth_public_monitor"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    manage_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)  # active/stopped
    public: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    last_status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)  # pending/live/nofollow/missing/error
    last_rel: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    history: Mapped[list] = jlist()
    last_email_status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()
