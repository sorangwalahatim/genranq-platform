"""Read models for the project, page, team and executive dashboards (all computed from project data)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select

from .. import models as m
from .. import models_monitor as mm
from . import keywords as kw
from .ingest import path_of
from .monitor import ga_agg, pct, search_agg

TOTAL = ("total", "page", "detail")
PAGE = ("page", "detail")


def anchor(db, pid) -> date | None:
    return db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == pid))


def ga_anchor(db, pid) -> date | None:
    return db.scalar(select(func.max(mm.AnalyticsMetric.date)).where(mm.AnalyticsMetric.project_id == pid))


def _kpi(cur: dict, prev: dict) -> dict:
    out = {}
    for k in ("clicks", "impressions"):
        out[k] = {"value": cur.get(k, 0), "prev": prev.get(k, 0), "change_pct": pct(cur.get(k, 0), prev.get(k, 0))}
    ctr = lambda d: round(100 * d.get("clicks", 0) / d["impressions"], 2) if d.get("impressions") else None  # noqa: E731
    out["ctr"] = {"value": ctr(cur), "prev": ctr(prev)}
    out["position"] = {"value": cur.get("position"), "prev": prev.get("position")}
    return out


def ranges(a: date, days: int):
    cur = (a - timedelta(days=days - 1), a)
    prev = (cur[0] - timedelta(days=days), cur[0] - timedelta(days=1))
    return cur, prev


def overview(db, project, days: int = 28) -> dict:
    pid = project.id
    a = anchor(db, pid)
    out: dict = {"project": {"id": str(pid), "name": project.name}, "anchor": a, "days": days}
    if a:
        cur, prev = ranges(a, days)
        c = search_agg(db, pid, (), cur, TOTAL).get((), {})
        p = search_agg(db, pid, (), prev, TOTAL).get((), {})
        out["search"] = _kpi(c, p)
        series = search_agg(db, pid, ("date",), (a - timedelta(days=89), a), TOTAL)
        out["series"] = [{"date": k[0], **v} for k, v in sorted(series.items())]
        dev_c = search_agg(db, pid, ("device",), cur, TOTAL, mm.SearchMetric.device.is_not(None))
        dev_p = search_agg(db, pid, ("device",), prev, TOTAL, mm.SearchMetric.device.is_not(None))
        out["devices"] = [{"device": k[0], **v, "prev_clicks": dev_p.get(k, {}).get("clicks", 0),
                           "change_pct": pct(v["clicks"], dev_p.get(k, {}).get("clicks", 0))} for k, v in sorted(dev_c.items(), key=lambda x: -x[1]["clicks"])]
        co_c = search_agg(db, pid, ("country",), cur, TOTAL, mm.SearchMetric.country.is_not(None))
        co_p = search_agg(db, pid, ("country",), prev, TOTAL, mm.SearchMetric.country.is_not(None))
        out["countries"] = [{"country": k[0], **v, "prev_clicks": co_p.get(k, {}).get("clicks", 0),
                             "change_pct": pct(v["clicks"], co_p.get(k, {}).get("clicks", 0))}
                            for k, v in sorted(co_c.items(), key=lambda x: -x[1]["clicks"])[:15]]
    ga = ga_anchor(db, pid)
    if ga:
        cur, prev = ranges(ga, days)
        c = ga_agg(db, pid, (), cur).get((), {})
        p = ga_agg(db, pid, (), prev).get((), {})
        out["analytics"] = {k: {"value": c.get(k, 0), "prev": p.get(k, 0), "change_pct": pct(c.get(k, 0), p.get(k, 0))}
                            for k in ("sessions", "users", "engaged_sessions", "conversions", "revenue")}
        gs = ga_agg(db, pid, ("date",), (ga - timedelta(days=89), ga))
        out["analytics_series"] = [{"date": k[0], **v} for k, v in sorted(gs.items())]
        ch = ga_agg(db, pid, ("channel",), cur)
        out["channels"] = [{"channel": k[0] or "(none)", **v} for k, v in sorted(ch.items(), key=lambda x: -x[1].get("sessions", 0))]
    iss = db.execute(select(mm.MonitorIssue.category, mm.MonitorIssue.severity, func.count())
                     .where(mm.MonitorIssue.project_id == pid, mm.MonitorIssue.status.in_(("open", "in_progress", "reopened", "verifying")))
                     .group_by(mm.MonitorIssue.category, mm.MonitorIssue.severity)).all()
    out["open_issues"] = [{"category": c, "severity": s, "count": n} for c, s, n in iss]
    run = db.scalar(select(mm.MonitorRun).where(mm.MonitorRun.project_id == pid).order_by(mm.MonitorRun.started_at.desc()).limit(1))
    out["last_run"] = {"at": run.started_at, "summary": run.summary, "stats": run.stats} if run else None
    out["sources"] = [{"provider": s.provider, "property": s.property, "mode": s.mode, "status": s.last_status, "last_sync_at": s.last_sync_at,
                       "error": s.last_error} for s in db.scalars(select(mm.ProjectSource).where(mm.ProjectSource.project_id == pid))]
    out["content_watch"] = dict(Counter(s for (s,) in db.execute(select(mm.ContentWatch.status).where(mm.ContentWatch.project_id == pid))))
    out["changes_7d"] = db.scalar(select(func.count()).select_from(mm.SiteChange).where(
        mm.SiteChange.project_id == pid, mm.SiteChange.detected_at >= func.now() - timedelta(days=7)))
    return out


def queries(db, project, days: int = 28, q: str | None = None, page: str | None = None, limit: int = 500) -> dict:
    pid = project.id
    a = anchor(db, pid)
    if not a:
        return {"anchor": None, "rows": []}
    cur, prev = ranges(a, days)
    where = mm.SearchMetric.query.is_not(None)
    if page:
        where = where & (mm.SearchMetric.page == page)
    c = search_agg(db, pid, ("query",), cur, ("detail",), where)
    p = search_agg(db, pid, ("query",), prev, ("detail",), where)
    rows = []
    for k in set(c) | set(p):
        qq = k[0]
        if q and q.lower() not in qq:
            continue
        cv, pv = c.get(k, {"clicks": 0, "impressions": 0, "position": None}), p.get(k, {"clicks": 0, "impressions": 0, "position": None})
        status = "new" if not pv["impressions"] and cv["impressions"] else ("lost" if pv["impressions"] and not cv["impressions"] else "")
        d = round(pv["position"] - cv["position"], 1) if pv["position"] and cv["position"] else None
        rows.append({"query": qq, "clicks": cv["clicks"], "impressions": cv["impressions"],
                     "ctr": round(100 * cv["clicks"] / cv["impressions"], 2) if cv["impressions"] else None,
                     "position": cv["position"], "prev_position": pv["position"], "position_change": d,
                     "clicks_change": cv["clicks"] - pv["clicks"], "status": status, "intent": kw.intent(qq)})
    rows.sort(key=lambda r: (-r["impressions"], -r["clicks"]))
    summary = {"total": len(rows), "new": sum(1 for r in rows if r["status"] == "new"), "lost": sum(1 for r in rows if r["status"] == "lost"),
               "up": sum(1 for r in rows if (r["position_change"] or 0) >= 1), "down": sum(1 for r in rows if (r["position_change"] or 0) <= -1),
               "top3": sum(1 for r in rows if r["position"] and r["position"] <= 3), "top10": sum(1 for r in rows if r["position"] and r["position"] <= 10),
               "striking": sum(1 for r in rows if r["position"] and 8 <= r["position"] <= 20)}
    return {"anchor": a, "days": days, "summary": summary, "rows": rows[:limit]}


def pages(db, project, days: int = 28, limit: int = 500) -> dict:
    pid = project.id
    a = anchor(db, pid)
    out = {"anchor": a, "rows": []}
    if not a:
        return out
    cur, prev = ranges(a, days)
    c = search_agg(db, pid, ("page",), cur, PAGE, mm.SearchMetric.page.is_not(None))
    p = search_agg(db, pid, ("page",), prev, PAGE, mm.SearchMetric.page.is_not(None))
    ga = ga_anchor(db, pid)
    gac = {}
    if ga:
        gcur, _ = ranges(ga, days)
        gac = {k[0]: v for k, v in ga_agg(db, pid, ("landing_page",), gcur).items()}
    gap = {}
    if ga:
        _, gprev = ranges(ga, days)
        gap = {k[0]: v for k, v in ga_agg(db, pid, ("landing_page",), gprev).items()}
    iss = Counter(u for (u,) in db.execute(select(mm.MonitorIssue.url).where(
        mm.MonitorIssue.project_id == pid, mm.MonitorIssue.status.in_(("open", "in_progress", "reopened", "verifying")), mm.MonitorIssue.url.is_not(None))))
    nq = dict(db.execute(select(mm.SearchMetric.page, func.count(func.distinct(mm.SearchMetric.query))).where(
        mm.SearchMetric.project_id == pid, mm.SearchMetric.grain == "detail", mm.SearchMetric.date >= cur[0]).group_by(mm.SearchMetric.page)).all())
    for k in set(c) | set(p):
        u = k[0]
        cv, pv = c.get(k, {"clicks": 0, "impressions": 0, "position": None}), p.get(k, {"clicks": 0, "impressions": 0, "position": None})
        g = gac.get(path_of(u)) or gac.get(u) or {}
        gp = gap.get(path_of(u)) or gap.get(u) or {}
        out["rows"].append({"url": u, "path": path_of(u), "clicks": cv["clicks"], "prev_clicks": pv["clicks"], "clicks_change_pct": pct(cv["clicks"], pv["clicks"]),
                            "impressions": cv["impressions"], "ctr": round(100 * cv["clicks"] / cv["impressions"], 2) if cv["impressions"] else None,
                            "position": cv["position"], "prev_position": pv["position"], "queries": nq.get(u, 0),
                            "sessions": g.get("sessions"), "sessions_change_pct": pct(g.get("sessions", 0), gp.get("sessions", 0)) if g else None,
                            "conversions": g.get("conversions"), "open_issues": iss.get(u, 0)})
    out["rows"].sort(key=lambda r: -r["clicks"])
    out["rows"] = out["rows"][:limit]
    return out


def page_detail(db, project, url: str, days: int = 28) -> dict:
    pid = project.id
    a = anchor(db, pid)
    out: dict = {"url": url, "path": path_of(url), "anchor": a}
    if a:
        where = mm.SearchMetric.page == url
        cur, prev = ranges(a, days)
        out["search"] = _kpi(search_agg(db, pid, (), cur, PAGE, where).get((), {}), search_agg(db, pid, (), prev, PAGE, where).get((), {}))
        s = search_agg(db, pid, ("date",), (a - timedelta(days=89), a), PAGE, where)
        out["series"] = [{"date": k[0], **v} for k, v in sorted(s.items())]
        dc = search_agg(db, pid, ("device",), cur, PAGE, where & mm.SearchMetric.device.is_not(None))
        dp = search_agg(db, pid, ("device",), prev, PAGE, where & mm.SearchMetric.device.is_not(None))
        out["devices"] = [{"device": k[0], **v, "change_pct": pct(v["clicks"], dp.get(k, {}).get("clicks", 0))} for k, v in dc.items()]
        out["queries"] = queries(db, project, days, page=url, limit=200)
    ga = ga_anchor(db, pid)
    if ga:
        cur, prev = ranges(ga, days)
        w = mm.AnalyticsMetric.landing_page.in_((url, path_of(url)))
        c, p = ga_agg(db, pid, (), cur, w).get((), {}), ga_agg(db, pid, (), prev, w).get((), {})
        out["analytics"] = {k: {"value": c.get(k, 0), "prev": p.get(k, 0), "change_pct": pct(c.get(k, 0), p.get(k, 0))} for k in ("sessions", "conversions", "engaged_sessions")}
    out["issues"] = [_issue(i) for i in db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == pid, mm.MonitorIssue.url == url)
                                                   .order_by(mm.MonitorIssue.last_seen.desc()).limit(50))]
    out["changes"] = [{"field": c.field, "old": c.old_value, "new": c.new_value, "at": c.detected_at, "impact": c.impact}
                      for c in db.scalars(select(mm.SiteChange).where(mm.SiteChange.project_id == pid, mm.SiteChange.url == url)
                                          .order_by(mm.SiteChange.detected_at.desc()).limit(50))]
    from .agents import _latest_pages
    pg, _ = _latest_pages(db, project)
    p = pg.get(url)
    out["crawl"] = {k: p.get(k) for k in ("status", "title", "meta_description", "h1", "canonical", "noindex", "word_count", "inlinks", "depth",
                                         "schema_types", "questions")} if p else None
    cw = db.scalar(select(mm.ContentWatch).where(mm.ContentWatch.project_id == pid, mm.ContentWatch.url == url))
    out["content_watch"] = {"status": cw.status, "published_at": cw.published_at, "target_keyword": cw.target_keyword, "checkpoints": cw.checkpoints} if cw else None
    out["gap_reports"] = [{"id": str(g.id), "kind": g.kind, "status": g.status, "created_at": g.created_at}
                          for g in db.scalars(select(mm.GapReport).where(mm.GapReport.project_id == pid, mm.GapReport.target_url == url).order_by(mm.GapReport.created_at.desc()).limit(10))]
    return out


def _issue(i: mm.MonitorIssue) -> dict:
    return {"id": str(i.id), "project_id": str(i.project_id), "kind": i.kind, "category": i.category, "categories": i.categories, "severity": i.severity,
            "is_opportunity": i.is_opportunity, "title": i.title, "url": i.url, "query": i.query, "dimension": i.dimension, "status": i.status,
            "recommendation": i.recommendation, "root_cause": i.root_cause, "evidence": i.evidence, "verify": i.verify, "occurrences": i.occurrences,
            "first_seen": i.first_seen, "last_seen": i.last_seen, "resolved_at": i.resolved_at, "task_ids": [str(t) for t in i.task_ids or []],
            "history": i.history}


OPEN = ("open", "in_progress", "reopened", "verifying")


def team_dashboard(db, team: str, project_ids) -> dict:
    """What one team (developer / seo / content / backlink / analytics) has on its plate across projects."""
    projects = {p.id: p.name for p in db.scalars(select(m.Project).where(m.Project.id.in_(project_ids)))}
    issues = db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id.in_(project_ids), mm.MonitorIssue.status.in_(OPEN),
                                                      mm.MonitorIssue.categories.contains([team]))).all()
    members = db.execute(select(mm.ProjectMember.project_id, m.User.id, m.User.name).join(m.User, m.User.id == mm.ProjectMember.user_id)
                         .where(mm.ProjectMember.team == team, mm.ProjectMember.project_id.in_(project_ids))).all()
    uids = {u for _, u, _ in members}
    names = {u: n for _, u, n in members}
    tasks = db.scalars(select(m.Task).where(m.Task.project_id.in_(project_ids), m.Task.status != "done",
                                            m.Task.labels.contains([team]) | (m.Task.workstream.in_(_team_ws(team))))).all()
    by_user = defaultdict(lambda: {"open": 0, "overdue": 0, "critical": 0})
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for t in tasks:
        for u in (t.assignee_ids or []):
            if u in uids or not uids:
                b = by_user[u]
                b["open"] += 1
                b["overdue"] += 1 if t.due_date and t.due_date < now else 0
                b["critical"] += 1 if t.priority == "critical" else 0
    return {"team": team,
            "issues_by_project": [{"project_id": str(pid), "project": projects.get(pid), **dict(Counter(i.severity for i in issues if i.project_id == pid))}
                                  for pid in {i.project_id for i in issues}],
            "issues": [_issue(i) for i in sorted(issues, key=lambda i: ("critical", "high", "medium", "low").index(i.severity))[:200]],
            "tasks_open": len(tasks), "tasks_overdue": sum(1 for t in tasks if t.due_date and t.due_date < now),
            "members": [{"user_id": str(u), "name": names.get(u) or str(u)[:8], **v} for u, v in by_user.items()],
            "tasks": [{"id": str(t.id), "title": t.title, "project": projects.get(t.project_id), "priority": t.priority, "status": t.status,
                       "due_date": t.due_date, "assignee_type": t.assignee_type} for t in sorted(tasks, key=lambda t: (t.due_date is None, t.due_date))[:200]]}


def _team_ws(team):
    return {"developer": ["technical"], "content": ["content", "social", "paid_social"], "backlink": ["off_page"], "analytics": ["analytics"],
            "seo": ["on_page", "aio", "aeo", "geo", "sxo", "local", "strategy"]}.get(team, [])


def executive(db, project_ids) -> dict:
    out = []
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for p in db.scalars(select(m.Project).where(m.Project.id.in_(project_ids), m.Project.is_template.is_(False))):
        a = anchor(db, p.id)
        s = None
        if a:
            cur, prev = ranges(a, 28)
            s = _kpi(search_agg(db, p.id, (), cur, TOTAL).get((), {}), search_agg(db, p.id, (), prev, TOTAL).get((), {}))
        sev = dict(db.execute(select(mm.MonitorIssue.severity, func.count()).where(mm.MonitorIssue.project_id == p.id, mm.MonitorIssue.status.in_(OPEN))
                              .group_by(mm.MonitorIssue.severity)).all())
        t_open = db.scalar(select(func.count()).select_from(m.Task).where(m.Task.project_id == p.id, m.Task.status != "done"))
        t_over = db.scalar(select(func.count()).select_from(m.Task).where(m.Task.project_id == p.id, m.Task.status != "done", m.Task.due_date < now))
        run = db.scalar(select(mm.MonitorRun).where(mm.MonitorRun.project_id == p.id).order_by(mm.MonitorRun.started_at.desc()).limit(1))
        health = 100 - min(100, 25 * sev.get("critical", 0) + 8 * sev.get("high", 0) + 2 * sev.get("medium", 0))
        out.append({"project_id": str(p.id), "project": p.name, "client": p.client_name, "search": s, "issues": sev, "health": health,
                    "tasks_open": t_open, "tasks_overdue": t_over, "briefing": run.summary if run else None, "last_run": run.started_at if run else None})
    out.sort(key=lambda r: r["health"])
    return {"projects": out,
            "totals": {"critical": sum(r["issues"].get("critical", 0) for r in out), "high": sum(r["issues"].get("high", 0) for r in out),
                       "tasks_overdue": sum(r["tasks_overdue"] for r in out),
                       "clicks": sum((r["search"] or {}).get("clicks", {}).get("value", 0) for r in out),
                       "clicks_prev": sum((r["search"] or {}).get("clicks", {}).get("prev", 0) for r in out)}}
