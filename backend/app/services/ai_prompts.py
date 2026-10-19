"""Prompt library (module 51): embedded prompts + custom prompts with {{variables}} and versioning.

Built-in prompts live in code (BUILTINS) and are mirrored into the spec ``prompt`` table with
organisation_id NULL (readable by every tenant through the builtin_read RLS policy). An organisation
customises one by duplicating it: a custom prompt whose name equals a built-in key replaces that built-in
for the organisation everywhere the studio uses it.
"""
from __future__ import annotations

import re
import threading

from sqlalchemy import select

from .. import models as m
from ..db import system_session

VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

VOICE = """Brand: {{brand}}. Audience: {{audience}}. Tone of voice: {{tone}}. Unique selling proposition: {{usp}}.
Write in that voice. Never invent statistics, quotes, prices or customer names — where a fact is needed and not given, write [source needed]."""

# key: (type, version, description, body)
BUILTINS: dict[str, tuple[str, int, str, str]] = {
    "blog.draft": ("blog", 1, "Full blog article from a brief and outline (Markdown, FAQ section, meta).", f"""You are an expert SEO content writer.
{VOICE}

Write a blog article titled "{{{{title}}}}" targeting the primary keyword "{{{{primary_keyword}}}}" (search intent: {{{{intent}}}}).
Supporting keywords to cover naturally: {{{{supporting_keywords}}}}.
Target length: about {{{{word_count}}}} words.

Follow this outline (H2/H3):
{{{{outline}}}}

Answer these questions in a final "## FAQ" section, each as "### <question>" followed by a 40–60 word answer:
{{{{faqs}}}}

Brief notes: {{{{brief_notes}}}}

Output format — exactly:
META_TITLE: <≤60 characters>
META_DESCRIPTION: <≤155 characters>
then the article in Markdown starting with "# <title>". Put the direct answer to the main question in the first paragraph."""),
    "news.draft": ("news", 1, "News article from a discovered topic and source notes.", f"""You are a news editor.
{VOICE}

Write a timely, factual news article.
Headline: {{{{headline}}}}
Angle: {{{{angle}}}}
Source material (only use facts from here; attribute them): {{{{source_notes}}}}
Search demand behind the topic: {{{{query_data}}}}

Output format — exactly:
META_TITLE: <≤60 characters>
META_DESCRIPTION: <≤155 characters>
then Markdown: "# <headline>", a 1–2 sentence standfirst, the story in short paragraphs with H2s, and "## Sources" listing the source URLs."""),
    "whitepaper.draft": ("whitepaper", 1, "Long-form whitepaper, one Markdown H2 per section, using the project's data.", f"""You are writing an authoritative whitepaper.
{VOICE}

Title: {{{{title}}}}
Audience: {{{{audience}}}}
Write these sections, in this order, each starting with "## <section title>" exactly as given:
{{{{sections}}}}

Data you may cite (from the organisation's own analytics; cite as "our data"):
{{{{data_summary}}}}

Each section 250–500 words. Use subheadings (###), bullet lists and concrete recommendations. Output Markdown only."""),
    "publication.draft": ("publication", 1, "Thought-leadership article built around an E-E-A-T title.", f"""You are ghost-writing a thought-leadership article for {{{{brand}}}}.
{VOICE}

Title: {{{{title}}}}
Subtitle: {{{{subtitle}}}}
E-E-A-T focus: {{{{eeat_focus}}}}
Show first-hand experience: {{{{experience_proof}}}}
Demonstrate expertise: {{{{expertise}}}}
Authority signals to include: {{{{authority}}}}
Trust indicators: {{{{trust}}}}
Verifiable metric to feature (only if real): {{{{metric}}}}

Write 1,000–1,500 words in Markdown starting with "# <title>". Include a short author-bio placeholder line "Author: [name, role]" and a "## Key takeaways" list."""),
    "playbook.draft": ("playbook", 1, "SOP / playbook as JSON steps with owners and checklists.", """You are an operations lead writing a standard operating procedure for {{brand}}.

Playbook: {{title}}
Goal: {{goal}}
Roles available as owners: {{roles}}
Existing steps to keep and improve (may be empty): {{existing_steps}}

Return ONLY a JSON object in a ```json code block with this shape:
{"purpose": str, "scope": str, "steps": [{"title": str, "owner": one of the roles, "details": str, "checklist": [str, ...]}], "kpis": [str]}
Use 5–12 steps, each with 2–6 checklist items that are specific and verifiable."""),
    "web_page.draft": ("web_page", 1, "Landing / service / location page copy as JSON sections.", f"""You are a conversion copywriter and SEO.
{VOICE}

Page type: {{{{page_type}}}}. H1: {{{{h1}}}}. Primary keyword: {{{{primary_keyword}}}} (intent: {{{{intent}}}}).
Service: {{{{service}}}}. Location: {{{{location}}}}. Proof points (only these are real): {{{{proof_points}}}}.
Write copy for these sections (keep the keys): {{{{sections}}}}

Return ONLY a JSON object in a ```json code block: {{"sections": [{{"key": str, "heading": str, "body": str}}], "faqs": [{{"question": str, "answer": str}}]}}
Body text may use short paragraphs and "- " bullets. Location pages must contain genuinely local detail placeholders like [local project] rather than invented facts."""),
    "social.post": ("social", 1, "One social post for a platform within its character limit.", """You write social posts for {{brand}} (tone: {{tone}}).

Platform: {{platform}} — hard limit {{limit}} characters INCLUDING hashtags and the link.
Source: "{{source_title}}"
Summary: {{source_summary}}
Link: {{link}}
Suggested hashtags: {{hashtags}}

Return exactly:
POST: <the post text without hashtags or link>
HASHTAGS: <3–5 hashtags separated by spaces>
Start with a hook. No invented statistics."""),
    "image.prompt": ("image", 1, "Branded image-generation prompt.", """A clean, professional marketing image for "{{title}}" by {{brand}}.
Style: {{style}}. Brand colours: {{colors}}. Composition for a {{size}} canvas with clear negative space for a headline.
No text, no logos, no watermarks, no real people's faces."""),
    "brief.outline": ("brief", 1, "Writer-ready outline from an SEO brief.", """Turn this SEO brief into a writer-ready outline with H2/H3s, key points per section and 5 FAQs. Keep it factual.
Brief:
{{brief}}"""),
    "seo.meta": ("seo", 1, "Title tag + meta description for a page.", """Write an SEO title (≤60 characters) and meta description (≤155 characters) for a page about "{{topic}}" for {{brand}}.
Primary keyword: {{primary_keyword}}. Return exactly:
META_TITLE: ...
META_DESCRIPTION: ..."""),
    "agent.research": ("agent_team", 1, "Agent team step: research notes on a topic.", """You are a research analyst for {{brand}} ({{industry}}).
Topic: {{topic}}
Data gathered so far:
{{previous}}
Produce research notes: key questions searchers ask, facts to verify, angles competitors miss, and sources to consult. Bullet points."""),
    "agent.outline": ("agent_team", 1, "Agent team step: outline from research.", """Create a detailed article outline (H1, H2, H3, key points, FAQ) for "{{topic}}" for {{audience}} from these research notes:
{{previous}}"""),
    "agent.write": ("agent_team", 1, "Agent team step: write the article from the outline.", """Write the full article in Markdown following this outline exactly. Brand voice: {{tone}}. Brand: {{brand}}. USP: {{usp}}.
Never invent statistics — use [source needed].
Outline:
{{previous}}"""),
    "agent.edit": ("agent_team", 1, "Agent team step: edit for clarity and remove AI-sounding filler.", """Edit this draft: tighten sentences, remove generic or AI-sounding filler, fix structure, keep Markdown and all facts. Return the full edited article only.
{{previous}}"""),
    "agent.seo_check": ("agent_team", 1, "Agent team step: SEO check and final polish.", """Act as an SEO editor for the primary topic "{{topic}}". Check the article for: keyword in title/H1/first paragraph, heading structure,
FAQ section, internal-link opportunities, meta title/description. Apply the fixes and return:
META_TITLE: ...
META_DESCRIPTION: ...
then the final Markdown article.
{{previous}}"""),
}

