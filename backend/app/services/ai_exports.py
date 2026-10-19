"""Publish-ready exports for studio content: HTML (with JSON-LD), Markdown (front matter + JSON-LD),
PDF (reportlab, with charts for whitepapers) and DOCX (python-docx, for playbooks and long-form)."""
from __future__ import annotations

import io
import re
from datetime import date

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer


def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<link href="\2" color="#2f5bea">\1</link>', s)
    return s


def chart_drawing(ch: dict, width=170 * mm, height=70 * mm, primary="#1F3A5F") -> Drawing:
    d = Drawing(width, height + 14)
    d.add(String(0, height + 4, ch.get("title", ""), fontName="Helvetica-Bold", fontSize=9, fillColor=colors.HexColor("#374151")))
    data = ch.get("data") or []
    series = ch.get("series") or []
    palette = [colors.HexColor(primary), colors.HexColor("#d9480f"), colors.HexColor("#13795b")]
    if ch.get("type") == "line" and data:
        lp = LinePlot()
        lp.x, lp.y, lp.width, lp.height = 30, 20, width - 50, height - 30
        lp.data = [[(i, float(r.get(s) or 0)) for i, r in enumerate(data)] for s in series[:1]]  # one scale: first series
        lp.lines[0].strokeColor = palette[0]
        lp.lines[0].strokeWidth = 1.5
        lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = 0, max(1, len(data) - 1)
        lp.xValueAxis.labelTextFormat = lambda v: data[int(v)]["label"][5:] if 0 <= int(v) < len(data) else ""
        lp.xValueAxis.valueStep = max(1, len(data) // 6)
        lp.xValueAxis.labels.fontSize = lp.yValueAxis.labels.fontSize = 7
        d.add(lp)
        d.add(String(width - 120, height - 4, f"Series: {series[0]}", fontSize=7, fillColor=colors.HexColor("#6B7280")))
    elif data:
        bc = VerticalBarChart()
        bc.x, bc.y, bc.width, bc.height = 30, 30, width - 50, height - 40
        bc.data = [[float(r.get(series[0]) or 0) for r in data]]
        bc.categoryAxis.categoryNames = [str(r["label"])[:14] for r in data]
        bc.categoryAxis.labels.angle = 20
        bc.categoryAxis.labels.fontSize = bc.valueAxis.labels.fontSize = 7
        bc.categoryAxis.labels.dy = -6
        bc.bars[0].fillColor = palette[0]
        bc.valueAxis.valueMin = 0
        d.add(bc)
    return d


def _md_flow(md: str, st: dict) -> list:
    """Very small Markdown → reportlab flowables (headings, bullets, checklists, paragraphs)."""
    out, para = [], []

    def flush():
        if para:
            out.append(Paragraph(_inline(" ".join(para)), st["body"]))
            para.clear()
    for line in (md or "").splitlines():
        s = line.strip()
        if not s:
            flush()
            continue
        h = re.match(r"^(#{1,4})\s+(.+)$", s)
        if h:
            flush()
            lvl = len(h.group(1))
            out.append(Paragraph(_inline(h.group(2)), st["h1" if lvl == 1 else "h2" if lvl == 2 else "h3"]))
            continue
        b = re.match(r"^[-*]\s+(\[[ x]\]\s+)?(.+)$", s)
        n = re.match(r"^\d+[.)]\s+(.+)$", s)
        if b or n:
            flush()
            box = "☐ " if b and b.group(1) else "• "
            out.append(Paragraph(box + _inline(b.group(2) if b else n.group(1)), st["bullet"]))
            continue
        para.append(s)
    flush()
    return out


def styles(primary="#1F3A5F"):
    ss = getSampleStyleSheet()
    p = colors.HexColor(primary)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14, spaceAfter=6)
    return {"title": ParagraphStyle("t", parent=ss["Title"], textColor=p, alignment=0, fontSize=24, leading=28),
            "h1": ParagraphStyle("h1", parent=ss["Heading1"], textColor=p), "h2": ParagraphStyle("h2", parent=ss["Heading2"], textColor=p),
            "h3": ParagraphStyle("h3", parent=ss["Heading3"]), "body": body,
            "bullet": ParagraphStyle("bl", parent=body, leftIndent=12, spaceAfter=3),
            "meta": ParagraphStyle("m", parent=body, textColor=colors.HexColor("#6B7280"), fontSize=9)}


def pdf(title: str, body_md: str, *, brand: str, subtitle: str | None = None, charts: dict | None = None,
        chart_section: str | None = None, primary: str = "#1F3A5F") -> bytes:
    buf = io.BytesIO()
    st = styles(primary)

    def on_page(c, d):
        c.saveState()
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(18 * mm, 10 * mm, brand)
        c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {d.page}")
        c.restoreState()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=title, author=brand)
    flow = [Paragraph(_esc(brand), st["meta"]), Spacer(1, 30 * mm), Paragraph(_esc(title), st["title"])]
    if subtitle:
        flow.append(Paragraph(_esc(subtitle), st["h3"]))
    flow += [Spacer(1, 8 * mm), Paragraph(date.today().strftime("%B %Y"), st["meta"]), PageBreak()]
    md = re.sub(r"^#\s+.+\n", "", body_md or "", count=1)  # the cover carries the H1
    charts = charts or {}
    placed = False
    sections = re.split(r"(?m)^(?=##\s)", md)
    for sec in sections:
        flow += _md_flow(sec, st)
        head = re.match(r"^##\s+(.+)$", sec.strip().splitlines()[0]) if sec.strip() else None
        if charts and not placed and head and chart_section and head.group(1).strip().lower() == chart_section.lower():
            for ch in charts.values():
                flow += [Spacer(1, 4 * mm), KeepTogether([chart_drawing(ch, primary=primary)]), Spacer(1, 4 * mm)]
            placed = True
    if charts and not placed:
        flow.append(Paragraph("Data", st["h2"]))
        for ch in charts.values():
            flow += [KeepTogether([chart_drawing(ch, primary=primary)]), Spacer(1, 4 * mm)]
    doc.build(flow, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def docx(title: str, body_md: str, *, brand: str, subtitle: str | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt
    d = Document()
    d.core_properties.title, d.core_properties.author = title[:250], brand[:250]
    d.add_heading(title, 0)
    if subtitle:
        d.add_paragraph(subtitle).italic = True
    for line in re.sub(r"^#\s+.+\n", "", body_md or "", count=1).splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        h = re.match(r"^(#{2,4})\s+(.+)$", s)
        if h:
            d.add_heading(h.group(2), level=len(h.group(1)) - 1)
            continue
        cb = re.match(r"^\s*[-*]\s+\[( |x)\]\s+(.+)$", s)
        if cb:
            d.add_paragraph(("☑ " if cb.group(1) == "x" else "☐ ") + cb.group(2), style="List Bullet")
            continue
        b = re.match(r"^\s*[-*]\s+(.+)$", s)
        if b:
            d.add_paragraph(b.group(1), style="List Bullet")
            continue
        n = re.match(r"^\s*\d+[.)]\s+(.+)$", s)
        if n:
            d.add_paragraph(n.group(1), style="List Number")
            continue
        p = d.add_paragraph()
        for part in re.split(r"(\*\*.+?\*\*)", s):
            r = p.add_run(part[2:-2] if part.startswith("**") and part.endswith("**") else part)
            r.bold = part.startswith("**") and part.endswith("**")
            r.font.size = Pt(11)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
