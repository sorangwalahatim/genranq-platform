"""Data ingestion for project monitoring.

Two paths per source, both landing in the same project-scoped time-series tables:
  * API sync (GSC Search Analytics, GA4 Data API, Ahrefs, Semrush) when the provider is
    connected in the Integrations Hub and the project has a property configured;
  * file import (CSV exports) when it isn't — GSC, GA4, Ahrefs/Semrush keyword and
    referring-domain exports, and Screaming Frog `internal_all.csv`.
Header names are matched through alias lists so exports from the tools' UIs work as-is.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_monitor as mm
from . import integrations as ih
from .crawler import normalise
from .integrations import IntegrationError, _ok, http_call


class ImportError_(ValueError):
    pass


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


ALIASES = {
    "date": ["date", "day"],
    "query": ["query", "topqueries", "searchquery", "keyword", "queries"],
    "page": ["page", "toppages", "url", "landingpage", "pages", "address", "currenturl", "landingpageplusquerystring"],
    "device": ["device", "devicecategory", "devices"],
    "country": ["country", "countries"],
    "clicks": ["clicks", "urlclicks"],
    "impressions": ["impressions"],
    "ctr": ["ctr", "urlctr"],
    "position": ["position", "averageposition", "avgposition", "currentposition", "pos"],
    "channel": ["channel", "sessiondefaultchannelgroup", "defaultchannelgroup", "sessiondefaultchannelgrouping", "channelgroup"],
    "sessions": ["sessions"],
    "users": ["users", "totalusers", "activeusers"],
    "engaged_sessions": ["engagedsessions"],
    "conversions": ["conversions", "keyevents"],
    "revenue": ["revenue", "totalrevenue", "purchaserevenue"],
    "volume": ["volume", "searchvolume"],
    "domain": ["domain", "referringdomain", "refdomain"],
    "domain_rating": ["dr", "domainrating", "authorityscore", "as"],
}


def _reader(content: bytes):
    text = content.decode("utf-8-sig", errors="replace")
    # skip leading comment lines (GA4 exports start with '# ----')
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    sample = "\n".join(lines[:5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rdr = csv.DictReader(io.StringIO("\n".join(lines)), dialect=dialect)
    cols = {}
    for h in rdr.fieldnames or []:
        n = _norm(h)
        for k, al in ALIASES.items():
            if n in al and k not in cols:
                cols[k] = h
    return rdr, cols


def _num(v, cast=float):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "n/a", "N/A", "None"):
        return None
    try:
        return cast(float(s))
    except ValueError:
        return None


def _date(v) -> date | None:
    s = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


DEVICE_MAP = {"desktop": "desktop", "mobile": "mobile", "tablet": "tablet", "smartphone": "mobile"}


def _dev(v):
    return DEVICE_MAP.get((v or "").strip().lower(), (v or "").strip().lower() or None)


def _page(v):
    v = (v or "").strip()
    if not v:
        return None
    if v.startswith("/"):
        return v  # GA4 gives paths; keep as path
    try:
        return normalise(v)
    except Exception:
        return v


def path_of(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("/"):
        return url.split("?")[0]
    p = urlparse(url)
    return (p.path or "/")


# ------------------------------------------------------------------ GSC
def import_gsc_csv(db: Session, org_id, project_id, content: bytes, *, default_date: date | None = None) -> dict:
    """Long-format GSC rows: date + any of query/page/device/country + clicks/impressions/(ctr)/position.
    GSC UI 'Dates.csv' (date, clicks, impressions, ctr, position) imports as daily totals."""
    rdr, cols = _reader(content)
    if "clicks" not in cols or "impressions" not in cols:
        raise ImportError_("GSC file needs Clicks and Impressions columns")
    if "date" not in cols and not default_date:
        raise ImportError_("GSC file needs a Date column (or choose the date the export covers)")
    rows, dates = [], set()
    for r in rdr:
        d = _date(r.get(cols.get("date", ""), "")) if "date" in cols else default_date
        if not d:
            continue
        q = (r.get(cols["query"]) or "").strip().lower() if "query" in cols else None
        pg = _page(r.get(cols["page"])) if "page" in cols else None
        grain = "detail" if q else ("page" if pg else "total")
        rows.append(dict(organisation_id=org_id, project_id=project_id, date=d, grain=grain, query=q or None, page=pg,
                         device=_dev(r.get(cols["device"])) if "device" in cols else None,
                         country=(r.get(cols["country"]) or "").strip().lower() or None if "country" in cols else None,
                         clicks=_num(r.get(cols["clicks"]), int) or 0, impressions=_num(r.get(cols["impressions"]), int) or 0,
                         position=_num(r.get(cols["position"])) if "position" in cols else None, source="gsc_import"))
        dates.add(d)
    if not rows:
        raise ImportError_("No usable rows found")
    grains = {x["grain"] for x in rows}
    # replace rows of the same grain for the imported dates (idempotent re-import)
    db.execute(delete(mm.SearchMetric).where(mm.SearchMetric.project_id == project_id, mm.SearchMetric.date.in_(dates),
                                             mm.SearchMetric.grain.in_(grains)))
    for i in range(0, len(rows), 2000):
        db.execute(insert(mm.SearchMetric), rows[i:i + 2000])
    return {"rows": len(rows), "dates": [str(min(dates)), str(max(dates))], "grains": sorted(grains)}


def sync_gsc(db: Session, org_id, src: mm.ProjectSource, days: int = 90) -> dict:
    tok = ih.oauth_access_token(db, org_id, "gsc")
    prop = src.property
    last = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == src.project_id,
                                                                 mm.SearchMetric.source == "gsc"))
    end = date.today() - timedelta(days=2)
    start = (last - timedelta(days=3)) if last else end - timedelta(days=days)
    url = f"https://www.googleapis.com/webmasters/v3/sites/{__import__('urllib.parse').parse.quote(prop, safe='')}/searchAnalytics/query"
    total = 0
    for grain, dims in (("total", ["date", "device", "country"]), ("page", ["date", "page", "device"]),
                        ("detail", ["date", "query", "page", "device", "country"])):
        db.execute(delete(mm.SearchMetric).where(mm.SearchMetric.project_id == src.project_id, mm.SearchMetric.grain == grain,
                                                 mm.SearchMetric.date >= start, mm.SearchMetric.source == "gsc"))
        start_row = 0
        while True:
            ih.take_token(db, org_id, "gsc")
            r = _ok(http_call("POST", url, headers={"Authorization": f"Bearer {tok}"}, json={
                "startDate": str(start), "endDate": str(end), "dimensions": dims, "rowLimit": 25000, "startRow": start_row,
                "dataState": "final"}), "GSC searchAnalytics")
            data = r.json().get("rows", [])
            batch = []
            for x in data:
                k = dict(zip(dims, x["keys"]))
                batch.append(dict(organisation_id=org_id, project_id=src.project_id, date=date.fromisoformat(k["date"]), grain=grain,
                                  query=k.get("query"), page=normalise(k["page"]) if k.get("page") else None,
                                  device=_dev(k.get("device")), country=k.get("country"), clicks=int(x["clicks"]),
                                  impressions=int(x["impressions"]), position=round(x["position"], 2), source="gsc"))
            if batch:
                db.execute(insert(mm.SearchMetric), batch)
            total += len(batch)
            if len(data) < 25000 or grain == "detail" and start_row >= 100000:
                break
            start_row += 25000
    return {"rows": total, "from": str(start), "to": str(end)}


# ------------------------------------------------------------------ GA4
def import_ga4_csv(db: Session, org_id, project_id, content: bytes) -> dict:
    rdr, cols = _reader(content)
    if "date" not in cols or "sessions" not in cols:
        raise ImportError_("GA4 file needs Date and Sessions columns (add the Date dimension to the exploration)")
    rows, dates = [], set()
    for r in rdr:
        d = _date(r.get(cols["date"]))
        if not d:
            continue
        rows.append(dict(organisation_id=org_id, project_id=project_id, date=d,
                         landing_page=path_of(r.get(cols["page"])) if "page" in cols else None,
                         device=_dev(r.get(cols["device"])) if "device" in cols else None,
                         country=(r.get(cols["country"]) or "").strip().lower() or None if "country" in cols else None,
                         channel=(r.get(cols["channel"]) or "").strip() or None if "channel" in cols else None,
                         sessions=_num(r.get(cols["sessions"]), int) or 0,
                         users=_num(r.get(cols["users"]), int) or 0 if "users" in cols else 0,
                         engaged_sessions=_num(r.get(cols["engaged_sessions"]), int) or 0 if "engaged_sessions" in cols else 0,
                         conversions=_num(r.get(cols["conversions"])) or 0 if "conversions" in cols else 0,
                         revenue=_num(r.get(cols["revenue"])) or 0 if "revenue" in cols else 0, source="ga4_import"))
        dates.add(d)
    if not rows:
        raise ImportError_("No usable rows found")
    db.execute(delete(mm.AnalyticsMetric).where(mm.AnalyticsMetric.project_id == project_id, mm.AnalyticsMetric.date.in_(dates)))
    for i in range(0, len(rows), 2000):
        db.execute(insert(mm.AnalyticsMetric), rows[i:i + 2000])
    return {"rows": len(rows), "dates": [str(min(dates)), str(max(dates))]}


def sync_ga4(db: Session, org_id, src: mm.ProjectSource, days: int = 90) -> dict:
    tok = ih.oauth_access_token(db, org_id, "ga4")
    prop = src.property if src.property.startswith("properties/") else f"properties/{src.property}"
    last = db.scalar(select(func.max(mm.AnalyticsMetric.date)).where(mm.AnalyticsMetric.project_id == src.project_id,
                                                                    mm.AnalyticsMetric.source == "ga4"))
    end = date.today() - timedelta(days=1)
    start = (last - timedelta(days=2)) if last else end - timedelta(days=days)
    db.execute(delete(mm.AnalyticsMetric).where(mm.AnalyticsMetric.project_id == src.project_id, mm.AnalyticsMetric.date >= start,
                                                mm.AnalyticsMetric.source == "ga4"))
    total, offset = 0, 0
    while True:
        ih.take_token(db, org_id, "ga4")
        r = _ok(http_call("POST", f"https://analyticsdata.googleapis.com/v1beta/{prop}:runReport", headers={"Authorization": f"Bearer {tok}"}, json={
            "dateRanges": [{"startDate": str(start), "endDate": str(end)}],
            "dimensions": [{"name": n} for n in ("date", "landingPage", "deviceCategory", "country", "sessionDefaultChannelGroup")],
            "metrics": [{"name": n} for n in ("sessions", "totalUsers", "engagedSessions", "keyEvents", "totalRevenue")],
            "limit": 100000, "offset": offset}), "GA4 runReport")
        j = r.json()
        batch = []
        for row in j.get("rows", []):
            d = [x["value"] for x in row["dimensionValues"]]
            v = [x["value"] for x in row["metricValues"]]
            batch.append(dict(organisation_id=org_id, project_id=src.project_id, date=_date(d[0]), landing_page=path_of(d[1]),
                              device=_dev(d[2]), country=d[3].lower(), channel=d[4], sessions=int(float(v[0])), users=int(float(v[1])),
                              engaged_sessions=int(float(v[2])), conversions=float(v[3]), revenue=float(v[4]), source="ga4"))
        if batch:
            db.execute(insert(mm.AnalyticsMetric), batch)
        total += len(batch)
        offset += 100000
        if offset >= int(j.get("rowCount", 0)):
            break
    return {"rows": total, "from": str(start), "to": str(end)}


# ------------------------------------------------------------------ Ahrefs / Semrush
def import_keywords_csv(db: Session, org_id, project_id, content: bytes, domain: str, is_own: bool, source: str) -> dict:
    rdr, cols = _reader(content)
    if "query" not in cols:
        raise ImportError_("Keyword export needs a Keyword column")
    dom = (urlparse(domain if "://" in domain else "https://" + domain).netloc or domain).lower()
    db.execute(delete(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project_id, mm.CompetitorKeyword.domain == dom))
    rows = []
    for r in rdr:
        kw = (r.get(cols["query"]) or "").strip().lower()
        if not kw:
            continue
        rows.append(dict(organisation_id=org_id, project_id=project_id, domain=dom, is_own=is_own, keyword=kw,
                         position=_num(r.get(cols["position"]), int) if "position" in cols else None,
                         url=(r.get(cols["page"]) or "").strip() or None if "page" in cols else None,
                         volume=_num(r.get(cols["volume"]), int) if "volume" in cols else None, source=source))
    if rows:
        db.execute(insert(mm.CompetitorKeyword), rows)
    return {"rows": len(rows), "domain": dom}


def import_refdomains_csv(db: Session, org_id, project_id, content: bytes, domain: str, source: str,
                          snapshot_date: date | None = None, target: str = "own") -> dict:
    """target='own' for the project's site, or the competitor domain these referring domains belong to."""
    rdr, cols = _reader(content)
    key = cols.get("domain") or cols.get("page")
    if not key:
        raise ImportError_("Referring-domains export needs a Domain / Referring domain column")
    d = snapshot_date or date.today()
    db.execute(delete(mm.RefdomainSnapshot).where(mm.RefdomainSnapshot.project_id == project_id, mm.RefdomainSnapshot.snapshot_date == d,
                                                  mm.RefdomainSnapshot.target == target))
    seen, rows = set(), []
    for r in rdr:
        v = (r.get(key) or "").strip().lower()
        v = urlparse(v if "://" in v else "https://" + v).netloc or v
        if v and v not in seen:
            seen.add(v)
            rows.append(dict(organisation_id=org_id, project_id=project_id, snapshot_date=d, domain=v, source=source, target=target,
                             domain_rating=_num(r.get(cols["domain_rating"])) if "domain_rating" in cols else None))
    if rows:
        db.execute(insert(mm.RefdomainSnapshot), rows)
    if target != "own":
        return {"rows": len(rows), "snapshot_date": str(d), "target": target}
    dom = (urlparse(domain if "://" in domain else "https://" + domain).netloc or domain).lower()
    db.execute(delete(mm.LinkMetric).where(mm.LinkMetric.project_id == project_id, mm.LinkMetric.date == d, mm.LinkMetric.source == source))
    db.add(mm.LinkMetric(organisation_id=org_id, project_id=project_id, date=d, domain=dom, refdomains=len(rows), source=source))
    return {"rows": len(rows), "snapshot_date": str(d)}


