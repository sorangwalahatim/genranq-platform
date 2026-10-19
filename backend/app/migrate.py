"""Create/upgrade the schema, row-level security, grants and global seed data.

Run as the owner role:  python -m app.migrate
Idempotent: safe to re-run.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from . import models
from .db import system_engine
from .seed import seed_global

# Global (non-tenant) tables — no RLS predicate on organisation.
GLOBAL_TABLES = {"audit_checklist_item", "service", "marketplace_listing", "spec_snapshot"}
# monitor_config is keyed by project but still carries organisation_id (RLS applies)
# Tenant tables where organisation_id NULL means a built-in row visible to everyone (read only).
SHARED_BUILTIN = {"prompt", "skill", "free_tool_lead", "strategy_template"}


def tenant_tables():
    out = []
    for t in models.Base.metadata.sorted_tables:
        if t.schema == "vault" or t.name in GLOBAL_TABLES:
            continue
        if t.name == "organisation":
            continue
        cols = {c.name for c in t.columns}
        key = "organisation_id" if "organisation_id" in cols else ("publisher_org_id" if "publisher_org_id" in cols else None)
        assert key, f"table {t.name} has no tenant key"
        out.append((t.name, key))
    return out


def run():
    with system_engine.begin() as c:
        c.execute(text("create extension if not exists pgcrypto"))
        c.execute(text("create extension if not exists citext"))
        c.execute(text("create schema if not exists vault"))
    models.Base.metadata.create_all(system_engine)
    with system_engine.begin() as c:  # additive column upgrades for existing databases
        c.execute(text('alter table "user" add column if not exists is_platform_admin boolean not null default false'))
        c.execute(text('alter table "user" add column if not exists failed_auth integer not null default 0'))
        c.execute(text('alter table "user" add column if not exists locked_until timestamptz'))
        c.execute(text('alter table "user" add column if not exists mfa_last_step bigint'))
        c.execute(text('alter table job add column if not exists heartbeat_at timestamptz'))
        c.execute(text("alter table task add column if not exists workstream text"))
        c.execute(text("alter table task add column if not exists assignee_type text not null default 'user'"))
        c.execute(text("alter table task add column if not exists agent_key text"))
        c.execute(text("alter table task add column if not exists output jsonb not null default '{}'::jsonb"))
        c.execute(text("alter table refdomain_snapshot add column if not exists target text not null default 'own'"))
        c.execute(text("alter table project_strategy add column if not exists template_key text"))
        c.execute(text("alter table activity_run add column if not exists triggered_by text not null default 'schedule'"))

    with system_engine.begin() as c:
        # ---- grants: app role gets DML on public tables only; no access to vault schema
        c.execute(text("grant usage on schema public to genranq_app"))
        c.execute(text("grant select, insert, update, delete on all tables in schema public to genranq_app"))
        c.execute(text("grant usage, select on all sequences in schema public to genranq_app"))
        c.execute(text("revoke all on schema vault from genranq_app"))
        c.execute(text("revoke update, delete on activity_log from genranq_app"))
        # The API role can never set the operator-only platform-admin flag.
        cols = ", ".join(col.name for col in models.User.__table__.columns if col.name != "is_platform_admin")
        c.execute(text('revoke insert, update on "user" from genranq_app'))
        c.execute(text(f'grant insert ({cols}), update ({cols}) on "user" to genranq_app'))
        c.execute(text("revoke insert, update, delete on audit_checklist_item, service, spec_snapshot from genranq_app"))

        # ---- RLS
        cur = "nullif(current_setting('app.org_id', true), '')::uuid"
        c.execute(text("alter table organisation enable row level security"))
        c.execute(text("drop policy if exists tenant_isolation on organisation"))
        c.execute(text(f"create policy tenant_isolation on organisation to genranq_app using (id = {cur}) with check (id = {cur})"))
        for name, key in tenant_tables():
            q = f'"{name}"'
            c.execute(text(f"alter table {q} enable row level security"))
            c.execute(text(f"drop policy if exists tenant_isolation on {q}"))
            c.execute(text(f"drop policy if exists builtin_read on {q}"))
            if name in SHARED_BUILTIN:
                c.execute(text(f"create policy tenant_isolation on {q} to genranq_app using ({key} = {cur}) with check ({key} = {cur})"))
                c.execute(text(f"create policy builtin_read on {q} for select to genranq_app using ({key} is null)"))
            elif name == "marketplace_listing":
                pass
            else:
                c.execute(text(f"create policy tenant_isolation on {q} to genranq_app using ({key} = {cur}) with check ({key} = {cur})"))

        # ---- immutable activity log (F-005)
        c.execute(text("""
            create or replace function activity_log_immutable() returns trigger language plpgsql as $$
            begin
              if current_setting('app.allow_log_purge', true) = 'on' then return old; end if;
              raise exception 'activity_log is immutable';
            end $$;"""))
        c.execute(text("drop trigger if exists trg_activity_log_immutable on activity_log"))
        c.execute(text("create trigger trg_activity_log_immutable before update or delete on activity_log "
                       "for each row execute function activity_log_immutable()"))

        # ---- content state machine guard (F-025)
        c.execute(text("""
            create or replace function content_state_guard() returns trigger language plpgsql as $$
            declare ok boolean;
            begin
              if new.state = old.state then return new; end if;
              ok := (old.state, new.state) in (
                ('draft','generated'),('generated','review'),('draft','review'),('review','approved'),
                ('review','draft'),('approved','published'),('approved','scheduled'),('scheduled','published'),
                ('scheduled','approved'),('generated','draft'));
              if not ok then raise exception 'invalid content state transition % -> %', old.state, new.state; end if;
              return new;
            end $$;"""))
        c.execute(text("drop trigger if exists trg_content_state on content_item"))
        c.execute(text("create trigger trg_content_state before update of state on content_item "
                       "for each row execute function content_state_guard()"))

    seed_global()
    insp = inspect(system_engine)
    print(f"migrated: {len(insp.get_table_names())} public tables, "
          f"{len(insp.get_table_names(schema='vault'))} vault tables, RLS on {len(tenant_tables()) + 1}")


if __name__ == "__main__":
    run()
