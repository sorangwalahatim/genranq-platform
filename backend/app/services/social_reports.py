"""Report automation (module 37): scheduled white-label report delivery.

A run builds the selected contents for one project (latest audit PDF/XLSX via services.reports, and a
performance-summary PDF covering monitoring, rank changes and social analytics), stores every file,
then delivers through in-app notifications (always), Microsoft Teams (when a channel is configured)
and email (when SMTP_* is configured — otherwise NOT CONNECTED, the files stay stored and in-app)."""
from __future__ import annotations

import io
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm as MM
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_monitor as mm
from .. import models_social as sm
from . import reports as rep
from . import social as soc
from . import social_teams

CONTENTS = {
    "audit_pdf": "Latest SEO audit (PDF)",
    "audit_xlsx": "Latest SEO audit (Excel)",
    "monitoring": "Monitoring summary",
    "rank_changes": "Rank changes",
    "social": "Social analytics",
}
FREQUENCIES = ("weekly", "monthly")
PERIOD_DAYS = {"weekly": 7, "monthly": 30}
MAX_ATTACH = 20 * 1024 * 1024


# ------------------------------------------------------------------ SMTP
def smtp_settings() -> dict | None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {"host": host, "port": int(os.environ.get("SMTP_PORT", "587")), "user": os.environ.get("SMTP_USER"),
            "password": os.environ.get("SMTP_PASSWORD"), "sender": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER"),
            "security": os.environ.get("SMTP_SECURITY", "starttls").lower()}


def send_email(cfg: dict, to: list[str], subject: str, text: str, attachments: list[tuple[str, str, bytes]]):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["sender"], ", ".join(to), subject
    msg.set_content(text)
    for name, ctype, data in attachments:
        main, _, sub = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
    if cfg["security"] == "ssl":
        s = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=ssl.create_default_context())
    else:
        s = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        if cfg["security"] == "starttls":
            s.starttls(context=ssl.create_default_context())
    try:
        if cfg.get("user"):
            s.login(cfg["user"], cfg.get("password") or "")
        s.send_message(msg)
    finally:
        s.quit()


# ------------------------------------------------------------------ data gathering
def monitoring_data(db: Session, project_id) -> dict:
    run = db.scalar(select(mm.MonitorRun).where(mm.MonitorRun.project_id == project_id).order_by(desc(mm.MonitorRun.started_at)).limit(1))
    counts = dict(db.execute(select(mm.MonitorIssue.severity, func.count()).where(
        mm.MonitorIssue.project_id == project_id, mm.MonitorIssue.status.in_(("open", "reopened", "in_progress"))).group_by(mm.MonitorIssue.severity)).all())
    top = db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == project_id,
                                                   mm.MonitorIssue.status.in_(("open", "reopened", "in_progress")))
                     .order_by(mm.MonitorIssue.severity, desc(mm.MonitorIssue.last_seen)).limit(200)).all()
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    top = sorted(top, key=lambda i: (order.get(i.severity, 9), -i.last_seen.timestamp()))[:10]
    return {"has_run": bool(run), "run_at": run.finished_at or run.started_at if run else None, "summary": run.summary if run else None,
            "open_issues": {k: counts.get(k, 0) for k in order},
            "top_issues": [{"severity": i.severity, "title": i.title, "url": i.url} for i in top]}


def rank_data(db: Session, project_id, days: int) -> dict:
    kws = db.scalars(select(m.Keyword).where(m.Keyword.project_id == project_id)).all()
    now = datetime.now(timezone.utc)
    out = []
    for k in kws:
        h = db.execute(select(m.RankHistory.checked_at, m.RankHistory.position).where(m.RankHistory.keyword_id == k.id,
                                                                                     m.RankHistory.checked_at > now - timedelta(days=days * 3))
                       .order_by(m.RankHistory.checked_at)).all()
        if not h:
            continue
        cur = h[-1]
        prev = next((x for x in reversed(h) if x.checked_at <= now - timedelta(days=days)), None)
        change = (prev.position - cur.position) if (prev and prev.position and cur.position) else None
        out.append({"phrase": k.phrase, "position": cur.position, "previous": prev.position if prev else None, "change": change})
    pos = [x["position"] for x in out if x["position"]]
    movers = sorted([x for x in out if x["change"]], key=lambda x: -abs(x["change"]))
    return {"tracked": len(kws), "with_data": len(out), "avg_position": round(sum(pos) / len(pos), 1) if pos else None,
            "top10": sum(1 for p in pos if p <= 10), "improved": sum(1 for x in out if (x["change"] or 0) > 0),
            "declined": sum(1 for x in out if (x["change"] or 0) < 0), "movers": movers[:15]}


