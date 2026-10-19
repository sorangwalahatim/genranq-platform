"""Client Portal dashboard aggregation.

Builds a Looker-Studio-style, client-safe dashboard from EXISTING tables only.
Rules:
  * never invent numbers — a section with no data returns {"connected": False}
    with a plain-words reason, never zeros dressed up as results;
  * never expose internal data — no costs, no team/performance info, no notes.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import desc, func, select

from .. import models as m
from .. import models_social as sm
from ..security import now

BUCKETS = ["1-3", "4-10", "11-20", "21-50", "51-100", "not ranking"]


def _missing(reason: str) -> dict:
    return {"connected": False, "reason": reason}


def _seo(db, project_id: uuid.UUID, days: int) -> dict:
    kws = db.scalars(select(m.Keyword).where(m.Keyword.project_id == project_id)).all()
    if not kws:
        return _missing("No keywords are being tracked for this project yet.")
    ids = [k.id for k in kws]
    since = now() - timedelta(days=days)
    hist = db.scalars(
        select(m.RankHistory)
        .where(m.RankHistory.keyword_id.in_(ids), m.RankHistory.checked_at >= since)
        .order_by(m.RankHistory.checked_at)
    ).all()
    by_kw: dict = {}
    for r in hist:
        by_kw.setdefault(r.keyword_id, []).append(r)
    positions: list[int] = []
    movers: list[tuple] = []
    for k in kws:
        h = [r for r in by_kw.get(k.id, []) if r.position]
        if not h:
            continue
        cur = h[-1]
        positions.append(cur.position)
        older = next((r for r in reversed(h[:-1]) if (cur.checked_at - r.checked_at).days >= 6), None)
        change = (older.position - cur.position) if older else None
        movers.append((k.phrase, cur.position, change))
    if not positions:
        return _missing("Keywords are tracked but no ranking data has come in yet.")
    avg = round(sum(positions) / len(positions), 1)
    top10 = sum(1 for p in positions if p <= 10)
    dist = {b: 0 for b in BUCKETS}
    for p in positions:
        b = "1-3" if p <= 3 else "4-10" if p <= 10 else "11-20" if p <= 20 else "21-50" if p <= 50 else "51-100"
        dist[b] += 1
    dist["not ranking"] = len(kws) - len(positions)
    trend_map: dict[str, list[int]] = {}
    for r in hist:
        if r.position:
            trend_map.setdefault(r.checked_at.date().isoformat(), []).append(r.position)
    trend = [{"day": d, "avg": round(sum(v) / len(v), 1)} for d, v in sorted(trend_map.items())]
    up = sorted([x for x in movers if x[2] and x[2] > 0], key=lambda x: -x[2])[:5]
    down = sorted([x for x in movers if x[2] and x[2] < 0], key=lambda x: x[2])[:5]
    return {
        "connected": True,
        "keywords": len(kws),
        "ranking": len(positions),
        "avg_position": avg,
        "top10": top10,
        "distribution": dist,
        "trend": trend,
        "movers_up": [{"phrase": p, "position": pos, "change": c} for p, pos, c in up],
        "movers_down": [{"phrase": p, "position": pos, "change": c} for p, pos, c in down],
        "summary": f"You are tracking {len(kws)} keywords. {top10} of them rank in the top 10 on Google, "
                   f"and your average position is {avg}.",
    }


def _backlinks(db, project_id: uuid.UUID) -> dict:
    bls = db.scalars(select(m.Backlink).where(m.Backlink.project_id == project_id)).all()
    if not bls:
        return _missing("No backlinks have been recorded for this project yet.")
    live = sum(1 for b in bls if b.status == "live")
    drs = [float(b.dr) for b in bls if b.dr is not None]
    avg_dr = round(sum(drs) / len(drs), 1) if drs else None
    new_30d = sum(1 for b in bls if b.first_detected and b.first_detected >= now() - timedelta(days=30))
    by_month: dict[str, int] = {}
    for b in bls:
        if b.first_detected:
            by_month[b.first_detected.strftime("%Y-%m")] = by_month.get(b.first_detected.strftime("%Y-%m"), 0) + 1
    months = [{"month": mo, "new": by_month.get(mo, 0)} for mo in sorted(by_month)[-6:]]
    dr_txt = f" Average strength (DR) is {avg_dr}." if avg_dr else ""
    return {
        "connected": True,
        "total": len(bls),
        "live": live,
        "avg_dr": avg_dr,
        "new_30d": new_30d,
        "by_month": months,
        "summary": f"You have {len(bls)} backlinks, {live} of them live.{dr_txt} {new_30d} were added in the last 30 days.",
    }


def _audits(db, project_id: uuid.UUID) -> dict:
    sites = db.scalars(select(m.Site).where(m.Site.project_id == project_id)).all()
    if not sites:
        return _missing("No website has been added to this project yet.")
    rows = []
    for s in sites:
        a = db.scalar(
            select(m.AuditRun)
            .where(m.AuditRun.site_id == s.id, m.AuditRun.status == "COMPLETED")
            .order_by(desc(m.AuditRun.finished_at)).limit(1)
        )
        if a:
            rows.append({"domain": s.domain, "score": float(a.score) if a.score is not None else None,
                         "checked": a.finished_at})
    if not rows:
        return _missing("No completed website health check yet — your agency runs these regularly.")
    best = max((r["score"] for r in rows if r["score"] is not None), default=None)
    return {
        "connected": True,
        "sites": rows,
        "summary": f"Latest website health score is {round(best)}/100." if best is not None else "Health checks are done.",
    }


def _social(db, project_id: uuid.UUID) -> dict:
    accts = db.scalars(select(sm.SocialAccount).where(sm.SocialAccount.project_id == project_id)).all()
    if not accts:
        return _missing("No social media accounts are connected for this project yet.")
    since = now() - timedelta(days=30)
    mets = db.scalars(
        select(sm.SocialMetric)
        .where(sm.SocialMetric.project_id == project_id, sm.SocialMetric.captured_at >= since)
    ).all()
    latest: dict[str, sm.SocialMetric] = {}
    for mt in sorted(mets, key=lambda x: x.captured_at):
        latest[mt.external_post_id] = mt
    by_channel: dict[str, dict] = {}
    for mt in latest.values():
        c = by_channel.setdefault(mt.channel, {"channel": mt.channel, "posts": 0, "impressions": 0, "engagement": 0})
        c["posts"] += 1
        c["impressions"] += mt.impressions or 0
        c["engagement"] += (mt.likes or 0) + (mt.comments or 0) + (mt.shares or 0)
    posts = sum(c["posts"] for c in by_channel.values())
    impr = sum(c["impressions"] for c in by_channel.values())
    eng = sum(c["engagement"] for c in by_channel.values())
    out: dict = {
        "connected": True,
        "accounts": [{"channel": a.channel, "name": a.name, "status": a.status} for a in accts],
        "posts_30d": posts,
        "impressions_30d": impr,
        "engagement_30d": eng,
        "by_channel": sorted(by_channel.values(), key=lambda c: -c["impressions"]),
    }
    out["summary"] = (f"{posts} posts in the last 30 days reached {impr:,} people with {eng:,} likes, comments and shares."
                      if posts else "Accounts are connected — post performance will appear here once publishing starts.")
    return out


def _gmb(db, project_id: uuid.UUID) -> dict:
    locs = db.scalars(select(m.GbpLocation).where(m.GbpLocation.project_id == project_id)).all()
    if not locs:
        return _missing("No Google business locations are connected for this project yet.")
    loc_ids = [l.id for l in locs]
    revs = db.scalars(select(m.GbpReview).where(m.GbpReview.location_id.in_(loc_ids))).all() if loc_ids else []
    ratings = [r.rating for r in revs if r.rating]
    avg = round(sum(ratings) / len(ratings), 1) if ratings else None
    return {
        "connected": True,
        "locations": len(locs),
        "location_names": [l.name for l in locs if l.name],
        "reviews": len(revs),
        "avg_rating": avg,
        "summary": (f"{len(locs)} location(s) with {len(revs)} Google reviews, average rating {avg}/5."
                    if avg else f"{len(locs)} location(s) connected."),
    }


def _work(db, project_id: uuid.UUID) -> dict:
    counts = dict(db.execute(
        select(m.Task.status, func.count()).where(m.Task.project_id == project_id).group_by(m.Task.status)).all())
    total = sum(counts.values())
    done = counts.get("done", 0)
    return {"todo": counts.get("todo", 0), "in_progress": counts.get("in_progress", 0),
            "review": counts.get("review", 0), "done": done, "total": total,
            "summary": f"{done} of {total} tasks finished." if total else "No tasks yet."}


def get_dashboard_data(db, project_id: uuid.UUID, days: int = 90) -> dict:
    """Client-safe dashboard payload. Sections without data say so honestly."""
    return {
        "project_id": str(project_id),
        "generated_at": now(),
        "seo": _seo(db, project_id, days),
        "backlinks": _backlinks(db, project_id),
        "audits": _audits(db, project_id),
        "social": _social(db, project_id),
        "gmb": _gmb(db, project_id),
        "work": _work(db, project_id),
    }
