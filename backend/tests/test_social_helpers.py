"""Shared helpers for the social test modules: fake platform HTTP, org-level connections, approved content."""
from __future__ import annotations

import json
import re
import time
import uuid

import httpx

from app import models as m
from app.db import tenant_session
from app.services import integrations as ih


class FakeHTTP:
    """Stands in for integrations.http_call. Routes are (method, url-regex) → response or callable."""

    def __init__(self):
        self.calls: list[dict] = []
        self.routes: list[tuple] = []

    def on(self, method, pattern, status=200, json_body=None, headers=None, fn=None):
        self.routes.insert(0, (method, re.compile(pattern), status, json_body, headers or {}, fn))
        return self

    def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for meth, rx, status, body, headers, fn in self.routes:
            if meth == method and rx.search(url):
                if fn:
                    return fn(method, url, **kw)
                return httpx.Response(status, json=body if body is not None else {}, headers=headers,
                                      request=httpx.Request(method, url))
        return httpx.Response(404, json={"error": {"message": f"no fake route for {method} {url}"}}, request=httpx.Request(method, url))

    def count(self, method, pattern):
        rx = re.compile(pattern)
        return sum(1 for c in self.calls if c["method"] == method and rx.search(c["url"]))


def fake_http(monkeypatch) -> FakeHTTP:
    f = FakeHTTP()
    monkeypatch.setattr(ih, "http_call", f)
    return f


def connect(org, key, config=None, webhook_only=False):
    oid = uuid.UUID(org.org_id)
    with tenant_session(oid) as db:
        secret = None if webhook_only else json.dumps({"access_token": f"tok-{key}", "refresh_token": None, "expires_at": time.time() + 3600})
        c = ih.connect_api_key(db, oid, None, key, secret, config or {})
        c.status = "CONNECTED"


def content(org, pid, title="Spring offer", body="Fixed-price drain unblocking this week only.", state="approved", channel="x"):
    """Create a content item and walk it through the state machine (the DB trigger validates each step)."""
    oid = uuid.UUID(org.org_id)
    with tenant_session(oid) as db:
        it = m.ContentItem(organisation_id=oid, project_id=uuid.UUID(pid), type="social", state="draft", channel=channel, title=title)
        db.add(it)
        db.flush()
        if body:
            db.add(m.ContentVersion(organisation_id=oid, content_item_id=it.id, version=1, body=body))
        path = {"draft": [], "generated": ["generated"], "review": ["review"], "approved": ["review", "approved"],
                "published": ["review", "approved", "published"]}[state]
        for s in path:
            it.state = s
            db.flush()
        return str(it.id)


def set_state(org, iid, *states):
    with tenant_session(uuid.UUID(org.org_id)) as db:
        it = db.get(m.ContentItem, uuid.UUID(iid))
        for s in states:
            it.state = s
            db.flush()


def state_of(org, iid):
    with tenant_session(uuid.UUID(org.org_id)) as db:
        return db.get(m.ContentItem, uuid.UUID(iid)).state


def account(org, pid, channel="x", ext="4242", name="Acme on X"):
    r = org.post("/social/accounts", {"project_id": pid, "channel": channel, "external_id": ext, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def drain(limit: int = 50) -> int:
    """Run the worker until the queue is empty (a backlog from earlier runs can exceed one run_once batch)."""
    from app.worker import run_once
    total = 0
    for _ in range(limit):
        n = run_once()
        total += n
        if not n:
            break
    return total