def social_data(db: Session, project_id, days: int) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pubs = db.execute(select(m.PublishJob.status, func.count()).join(sm.SocialPublish, sm.SocialPublish.publish_job_id == m.PublishJob.id)
                      .where(sm.SocialPublish.project_id == project_id, sm.SocialPublish.created_at >= since)
                      .group_by(m.PublishJob.status)).all()
    snaps = soc.latest_snapshots(db, [project_id], since=since)
    agg = soc.aggregate(snaps, {}, {})
    return {"published": dict(pubs).get("PUBLISHED", 0), "failed": dict(pubs).get("FAILED", 0), "totals": agg["totals"],
            "by_channel": agg["by_channel"], "top_posts": agg["top_posts"][:5]}


def latest_audit(db: Session, project_id) -> m.AuditRun | None:
    return db.scalar(select(m.AuditRun).join(m.Site, m.Site.id == m.AuditRun.site_id).where(
        m.Site.project_id == project_id, m.AuditRun.status == "COMPLETED", m.AuditRun.competitor_id.is_(None))
        .order_by(desc(m.AuditRun.finished_at)).limit(1))


# ------------------------------------------------------------------ summary PDF
def _esc(s):
    return str(s if s is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_summary_pdf(org: m.Organisation, project: m.Project, period_days: int, sections: dict, white_label: bool) -> bytes:
    b = org.branding or {}
    brand = b.get("company_name") or org.name
    primary = colors.HexColor(b.get("primary_color") or "#1F3A5F")
    footer = brand if white_label else f"{brand} · Generated with Genranq"
    buf = io.BytesIO()

    def on_page(c, d):
        c.saveState()
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(18 * MM, 10 * MM, footer)
        c.drawRightString(A4[0] - 18 * MM, 10 * MM, f"Page {d.page}")
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * MM, rightMargin=18 * MM, topMargin=18 * MM, bottomMargin=18 * MM,
                            title=f"Performance summary — {project.name}", author=brand)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=primary, alignment=0, fontSize=20)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=primary)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    tbl = TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                      ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    now = datetime.now(timezone.utc)
    st = [Paragraph(_esc(brand), ParagraphStyle("brand", parent=body, textColor=primary, fontSize=11)),
          Paragraph(f"Performance summary — {_esc(project.name)}", h1),
          Paragraph(f"Period: {(now - timedelta(days=period_days)).strftime('%d %b %Y')} – {now.strftime('%d %b %Y')}"
                    + (f" · Client: {_esc(project.client_name)}" if project.client_name else ""), body), Spacer(1, 5 * MM)]
    if "monitoring" in sections:
        d = sections["monitoring"]
        st.append(Paragraph("Monitoring", h2))
        if not d["has_run"]:
            st.append(Paragraph("No monitoring run yet for this project — connect Search Console / Analytics or import data.", body))
        else:
            if d["summary"]:
                st.append(Paragraph(_esc(d["summary"]).replace("\n", "<br/>"), body))
            oi = d["open_issues"]
            t = Table([["Critical", "High", "Medium", "Low"], [oi["critical"], oi["high"], oi["medium"], oi["low"]]], colWidths=[30 * MM] * 4)
            t.setStyle(tbl)
            st += [Spacer(1, 2 * MM), t]
            if d["top_issues"]:
                rows = [["Severity", "Issue", "URL"]] + [[i["severity"], Paragraph(_esc(i["title"]), body), Paragraph(_esc(i["url"] or ""), body)]
                                                         for i in d["top_issues"]]
                t = Table(rows, colWidths=[20 * MM, 90 * MM, 64 * MM], repeatRows=1)
                t.setStyle(tbl)
                st += [Spacer(1, 3 * MM), t]
        st.append(Spacer(1, 5 * MM))
    if "rank_changes" in sections:
        d = sections["rank_changes"]
        st.append(Paragraph("Rank changes", h2))
        if not d["with_data"]:
            st.append(Paragraph(f"{d['tracked']} keywords tracked; no position data recorded yet.", body))
        else:
            st.append(Paragraph(f"{d['tracked']} keywords tracked · average position {d['avg_position'] or '—'} · {d['top10']} in the top 10 · "
                                f"{d['improved']} improved, {d['declined']} declined over the period.", body))
            if d["movers"]:
                rows = [["Keyword", "Position", "Previous", "Change"]] + [[Paragraph(_esc(x["phrase"]), body), x["position"] or "—",
                                                                          x["previous"] or "—", f"{x['change']:+d}"] for x in d["movers"]]
                t = Table(rows, colWidths=[100 * MM, 24 * MM, 24 * MM, 24 * MM], repeatRows=1)
                t.setStyle(tbl)
                st += [Spacer(1, 2 * MM), t]
        st.append(Spacer(1, 5 * MM))
    if "social" in sections:
        d = sections["social"]
        st.append(Paragraph("Social media", h2))
        tt = d["totals"]
        st.append(Paragraph(f"{d['published']} posts published, {d['failed']} failed. Engagement on {tt['posts']} measured posts: "
                            f"{tt['likes']} likes, {tt['comments']} comments, {tt['shares']} shares, {tt['clicks']} clicks, "
                            f"{tt['impressions']} impressions" + (f" (engagement rate {tt['engagement_rate']}%)." if tt["engagement_rate"] is not None else "."), body))
        if d["by_channel"]:
            rows = [["Channel", "Posts", "Likes", "Comments", "Shares", "Clicks", "Impressions"]] + [
                [c["channel"], c["posts"], c["likes"], c["comments"], c["shares"], c["clicks"], c["impressions"]] for c in d["by_channel"]]
            t = Table(rows, repeatRows=1)
            t.setStyle(tbl)
            st += [Spacer(1, 2 * MM), t]
    doc.build(st, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


# ------------------------------------------------------------------ run + deliver
def run(db: Session, org_id, rs: sm.ReportSchedule, triggered_by: str = "manual", user_id=None) -> sm.ReportDelivery:
    project = db.get(m.Project, rs.project_id)
    org = db.get(m.Organisation, org_id)
    days = PERIOD_DAYS.get(rs.frequency, 7)
    d = sm.ReportDelivery(organisation_id=org_id, report_schedule_id=rs.id, project_id=rs.project_id, triggered_by=triggered_by)
    db.add(d)
    db.flush()
    files, notes, attachments = [], [], []
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = "".join(ch if ch.isalnum() else "-" for ch in project.name.lower()).strip("-")[:40] or "project"
    try:
        audit = latest_audit(db, rs.project_id) if {"audit_pdf", "audit_xlsx"} & set(rs.contents) else None
        for kind, fmt, builder, ctype in (("audit_pdf", "pdf", rep.build_pdf, "application/pdf"),
                                          ("audit_xlsx", "xlsx", rep.build_xlsx,
                                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")):
            if kind not in rs.contents:
                continue
            if not audit:
                notes.append("No completed SEO audit for this project yet — audit file skipped.")
                continue
            data = builder(db, audit.id, rs.white_label)
            f = rep.store(db, org_id, f"seo-audit-{slug}-{stamp}.{fmt}", data, ctype, user_id)
            db.add(m.Report(organisation_id=org_id, audit_run_id=audit.id, format=fmt, stored_file_id=f.id,
                            white_label=rs.white_label, status="COMPLETED"))
            files.append({"stored_file_id": str(f.id), "filename": f.filename, "kind": kind, "size_bytes": f.size_bytes})
            attachments.append((f.filename, ctype, data))
        sections = {}
        if "monitoring" in rs.contents:
            sections["monitoring"] = monitoring_data(db, rs.project_id)
        if "rank_changes" in rs.contents:
            sections["rank_changes"] = rank_data(db, rs.project_id, days)
        if "social" in rs.contents:
            sections["social"] = social_data(db, rs.project_id, days)
        if sections:
            data = build_summary_pdf(org, project, days, sections, rs.white_label)
            f = rep.store(db, org_id, f"performance-summary-{slug}-{stamp}.pdf", data, "application/pdf", user_id)
            files.append({"stored_file_id": str(f.id), "filename": f.filename, "kind": "summary", "size_bytes": f.size_bytes})
            attachments.append((f.filename, "application/pdf", data))
        d.summary = {"notes": notes, "period_days": days,
                     **({"open_issues": sections["monitoring"]["open_issues"]} if "monitoring" in sections else {}),
                     **({"rank": {k: sections["rank_changes"][k] for k in ("tracked", "avg_position", "improved", "declined")}}
                        if "rank_changes" in sections else {}),
                     **({"social": sections["social"]["totals"]} if "social" in sections else {})}
    except Exception as e:  # build failure: record it, still tell people in-app
        d.status, d.error, d.files = "failed", f"{type(e).__name__}: {e}"[:1000], files
        d.finished_at = datetime.now(timezone.utc)
        db.add(m.Notification(organisation_id=org_id, kind="report_failed", title=f"Scheduled report failed: {rs.name}",
                              body=d.error[:300], link="/reports/automation"))
        return d
    d.files = files
    title = f"Report ready: {rs.name} — {project.name}"
    text = f"{len(files)} file(s) for {project.name}." + (" " + " ".join(notes) if notes else "")
    link = f"/reports/automation?delivery={d.id}"
    ch = {}
    want = rs.channels or {}
    # in-app (always)
    users = list(rs.recipient_user_ids or [])
    if users:
        for u in users:
            db.add(m.Notification(organisation_id=org_id, user_id=u, kind="report_ready", title=title[:200], body=text[:300], link=link))
    else:
        db.add(m.Notification(organisation_id=org_id, kind="report_ready", title=title[:200], body=text[:300], link=link))
    ch["in_app"] = {"status": "sent", "recipients": len(users) or "all"}
    if want.get("teams"):
        res = social_teams.dispatch(db, org_id, "report_ready", title, text, link=link, project_id=rs.project_id,
                                    facts={"Files": ", ".join(f["filename"] for f in files) or "none"}, dedupe_key=f"report:{d.id}")
        if not res:
            ch["teams"] = {"status": "not_connected", "error": "NOT CONNECTED: no Teams channel subscribed to 'Reports ready' for this project"}
        else:
            ok = all(r["status"] == "sent" for r in res)
            ch["teams"] = {"status": "sent" if ok else ("not_connected" if all(r["status"] == "not_connected" for r in res) else "failed"),
                           "results": res}
    if want.get("email"):
        emails = sorted({*(rs.recipient_emails or []), *[u.email for u in db.scalars(select(m.User).where(m.User.id.in_(users), m.User.is_active))]})
        cfg = smtp_settings()
        if not cfg:
            ch["email"] = {"status": "not_connected", "error": "NOT CONNECTED: email delivery needs SMTP_HOST / SMTP_FROM configured by the operator"}
        elif not emails:
            ch["email"] = {"status": "failed", "error": "No email recipients"}
        else:
            att = attachments if sum(len(a[2]) for a in attachments) <= MAX_ATTACH else []
            body = text + ("\n\nFiles are attached." if att else "\n\nFiles are too large to attach; download them in the app.")
            try:
                send_email(cfg, emails, title, body, att)
                ch["email"] = {"status": "sent", "recipients": len(emails)}
            except (smtplib.SMTPException, OSError) as e:
                ch["email"] = {"status": "failed", "error": str(e)[:300]}
    d.channels = ch
    requested = [k for k in ("teams", "email") if want.get(k)]
    d.status = "delivered" if all(ch[k]["status"] == "sent" for k in requested) else "partial"
    d.finished_at = datetime.now(timezone.utc)
    rs.last_run_at = d.finished_at
    return d
