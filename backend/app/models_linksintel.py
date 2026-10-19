"""Links Intel module tables: anchor-wise backlink stats + internal link map.

Project-wise, tenant-owned (organisation_id + project_id on every row; RLS automatic).
These are analysis tables for the Links Intel UI; existing backlink tables in
models_backlinks.py are untouched.
"""
from __future__ import annotations

import uuid  # noqa: F401
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, created, fk, org_fk, pk

DR_BUCKETS = ["0-20", "20-40", "40-60", "60+"]
ANCHOR_TYPES = ["branded", "exact", "partial", "generic"]
TARGET_TYPES = ["service", "blog", "product", "other"]


class AnchorStat(Base):
    """Aggregated backlink counts per (project, site, anchor text, DR bucket)."""
    __tablename__ = "linksintel_anchor_stat"
    __table_args__ = (
        UniqueConstraint("project_id", "site_label", "anchor_text", "dr_bucket",
                         name="uq_linksintel_anchor_stat"),
    )
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    site_label: Mapped[str] = mapped_column(Text, nullable=False)  # "ours" or a competitor domain
    anchor_text: Mapped[str] = mapped_column(Text, nullable=False)
    backlink_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    dr_bucket: Mapped[str] = mapped_column(Text, nullable=False)  # 0-20 / 20-40 / 40-60 / 60+
    anchor_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="partial")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InternalLink(Base):
    """Internal link map: which page links to which, with the exact anchor word."""
    __tablename__ = "linksintel_internal_link"
    __table_args__ = (
        UniqueConstraint("project_id", "site_label", "source_url", "anchor_text", "target_url",
                         name="uq_linksintel_internal_link"),
    )
    id: Mapped[uuid.UUID] = pk()
    organisation_id: Mapped[uuid.UUID] = org_fk()
    project_id: Mapped[uuid.UUID] = fk("project.id")
    site_label: Mapped[str] = mapped_column(Text, nullable=False)  # "ours" or a competitor domain
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="other")  # service/blog/product/other
    created_at: Mapped[datetime] = created()
