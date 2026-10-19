"""Per-user access rights on top of roles: which projects and which modules a user may use."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, fk, org_fk


class UserAccess(Base):
    """No row = full access allowed by the role. all_projects=False → only project_ids.
    modules=None → every module; otherwise only the listed module keys (see app/access.py)."""
    __tablename__ = "user_access"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    organisation_id: Mapped[uuid.UUID] = org_fk()
    all_projects: Mapped[bool] = mapped_column(Boolean, server_default="true")
    project_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    modules: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = fk("user.id", nullable=True, ondelete="SET NULL")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
