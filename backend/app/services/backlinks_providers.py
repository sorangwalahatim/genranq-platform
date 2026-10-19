"""Ahrefs (module 9) and Semrush (module 10) project ingest.

API mode (provider connected in the Integrations Hub): reuses ingest.sync_ahrefs / ingest.sync_semrush for
domain metrics, referring domains and organic positions, then adds the provider's backlink list and keyword
difficulty. Import mode: the providers' CSV exports (backlinks, referring domains, organic keywords,
Semrush Backlink Audit toxic scores). Shared importers in services/ingest.py are called, never duplicated.

Semrush's API exposes no toxicity score (Backlink Audit is UI/CSV only), so toxic scores are always imported
from the Backlink Audit export.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_backlinks as mb
from .. import models_monitor as mm
from . import ingest
from . import integrations as ih
from .backlinks import _date, host_of, num, reader, to_utf8
from .integrations import IntegrationError, _ok, http_call

NAMES = {"ahrefs": "Ahrefs", "semrush": "Semrush"}

EXT_COLS = {
    "source_url": ["referringpageurl", "referringpage", "sourceurl", "source", "urlfrom", "url"],
    "target_url": ["targeturl", "target", "urlto", "linkurl"],
    "anchor": ["anchor", "anchortext"],
    "authority": ["domainrating", "dr", "pageascore", "ascore", "authorityscore", "ur"],
    "first_seen": ["firstseen", "firstseenlink"],
    "last_seen": ["lastseen", "lastvisited", "lastcheck", "lastchecked"],
    "nofollow": ["nofollow", "isnofollow"],
    "type": ["linktype", "type", "rel", "follow"],
}
KD_COLS = {"keyword": ["keyword", "query", "phrase"], "difficulty": ["kd", "keyworddifficulty", "difficulty", "kdpercent", "kd%"],
           "volume": ["volume", "searchvolume"], "cpc": ["cpc", "cpcusd", "costperclick"]}


def connected(db: Session, provider: str) -> bool:
    return ih.is_connected(db, provider)


def not_connected_msg(provider: str) -> str:
    return f"{NAMES[provider]} is NOT CONNECTED — connect it in Integrations to sync automatically, or import CSV exports."


def get_source(db: Session, pid, provider: str) -> mm.ProjectSource | None:
    return db.scalar(select(mm.ProjectSource).where(mm.ProjectSource.project_id == pid, mm.ProjectSource.provider == provider))


def set_source(db: Session, org_id, pid, provider: str, domain: str, config: dict) -> mm.ProjectSource:
    s = get_source(db, pid, provider)
    if not s:
        s = mm.ProjectSource(organisation_id=org_id, project_id=pid, provider=provider)
        db.add(s)
    s.property = host_of(domain) if "/" in domain or ":" in domain else domain.lower().removeprefix("www.")
    s.config = {**(s.config or {}), **{k: v for k, v in config.items() if v not in (None, "")}}
    s.mode, s.enabled = "api", True
    db.flush()
    return s


def summary(db: Session, project: m.Project, provider: str) -> dict:
    pid = project.id
    src = get_source(db, pid, provider)
    conn = db.scalar(select(m.IntegrationConnection).where(m.IntegrationConnection.provider_key == provider))
    metrics = [{"date": x.date, "domain": x.domain, "refdomains": x.refdomains, "backlinks": x.backlinks,
                "domain_rating": float(x.domain_rating) if x.domain_rating is not None else None, "source": x.source}
               for x in db.scalars(select(mm.LinkMetric).where(mm.LinkMetric.project_id == pid, mm.LinkMetric.source == provider)
                                   .order_by(mm.LinkMetric.date))]
    snap = db.scalar(select(func.max(mm.RefdomainSnapshot.snapshot_date)).where(mm.RefdomainSnapshot.project_id == pid,
                                                                               mm.RefdomainSnapshot.source == provider,
                                                                               mm.RefdomainSnapshot.target == "own"))
    refdomains = [{"domain": r.domain, "domain_rating": float(r.domain_rating) if r.domain_rating is not None else None}
                  for r in db.scalars(select(mm.RefdomainSnapshot).where(mm.RefdomainSnapshot.project_id == pid, mm.RefdomainSnapshot.source == provider,
                                                                         mm.RefdomainSnapshot.target == "own", mm.RefdomainSnapshot.snapshot_date == snap)
                                      .order_by(mm.RefdomainSnapshot.domain_rating.desc().nullslast()).limit(300))] if snap else []
    comp_ref = db.execute(select(mm.RefdomainSnapshot.target, func.count()).where(mm.RefdomainSnapshot.project_id == pid,
                                                                                mm.RefdomainSnapshot.source == provider,
                                                                                mm.RefdomainSnapshot.target != "own")
                          .group_by(mm.RefdomainSnapshot.target)).all()
    backlinks = [{"id": b.id, "source_url": b.source_url, "source_domain": b.source_domain, "target_url": b.target_url, "anchor": b.anchor,
                  "rel": b.rel, "authority": float(b.authority) if b.authority is not None else None, "first_seen": b.first_seen,
                  "last_seen": b.last_seen, "via": b.via}
                 for b in db.scalars(select(mb.ExtBacklink).where(mb.ExtBacklink.project_id == pid, mb.ExtBacklink.provider == provider)
                                     .order_by(mb.ExtBacklink.authority.desc().nullslast()).limit(500))]
    in_crm = {(host_of(b.source_url), b.source_url) for b in db.scalars(select(m.Backlink).where(m.Backlink.project_id == pid))}
    for b in backlinks:
        b["in_crm"] = (b["source_domain"], b["source_url"]) in in_crm
    kws = db.execute(select(mm.CompetitorKeyword.domain, mm.CompetitorKeyword.is_own, func.count(), func.avg(mm.CompetitorKeyword.position))
                     .where(mm.CompetitorKeyword.project_id == pid, mm.CompetitorKeyword.source == provider)
                     .group_by(mm.CompetitorKeyword.domain, mm.CompetitorKeyword.is_own)).all()
    own_kw = [{"keyword": k.keyword, "position": k.position, "url": k.url, "volume": k.volume}
              for k in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == pid, mm.CompetitorKeyword.source == provider,
                                                                     mm.CompetitorKeyword.is_own).order_by(mm.CompetitorKeyword.position.nullslast()).limit(300))]
    kd = {r.keyword: float(r.difficulty) for r in db.scalars(select(mb.KeywordMetric).where(mb.KeywordMetric.project_id == pid,
                                                                                           mb.KeywordMetric.source == provider)) if r.difficulty is not None}
    for k in own_kw:
        k["difficulty"] = kd.get(k["keyword"])
    out = {
        "provider": provider, "connected": bool(conn and conn.status in ("CONNECTED", "RATE_LIMITED")),
        "connection_status": conn.status if conn else "NOT_CONNECTED",
        "source": {"domain": src.property, "config": src.config, "last_sync_at": src.last_sync_at, "last_status": src.last_status,
                   "last_error": src.last_error, "rows_last_sync": src.rows_last_sync} if src else None,
        "metrics": metrics, "refdomains_snapshot": snap, "refdomains": refdomains,
        "competitor_refdomains": [{"domain": t, "refdomains": n} for t, n in comp_ref],
        "backlinks": backlinks, "keyword_sets": [{"domain": d, "is_own": o, "keywords": n, "avg_position": round(float(a), 1) if a else None}
                                                 for d, o, n, a in kws],
        "own_keywords": own_kw, "difficulty_rows": len(kd),
    }
    if not out["connected"]:
        out["message"] = not_connected_msg(provider)
    if provider == "semrush":
        toxic = db.scalars(select(mb.BacklinkToxic).where(mb.BacklinkToxic.project_id == pid).order_by(mb.BacklinkToxic.score.desc()).limit(300)).all()
        out["toxic"] = [{"id": str(t.id), "source_url": t.source_url, "source_domain": t.source_domain, "score": float(t.score),
                         "backlink_id": str(t.backlink_id) if t.backlink_id else None, "markers": t.markers, "imported_at": t.imported_at} for t in toxic]
        out["toxic_note"] = "Semrush's API does not expose Backlink Audit toxicity scores; import the Backlink Audit CSV export."
    return out


# ------------------------------------------------------------------ CSV import
def import_ext_backlinks(db: Session, org_id, pid, content: bytes, provider: str, via: str = "csv") -> dict:
    rdr, cols = reader(content, EXT_COLS)
    if "source_url" not in cols:
        raise HTTPException(400, "Backlinks export needs a Referring page URL / Source url column")
    db.execute(delete(mb.ExtBacklink).where(mb.ExtBacklink.project_id == pid, mb.ExtBacklink.provider == provider))
    rows = []
    for r in rdr:
        g = lambda k: (r.get(cols[k]) or "").strip() if k in cols else ""  # noqa: E731
        src = g("source_url")
        if not src.lower().startswith(("http://", "https://")):
            continue
        rel = None
        nf = g("nofollow").lower()
        t = g("type").lower()
        if t:
            rel = "sponsored" if "sponsored" in t else "ugc" if "ugc" in t else "nofollow" if "nofollow" in t else "dofollow"
        elif nf:
            rel = "nofollow" if nf in ("true", "1", "yes") else "dofollow"
        rows.append(dict(organisation_id=org_id, project_id=pid, provider=provider, source_url=src[:2000], source_domain=host_of(src),
                         target_url=g("target_url")[:2000] or None, anchor=g("anchor")[:500] or None, rel=rel, authority=num(g("authority")),
                         first_seen=_date(g("first_seen")), last_seen=_date(g("last_seen")), via=via))
        if len(rows) >= 50000:
            break
    if not rows:
        raise HTTPException(400, "No usable backlink rows found")
    for i in range(0, len(rows), 2000):
        db.execute(mb.ExtBacklink.__table__.insert(), rows[i:i + 2000])
    return {"rows": len(rows), "domains": len({r["source_domain"] for r in rows})}


def upsert_difficulty(db: Session, org_id, pid, rows: list[dict], source: str) -> int:
    rows = [r for r in rows if r.get("keyword") and (r.get("difficulty") is not None or r.get("volume") is not None)]
    if not rows:
        return 0
    seen = {}
    for r in rows:
        seen[r["keyword"]] = r
    stmt = pg_insert(mb.KeywordMetric).values([dict(organisation_id=org_id, project_id=pid, keyword=k, difficulty=r.get("difficulty"),
                                                    volume=r.get("volume"), cpc=r.get("cpc"), source=source) for k, r in seen.items()])
    stmt = stmt.on_conflict_do_update(constraint="uq_keyword_metric", set_={"difficulty": func.coalesce(stmt.excluded.difficulty, mb.KeywordMetric.difficulty),
                                                                           "volume": func.coalesce(stmt.excluded.volume, mb.KeywordMetric.volume),
                                                                           "cpc": func.coalesce(stmt.excluded.cpc, mb.KeywordMetric.cpc),
                                                                           "updated_at": func.now()})
    db.execute(stmt)
    return len(seen)


def import_keywords(db: Session, org_id, pid, content: bytes, domain: str, is_own: bool, provider: str) -> dict:
    content = to_utf8(content)
    try:
        res = ingest.import_keywords_csv(db, org_id, pid, content, domain, is_own, provider)
    except ingest.ImportError_ as e:
        raise HTTPException(400, str(e))
    rdr, cols = reader(content, KD_COLS)
    kd = []
    if "keyword" in cols and ("difficulty" in cols or "cpc" in cols):
        for r in rdr:
            k = (r.get(cols["keyword"]) or "").strip().lower()
            d = num(r.get(cols["difficulty"])) if "difficulty" in cols else None
            kd.append({"keyword": k, "difficulty": d if d is None or 0 <= d <= 100 else None,
                       "volume": int(num(r.get(cols["volume"])) or 0) if "volume" in cols and num(r.get(cols["volume"])) is not None else None,
                       "cpc": num(r.get(cols["cpc"])) if "cpc" in cols else None})
    res["difficulty_rows"] = upsert_difficulty(db, org_id, pid, kd, provider)
    return res


def import_refdomains(db: Session, org_id, pid, content: bytes, domain: str, provider: str, target: str = "own") -> dict:
    try:
        return ingest.import_refdomains_csv(db, org_id, pid, to_utf8(content), domain, provider, target=target)
    except ingest.ImportError_ as e:
        raise HTTPException(400, str(e))


def mark_import(db: Session, org_id, pid, provider: str, rows: int):
    s = get_source(db, pid, provider)
    if not s:
        s = mm.ProjectSource(organisation_id=org_id, project_id=pid, provider=provider, mode="import")
        db.add(s)
    from datetime import datetime, timezone
    s.last_sync_at, s.last_status, s.last_error, s.rows_last_sync = datetime.now(timezone.utc), "ok", None, rows


# ------------------------------------------------------------------ API sync (only when connected)
def _rows_csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def ahrefs_sync(db: Session, org_id, src: mm.ProjectSource) -> dict:
    res = ingest.sync_ahrefs(db, org_id, src)  # domain metrics + referring domains (shared)
    cr = ih.credentials(db, org_id, "ahrefs")
    h = {"Authorization": f"Bearer {cr['secret']}"}
    ih.take_token(db, org_id, "ahrefs")
    bl = _ok(http_call("GET", "https://api.ahrefs.com/v3/site-explorer/all-backlinks", headers=h, params={
        "target": src.property, "mode": "subdomains", "limit": 1000, "aggregation": "similar", "history": "live",
        "select": "url_from,url_to,anchor,domain_rating_source,first_seen,last_visited,is_dofollow,is_ugc,is_sponsored"}), "Ahrefs backlinks").json()
    rows = []
    for x in bl.get("backlinks", []):
        rel = "sponsored" if x.get("is_sponsored") else "ugc" if x.get("is_ugc") else "dofollow" if x.get("is_dofollow", True) else "nofollow"
        rows.append([x.get("url_from"), x.get("url_to"), x.get("anchor"), x.get("domain_rating_source"), (x.get("first_seen") or "")[:10],
                     (x.get("last_visited") or "")[:10], rel])
    res["backlinks"] = import_ext_backlinks(db, org_id, src.project_id, _rows_csv(
        ["Referring page URL", "Target URL", "Anchor", "Domain rating", "First seen", "Last seen", "Link type"], rows), "ahrefs", via="api")["rows"] if rows else 0
    ih.take_token(db, org_id, "ahrefs")
    kw = _ok(http_call("GET", "https://api.ahrefs.com/v3/site-explorer/organic-keywords", headers=h, params={
        "target": src.property, "mode": "subdomains", "country": (src.config or {}).get("country", "us"), "date": str(date.today()),
        "limit": 1000, "select": "keyword,best_position,best_position_url,volume,keyword_difficulty"}), "Ahrefs organic keywords").json()
    krows = [[k.get("keyword"), k.get("best_position"), k.get("best_position_url"), k.get("volume"), k.get("keyword_difficulty")]
             for k in kw.get("keywords", []) if k.get("keyword")]
    res["organic_keywords"] = import_keywords(db, org_id, src.project_id, _rows_csv(["Keyword", "Position", "URL", "Volume", "KD"], krows),
                                              src.property, True, "ahrefs")["rows"] if krows else 0
    res["rows"] = res["backlinks"] + res["organic_keywords"] + int(res.get("refdomains") or 0)
    return res


def semrush_sync(db: Session, org_id, src: mm.ProjectSource) -> dict:
    res = ingest.sync_semrush(db, org_id, src)  # organic positions for own domain + competitors (shared)
    cr = ih.credentials(db, org_id, "semrush")
    ih.take_token(db, org_id, "semrush")
    r = _ok(http_call("GET", "https://api.semrush.com/analytics/v1/", params={
        "type": "backlinks", "key": cr["secret"], "target": src.property, "target_type": "root_domain", "display_limit": 1000,
        "export_columns": "source_url,target_url,anchor,nofollow,page_ascore,first_seen,last_seen"}), "Semrush backlinks")
    if r.text.startswith("ERROR"):
        if "NOTHING FOUND" not in r.text:
            raise IntegrationError(f"Semrush: {r.text[:120]}")
        res["backlinks"] = 0
    else:
        lines = r.text.strip().splitlines()
        rows = []
        for ln in lines[1:]:
            p = ln.split(";")
            if len(p) >= 7:
                fs = date.fromtimestamp(int(p[5])).isoformat() if p[5].isdigit() else p[5]
                ls = date.fromtimestamp(int(p[6])).isoformat() if p[6].isdigit() else p[6]
                rows.append([p[0], p[1], p[2], p[3], p[4], fs, ls])
        res["backlinks"] = import_ext_backlinks(db, org_id, src.project_id, _rows_csv(
            ["Source url", "Target url", "Anchor", "Nofollow", "Page ascore", "First seen", "Last seen"], rows), "semrush", via="api")["rows"] if rows else 0
    res["rows"] = res["backlinks"] + sum((res.get("domains") or {}).values())
    return res


SYNC = {"ahrefs": ahrefs_sync, "semrush": semrush_sync}


def refresh_difficulty(db: Session, org_id, project: m.Project, phrases: list[str]) -> dict:
    """Keyword difficulty for the given phrases from whichever provider is connected (Semrush phrase_kdi or
    Ahrefs Keywords Explorer). Raises NotConnected when neither is."""
    phrases = [p for p in dict.fromkeys(x.strip().lower() for x in phrases) if p][:100]
    if not phrases:
        return {"updated": 0}
    if connected(db, "semrush"):
        cr = ih.credentials(db, org_id, "semrush")
        rows = []
        for i in range(0, len(phrases), 100):
            ih.take_token(db, org_id, "semrush")
            r = _ok(http_call("GET", "https://api.semrush.com/", params={"type": "phrase_kdi", "key": cr["secret"], "database": "us",
                                                                         "phrase": ";".join(phrases[i:i + 100]), "export_columns": "Ph,Kd"}),
                    "Semrush phrase_kdi")
            if r.text.startswith("ERROR"):
                raise IntegrationError(f"Semrush: {r.text[:120]}")
            for ln in r.text.strip().splitlines()[1:]:
                p = ln.split(";")
                if len(p) >= 2:
                    rows.append({"keyword": p[0].strip().lower(), "difficulty": num(p[1])})
        return {"updated": upsert_difficulty(db, org_id, project.id, rows, "semrush"), "source": "semrush"}
    if connected(db, "ahrefs"):
        cr = ih.credentials(db, org_id, "ahrefs")
        ih.take_token(db, org_id, "ahrefs")
        j = _ok(http_call("GET", "https://api.ahrefs.com/v3/keywords-explorer/overview", headers={"Authorization": f"Bearer {cr['secret']}"},
                          params={"country": "us", "keywords": ",".join(phrases), "select": "keyword,difficulty,volume,cpc"}),
                "Ahrefs keywords explorer").json()
        rows = [{"keyword": (k.get("keyword") or "").lower(), "difficulty": k.get("difficulty"), "volume": k.get("volume"),
                 "cpc": (k.get("cpc") / 100) if isinstance(k.get("cpc"), (int, float)) else None} for k in j.get("keywords", [])]
        return {"updated": upsert_difficulty(db, org_id, project.id, rows, "ahrefs"), "source": "ahrefs"}
    raise ih.NotConnected("Keyword difficulty needs Ahrefs or Semrush — both are NOT CONNECTED. Import a keyword export with a KD column instead.")
