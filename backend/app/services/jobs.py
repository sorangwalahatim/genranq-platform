"""Job queue + scheduler (F-004).

Postgres-backed queue using ``FOR UPDATE SKIP LOCKED`` so any number of workers can
claim safely. Statuses: QUEUED → RUNNING → COMPLETED | FAILED.
* retries with exponential backoff + jitter (attempts / max_attempts)
* FAILED after max_attempts = the dead-letter queue (listed, manually retryable)
* RateLimited re-queues without consuming an attempt
* NotConnected / PermanentError fail immediately (no pointless retries)
* stale RUNNING jobs (worker crash) are re-queued after the lease expires
"""
from __future__ import annotations

import os
import random
import socket
import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from .. import models as m
from ..db import system_session, tenant_session
from .integrations import NotConnected, RateLimited

LEASE = timedelta(minutes=30)
HANDLERS: dict[str, Callable] = {}


class PermanentError(Exception):
    pass


def handler(kind: str):
    def deco(f):
        HANDLERS[kind] = f
        return f
    return deco


def utcnow():
    return datetime.now(timezone.utc)


def enqueue(db: Session, org_id, kind: str, payload: dict | None = None, *, run_at=None, max_attempts=3,
            idempotency_key: str | None = None, created_by=None) -> m.Job:
    if idempotency_key:
        existing = db.scalar(select(m.Job).where(m.Job.idempotency_key == idempotency_key))
        if existing:
            return existing
    j = m.Job(organisation_id=org_id, kind=kind, payload=payload or {}, run_at=run_at or utcnow(),
              max_attempts=max_attempts, idempotency_key=idempotency_key, created_by=created_by)
    db.add(j)
    db.flush()
    return j


@dataclass
class JobCtx:
    job_id: uuid.UUID
    org_id: uuid.UUID
    payload: dict
    attempt: int

    def progress(self, pct: int):
        with system_session() as db:
            db.execute(update(m.Job).where(m.Job.id == self.job_id).values(progress=max(0, min(100, int(pct)))))

    def session(self):
        return tenant_session(self.org_id)


def claim(worker_id: str) -> m.Job | None:
    with system_session() as db:
        # Workers heartbeat every 30s; a job silent for 5 minutes belongs to a dead worker.
        db.execute(text("""update job set
                             status = case when attempts >= max_attempts then 'FAILED' else 'QUEUED' end,
                             finished_at = case when attempts >= max_attempts then now() else null end,
                             locked_by = null, error = coalesce(error,'') || ' [worker lost]'
                           where status='RUNNING' and coalesce(heartbeat_at, started_at) < now() - interval '5 minutes'"""))
        row = db.execute(text("""
            update job set status='RUNNING', locked_by=:w, started_at=now(), heartbeat_at=now(), attempts=attempts+1
            where id = (select id from job where status='QUEUED' and run_at <= now()
                        order by run_at for update skip locked limit 1)
            returning id"""), {"w": worker_id}).first()
        return db.get(m.Job, row[0]) if row else None


def _finish(job_id, **vals):
    with system_session() as db:
        db.execute(update(m.Job).where(m.Job.id == job_id).values(**vals))


def _heartbeat(job_id, stop: threading.Event):
    while not stop.wait(30):
        try:
            with system_session() as db:
                db.execute(update(m.Job).where(m.Job.id == job_id, m.Job.status == "RUNNING").values(heartbeat_at=utcnow()))
        except Exception:
            traceback.print_exc()


def execute(job: m.Job):
    stop = threading.Event()
    hb = threading.Thread(target=_heartbeat, args=(job.id, stop), daemon=True)
    hb.start()
    try:
        _execute(job)
    finally:
        stop.set()


def _execute(job: m.Job):
    f = HANDLERS.get(job.kind)
    ctx = JobCtx(job.id, job.organisation_id, job.payload or {}, job.attempts)
    try:
        if not f:
            raise PermanentError(f"No handler for job kind '{job.kind}'")
        result = f(ctx) or {}
        _finish(job.id, status="COMPLETED", result=result, progress=100, finished_at=utcnow(), error=None, locked_by=None)
    except RateLimited as e:
        _finish(job.id, status="QUEUED", attempts=job.attempts - 1, run_at=utcnow() + timedelta(seconds=max(5, e.retry_after)),
                error=str(e), locked_by=None)
    except (NotConnected, PermanentError, HTTPException) as e:
        msg = str(e.detail if isinstance(e, HTTPException) else e)
        _finish(job.id, status="FAILED", error=msg, finished_at=utcnow(), locked_by=None)
        _on_failed(job, msg)
        return
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:2000]
        if job.attempts < job.max_attempts:
            delay = (2 ** job.attempts) * 15 + random.uniform(0, 10)
            _finish(job.id, status="QUEUED", run_at=utcnow() + timedelta(seconds=delay), error=err, locked_by=None)
        else:
            _finish(job.id, status="FAILED", error=err + "\n" + traceback.format_exc()[-1500:], finished_at=utcnow(), locked_by=None)
            _on_failed(job, err)


def _on_failed(job: m.Job, err: str):
    hook = FAIL_HOOKS.get(job.kind)
    try:
        if hook:
            hook(job, err)
        with tenant_session(job.organisation_id) as db:
            db.add(m.Notification(organisation_id=job.organisation_id, kind="job_failed",
                                  title=f"Job failed: {job.kind.replace('_', ' ')}", body=err[:300], link="/jobs"))
    except Exception:
        traceback.print_exc()


FAIL_HOOKS: dict[str, Callable] = {}


def retry(db: Session, job_id) -> m.Job:
    j = db.get(m.Job, job_id)
    if not j or j.status != "FAILED":
        raise ValueError("Only FAILED jobs can be retried")
    j.status, j.attempts, j.run_at, j.error, j.finished_at = "QUEUED", 0, utcnow(), None, None
    return j


# ------------------------------------------------------------------ scheduler
FREQ = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "monthly": timedelta(days=30)}


def scheduler_tick() -> int:
    n = 0
    with system_session() as db:
        due = db.scalars(select(m.JobSchedule).where(m.JobSchedule.enabled, m.JobSchedule.next_run_at <= utcnow())
                         .with_for_update(skip_locked=True)).all()
        for s in due:
            key = f"sched:{s.id}:{s.next_run_at.isoformat()}"
            enqueue(db, s.organisation_id, s.kind, {**(s.payload or {}), "triggered_by": "schedule"}, idempotency_key=key)
            s.last_run_at = utcnow()
            nxt = s.next_run_at
            while nxt <= utcnow():
                nxt += FREQ[s.frequency]
            s.next_run_at = nxt
            n += 1
    return n


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"
