"""Platform email: an outbox table + a job that sends through SMTP when configured.

Env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY (starttls|ssl|none).
Without SMTP_HOST every email is recorded as NOT CONNECTED (visible in the app) — never pretended.
Users can opt out of task emails with organisation/user settings later; in-app notifications always happen.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from .. import models as m
from .. import models_monitor as mm


def smtp_settings() -> dict | None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {"host": host, "port": int(os.environ.get("SMTP_PORT", "587")), "user": os.environ.get("SMTP_USER"),
            "password": os.environ.get("SMTP_PASSWORD"), "sender": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "no-reply@genranq.com",
            "security": os.environ.get("SMTP_SECURITY", "starttls").lower()}


def status() -> dict:
    s = smtp_settings()
    return {"connected": bool(s), "status": "CONNECTED" if s else "NOT_CONNECTED",
            "message": None if s else "Email is NOT CONNECTED — set SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM on the server. "
                                     "Tasks and in-app notifications still work."}


def _app_url() -> str:
    return (os.environ.get("APP_URL") or "").rstrip("/")


def queue(db, org_id, user_id, subject: str, body: str, *, kind="task_assigned", entity=None) -> mm.EmailOutbox | None:
    """Queue an email to a user (by id). Returns the outbox row (or None when the user has no email / is inactive)."""
    from .jobs import enqueue
    u = db.get(m.User, user_id) if user_id else None
    if not u or not u.is_active or not u.email:
        return None
    link = _app_url()
    if link and "{link}" in body:
        body = body.replace("{link}", link)
    row = mm.EmailOutbox(organisation_id=org_id, user_id=u.id, to_email=u.email, subject=subject[:250], body=body[:100000], kind=kind, entity=entity)
    db.add(row)
    db.flush()
    if not smtp_settings():
        row.status, row.error = "not_connected", "SMTP not configured"
        return row
    enqueue(db, org_id, "email_send", {"outbox_id": str(row.id)}, max_attempts=3)
    return row


def deliver(row: mm.EmailOutbox) -> str:
    cfg = smtp_settings()
    if not cfg:
        row.status, row.error = "not_connected", "SMTP not configured"
        return row.status
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["sender"], row.to_email, row.subject
    msg.set_content(row.body)
    row.attempts += 1
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
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass
    row.status, row.sent_at, row.error = "sent", datetime.now(timezone.utc), None
    return row.status


def task_email(db, task: m.Task, user_id, *, reason="assigned") -> mm.EmailOutbox | None:
    project = db.get(m.Project, task.project_id)
    subj = f"[{project.name if project else 'Genranq'}] {'New task' if reason == 'assigned' else 'Task reopened'}: {task.title}"
    due = f"Due: {task.due_date:%Y-%m-%d}\n" if task.due_date else ""
    body = (f"You have been {'assigned' if reason == 'assigned' else 'sent back'} a task in Genranq.\n\n"
            f"Project: {project.name if project else '-'}\nPriority: {task.priority}\n{due}\n{task.title}\n\n{task.description or ''}\n\n"
            f"Open it: {{link}}/tasks?project={task.project_id}\n")
    return queue(db, task.organisation_id, user_id, subj, body, kind=f"task_{reason}", entity=f"task:{task.id}")
