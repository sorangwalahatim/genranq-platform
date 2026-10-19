"""Global seed data, all derived from the active specification workbook."""
from __future__ import annotations

from sqlalchemy import select

from . import models as m
from .db import system_session
from .services.checks import RECS
from .spec.parser import parse_workbook

SERVICES = ["Link Building", "Guest Post", "Digital PR", "Niche Edits", "Content", "Brand Mentions", "SEO Reseller"]

BUILTIN_PROMPTS = [
    ("Audit fix guidance", "audit", "You are a senior technical SEO. For the issue {{code}} — {{title}} affecting {{count}} URLs on {{domain}}, give concrete fix steps."),
    ("Blog brief", "blog", "Write a content brief for '{{keyword}}' targeting {{audience}} in a {{tone}} voice. Include H2 outline, FAQs and internal links."),
    ("GBP review reply", "gmb", "Reply to this {{rating}}-star Google review for {{business}} politely and specifically: {{review}}"),
    ("Social post", "social", "Write a {{platform}} post about {{topic}} with a hook, 3 short lines and 3 relevant hashtags."),
]
BUILTIN_SKILLS = [
    ("SEO auditor", "Interpret audit findings and propose prioritised fixes.", ["audit.read"], {"type": "object", "properties": {"fixes": {"type": "array"}}}),
    ("Content writer", "Draft long-form content from a brief in the brand voice.", ["web.search"], {"type": "object", "properties": {"draft": {"type": "string"}}}),
]


def seed_global(spec: dict | None = None):
    from .spec.store import invalidate
    spec = spec or parse_workbook()
    with system_session() as db:
        existing = {c.code: c for c in db.scalars(select(m.AuditChecklistItem))}
        for c in spec["audit_checklist"]["checks"]:
            row = existing.get(c["code"])
            vals = dict(category=c["category"], check=c["check"], data_source=c["data_source"], auto=c["auto"],
                        severity=c["severity"], default_owner=c["owner"], recommendation=RECS.get(c["code"]), active=True)
            if row:
                for k, v in vals.items():
                    setattr(row, k, v)
            else:
                db.add(m.AuditChecklistItem(code=c["code"], **vals))
        spec_codes = {c["code"] for c in spec["audit_checklist"]["checks"]}
        for code, row in existing.items():
            if code not in spec_codes:
                row.active = False
        if not db.scalar(select(m.Service).limit(1)):
            for s in SERVICES:
                db.add(m.Service(name=s, type="link" if s in ("Link Building", "Guest Post", "Niche Edits") else "service"))
        if not db.scalar(select(m.Prompt).where(m.Prompt.organisation_id.is_(None)).limit(1)):
            for n, t, b in BUILTIN_PROMPTS:
                db.add(m.Prompt(organisation_id=None, name=n, type=t, body=b, version=1, is_custom=False))
            for n, i, tools, schema in BUILTIN_SKILLS:
                db.add(m.Skill(organisation_id=None, name=n, instructions=i, tools=tools, output_schema=schema, is_custom=False, enabled=True))
        if not db.scalar(select(m.SpecSnapshot).where(m.SpecSnapshot.status == "active")):
            db.add(m.SpecSnapshot(sha256=spec["meta"]["sha256"], source="bundled", spec=spec, status="active"))
    invalidate()