def sync_ahrefs(db: Session, org_id, src: mm.ProjectSource) -> dict:
    from . import sources
    cr = ih.credentials(db, org_id, "ahrefs")
    target = src.property
    met = sources.ahrefs_metrics(db, org_id, target)
    today = date.today()
    db.execute(delete(mm.LinkMetric).where(mm.LinkMetric.project_id == src.project_id, mm.LinkMetric.date == today, mm.LinkMetric.source == "ahrefs"))
    db.add(mm.LinkMetric(organisation_id=org_id, project_id=src.project_id, date=today, domain=target, refdomains=met.get("refdomains"),
                         backlinks=met.get("backlinks"), domain_rating=met.get("domain_rating"), source="ahrefs"))
    ih.take_token(db, org_id, "ahrefs")
    r = _ok(http_call("GET", "https://api.ahrefs.com/v3/site-explorer/refdomains", headers={"Authorization": f"Bearer {cr['secret']}"},
                      params={"target": target, "mode": "subdomains", "select": "domain,domain_rating", "limit": 1000,
                              "history": "live"}), "Ahrefs refdomains")
    doms = r.json().get("refdomains", [])
    db.execute(delete(mm.RefdomainSnapshot).where(mm.RefdomainSnapshot.project_id == src.project_id, mm.RefdomainSnapshot.snapshot_date == today,
                                                  mm.RefdomainSnapshot.target == "own"))
    if doms:
        db.execute(insert(mm.RefdomainSnapshot), [dict(organisation_id=org_id, project_id=src.project_id, snapshot_date=today,
                                                       domain=d["domain"], domain_rating=d.get("domain_rating"), source="ahrefs") for d in doms])
    return {"refdomains": len(doms), **met}


