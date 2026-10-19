"""Data model additions for the ai modules (tables beyond the spec's Database Schema sheet).

Spec tables (content_item, content_version, ai_provider, prompt, agent, agent_team, agent_run, ai_usage,
topical_map, topic_node, content_plan, content_brief) cannot be altered here, so every extra column lives in
a 1:1 extension table keyed by the spec row's id. All tables are tenant-owned (organisation_id + RLS).
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import date, datetime  # noqa: F401

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text  # noqa: F401
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column  # noqa: F401

from .models import Base, created, fk, org_fk, pk  # noqa: F401

J = JSONB


def _pk_fk(target):
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete="CASCADE"), primary_key=True)


# ------------------------------------------------------------------ AI provider hub (module 50)
class AiProviderPref(Base):
    """Routing preferences for one ai_provider row: order, on/off, default model per task type, provider budget."""
    __tablename__ = "ai_provider_pref"
    ai_provider_id: Mapped[uuid.UUID] = _pk_fk("ai_provider.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)  # claude/openai/gemini/perplexity
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    task_models: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # task_type -> model
    monthly_budget: Mapped[float | None] = mapped_column(Numeric(12, 2))  # USD; null = no provider cap
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiOrgSetting(Base):
    """Organisation-wide AI budget cap (USD per calendar month)."""
    __tablename__ = "ai_org_setting"
    organisation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisation.id", ondelete="CASCADE"), primary_key=True)
    monthly_budget: Mapped[float | None] = mapped_column(Numeric(12, 2))
    alert_pct: Mapped[int] = mapped_column(Integer, server_default="80")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiUsageDetail(Base):
    """1:1 with ai_usage: what the call was for (task type / project / user) — module 54."""
    __tablename__ = "ai_usage_detail"
    ai_usage_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_usage.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(Text, server_default="general")
    kind: Mapped[str] = mapped_column(Text, server_default="text")  # text / image
    units: Mapped[int] = mapped_column(Integer, server_default="0")  # images generated
    project_id: Mapped[uuid.UUID | None] = fk("project.id", nullable=True, ondelete="SET NULL")
    user_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    estimated: Mapped[bool] = mapped_column(Boolean, server_default="false")  # tokens estimated (provider did not report)


# ------------------------------------------------------------------ content (state machine + versions)
class ContentMeta(Base):
    """1:1 with content_item: project generator data (brief, outline, SEO fields, sections, platform…)."""
    __tablename__ = "content_meta"
    content_item_id: Mapped[uuid.UUID] = _pk_fk("content_item.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    generator: Mapped[str | None] = mapped_column(Text)  # blog/news/whitepaper/publication/playbook/web_page/landing/social
    data: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)  # last reviewer notes (rejections)
    current_version: Mapped[int] = mapped_column(Integer, server_default="0")
    approval_id: Mapped[uuid.UUID | None] = fk("approval.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ContentVersionMeta(Base):
    """1:1 with content_version: who/what produced the version and the structured data at that version."""
    __tablename__ = "content_version_meta"
    content_version_id: Mapped[uuid.UUID] = _pk_fk("content_version.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    author_id: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    origin: Mapped[str] = mapped_column(Text, server_default="manual")  # ai/manual/restore/template/agent_team
    model: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))


class ContentTransition(Base):
    """Audit trail of the content state machine."""
    __tablename__ = "content_transition"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    content_item_id: Mapped[uuid.UUID] = fk("content_item.id")
    from_state: Mapped[str] = mapped_column(Text, nullable=False)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # generate/submit/approve/reject/publish/schedule/…
    by_user: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = created()


class NewsSource(Base):
    """User-entered news sources per project (module 25)."""
    __tablename__ = "news_source"
    __table_args__ = (UniqueConstraint("project_id", "url", name="uq_news_source_url"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class ImageAsset(Base):
    """Branded images (Pillow text cards or AI-generated) in a given size (module 32)."""
    __tablename__ = "image_asset"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    content_item_id: Mapped[uuid.UUID | None] = fk("content_item.id", nullable=True, ondelete="SET NULL")
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # text_card / ai
    size_key: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    stored_file_id: Mapped[uuid.UUID | None] = fk("stored_file.id", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ prompt library (module 51)
class PromptVersion(Base):
    """Version history of a custom prompt (the prompt row holds the current body + version)."""
    __tablename__ = "prompt_version"
    __table_args__ = (UniqueConstraint("prompt_id", "version", name="uq_prompt_version"),)
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    prompt_id: Mapped[uuid.UUID] = fk("prompt.id")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ agent teams (module 53)
class AgentTeamRun(Base):
    """One execution of an agent team on a project; each step is an agent_run row."""
    __tablename__ = "agent_team_run"
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    agent_team_id: Mapped[uuid.UUID] = fk("agent_team.id")
    project_id: Mapped[uuid.UUID] = fk("project.id")
    status: Mapped[str] = mapped_column(Text, server_default="QUEUED")  # QUEUED/RUNNING/COMPLETED/PARTIAL/FAILED
    input: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    final_output: Mapped[str | None] = mapped_column(Text)
    content_item_id: Mapped[uuid.UUID | None] = fk("content_item.id", nullable=True, ondelete="SET NULL")
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    cost: Mapped[float] = mapped_column(Numeric(12, 6), server_default="0")
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRunStep(Base):
    """1:1 with agent_run: which team run / step it belongs to."""
    __tablename__ = "agent_run_step"
    agent_run_id: Mapped[uuid.UUID] = _pk_fk("agent_run.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    team_run_id: Mapped[uuid.UUID] = fk("agent_team_run.id")
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentTeamMeta(Base):
    """1:1 with agent_team: description and creator."""
    __tablename__ = "agent_team_meta"
    agent_team_id: Mapped[uuid.UUID] = _pk_fk("agent_team.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    description: Mapped[str | None] = mapped_column(Text)
    output_type: Mapped[str | None] = mapped_column(Text)  # content type the final output becomes (blog/…)
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------ topical map + content plan (modules 61/62)
class TopicalMapMeta(Base):
    __tablename__ = "topical_map_meta"
    topical_map_id: Mapped[uuid.UUID] = _pk_fk("topical_map.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    seeds: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))
    sources: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))  # counts per source
    stats: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()


class TopicNodeMeta(Base):
    __tablename__ = "topic_node_meta"
    topic_node_id: Mapped[uuid.UUID] = _pk_fk("topic_node.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # pillar / cluster
    keywords: Mapped[list] = mapped_column(J, server_default=text("'[]'::jsonb"))  # member keywords with metrics
    impressions: Mapped[int] = mapped_column(Integer, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, server_default="0")
    position: Mapped[float | None] = mapped_column(Numeric(8, 2))
    coverage_status: Mapped[str] = mapped_column(Text, server_default="gap")  # covered / partial / gap
    coverage_url: Mapped[str | None] = mapped_column(Text)
    position_order: Mapped[int] = mapped_column(Integer, server_default="0")


class ContentPlanMeta(Base):
    __tablename__ = "content_plan_meta"
    content_plan_id: Mapped[uuid.UUID] = _pk_fk("content_plan.id")
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict] = mapped_column(J, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = created()