_seeded = {"done": False}
_lock = threading.Lock()


def variables(body: str) -> list[str]:
    return list(dict.fromkeys(VAR_RE.findall(body or "")))


def render(body: str, values: dict, strict: bool = False) -> tuple[str, list[str]]:
    """Fill {{vars}}. Lists are joined with ", ". Returns (text, missing). Missing vars render as '(not set)'
    unless strict, in which case they are left untouched."""
    missing = []

    def val(mt):
        k = mt.group(1)
        v = values.get(k)
        if v is None or v == "" or v == []:
            missing.append(k)
            return mt.group(0) if strict else "(not set)"
        if isinstance(v, (list, tuple)):
            return "\n".join(f"- {x}" for x in v) if any(len(str(x)) > 60 for x in v) else ", ".join(map(str, v))
        if isinstance(v, dict):
            import json
            return json.dumps(v, default=str, ensure_ascii=False)[:20000]
        return str(v)
    out = VAR_RE.sub(val, body or "")
    return out, list(dict.fromkeys(missing))


def ensure_builtins():
    """Mirror BUILTINS into the prompt table (organisation_id NULL). Idempotent; runs once per process."""
    if _seeded["done"]:
        return
    with _lock:
        if _seeded["done"]:
            return
        with system_session() as db:
            have = {p.name: p for p in db.scalars(select(m.Prompt).where(m.Prompt.organisation_id.is_(None)))}
            for key, (typ, ver, _desc, body) in BUILTINS.items():
                p = have.get(key)
                if not p:
                    db.add(m.Prompt(organisation_id=None, name=key, type=typ, body=body, version=ver, is_custom=False))
                elif p.version < ver or p.body != body:
                    p.body, p.type, p.version = body, typ, max(ver, p.version + (0 if p.body == body else 1))
        _seeded["done"] = True


def get_body(db, key: str) -> tuple[str, dict]:
    """Prompt text for a built-in key, honouring the organisation's override (custom prompt with the same name)."""
    ensure_builtins()
    own = db.scalar(select(m.Prompt).where(m.Prompt.name == key, m.Prompt.organisation_id.is_not(None)).limit(1))
    if own:
        return own.body, {"prompt_id": str(own.id), "version": own.version, "custom": True}
    typ, ver, _d, body = BUILTINS[key]
    return body, {"key": key, "version": ver, "custom": False}


def build(db, key: str, values: dict) -> tuple[str, dict]:
    body, info = get_body(db, key)
    text, missing = render(body, values)
    return text, {**info, "missing": missing}