def sync_semrush(db: Session, org_id, src: mm.ProjectSource) -> dict:
    cr = ih.credentials(db, org_id, "semrush")
    db_code = (src.config or {}).get("database", "us")
    domains = [(src.property, True)] + [(c.domain, False) for c in db.scalars(select(m.Competitor).where(m.Competitor.project_id == src.project_id))]
    out = {}
    for dom, own in domains:
        d = urlparse(dom if "://" in dom else "https://" + dom).netloc or dom
        ih.take_token(db, org_id, "semrush")
        r = _ok(http_call("GET", "https://api.semrush.com/", params={"type": "domain_organic", "key": cr["secret"], "domain": d,
                                                                    "database": db_code, "display_limit": 500,
                                                                    "export_columns": "Ph,Po,Ur,Nq"}), "Semrush domain_organic")
        if r.text.startswith("ERROR"):
            raise IntegrationError(f"Semrush: {r.text[:120]}")
        content = r.text.encode()
        res = import_keywords_csv(db, org_id, src.project_id, content, d, own, "semrush")
        out[d] = res["rows"]
    return {"domains": out}


# ------------------------------------------------------------------ Screaming Frog
def import_screaming_frog(db: Session, org_id, project_id, site_id, content: bytes) -> dict:
    """internal_all.csv → a crawl_run (engine=screaming_frog) with crawl_page rows; change detection runs on it."""
    rdr, _ = _reader(content)
    fields = {(_norm(h)): h for h in rdr.fieldnames or []}

    def g(r, *names):
        for n in names:
            h = fields.get(_norm(n))
            if h and r.get(h) not in (None, ""):
                return r.get(h)
        return None

    if not fields.get("address"):
        raise ImportError_("Screaming Frog file needs the Address column (export Internal → All)")
    site = db.get(m.Site, site_id)
    cr = m.CrawlRun(organisation_id=org_id, site_id=site_id, start_url=site.domain, engine="screaming_frog", status="COMPLETED",
                    started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), site_info={"import": True})
    db.add(cr)
    db.flush()
    n = 0
    for r in rdr:
        url = g(r, "Address")
        if not url:
            continue
        ctype = g(r, "Content Type") or ""
        status = _num(g(r, "Status Code"), int)
        indexability = (g(r, "Indexability") or "").lower()
        status_txt = (g(r, "Indexability Status") or "").lower()
        h1 = [x for x in (g(r, "H1-1"), g(r, "H1-2")) if x]
        data = {"status": status, "content_type": ctype, "title": g(r, "Title 1"), "meta_description": g(r, "Meta Description 1"),
                "h1": h1, "canonical": normalise(g(r, "Canonical Link Element 1")) if g(r, "Canonical Link Element 1") else None,
                "noindex": "noindex" in status_txt or ("non-indexable" in indexability and "noindex" in (g(r, "Meta Robots 1") or "").lower()),
                "meta_robots": (g(r, "Meta Robots 1") or "").lower(), "word_count": _num(g(r, "Word Count"), int) or 0,
                "depth": _num(g(r, "Crawl Depth"), int), "inlinks": _num(g(r, "Inlinks", "Unique Inlinks"), int) or 0,
                "outlinks": _num(g(r, "Outlinks"), int) or 0, "elapsed": _num(g(r, "Response Time")),
                "indexability": indexability, "redirect_url": g(r, "Redirect URL"), "source": "screaming_frog"}
        db.add(m.CrawlPage(organisation_id=org_id, crawl_run_id=cr.id, url=normalise(url)[:2000], status_code=status,
                           depth=data["depth"] if data["depth"] is not None else -1, content_type=ctype[:200], data=data))
        n += 1
    cr.pages_crawled = n
    return {"crawl_run_id": str(cr.id), "pages": n}
