# Genranq

A multi-tenant SEO operations platform for agencies. It is built from the **Genranq Build Plan** workbook (all nine sheets) and follows the workbook's Roadmap.

| Roadmap phase | State in this release |
|---|---|
| 0. Foundation | **Working.** Auth, multi-tenancy, RBAC, projects, tasks, time tracking, approvals, activity log, job queue |
| 1. SEO Core & Audit | **Working.** Crawler, 62-check audit engine, scoring, action plans, rank tracker, PageSpeed, Search Console, reports, automation |
| Pulled forward | **Working.** Integrations Hub (all 23 providers) and Credential Vault |
| 2–6 | Database schema created; module pages show the spec and roadmap status |

See `docs/TRACEABILITY.md`, which maps every module, feature, table, integration and audit check to the build, and `docs/ASSUMPTIONS.md`.

## Run it

### With Docker
```bash
cp .env.example .env                      # then set JWT_SECRET and VAULT_MASTER_KEY
docker compose up --build                 # api on :8000, worker, postgres
docker compose exec api python -m app.admin grant-platform-admin you@agency.com your-org-slug
```
Open http://localhost:8000 and create an organisation.

### Local development
```bash
sudo -u postgres bash scripts/setup_db.sh          # roles: genranq_owner (migrations), genranq_app (RLS-restricted)
cd backend && pip install -r requirements.txt
python -m app.migrate                               # schema, RLS policies, grants, checklist seed
EMBEDDED_WORKER=1 uvicorn app.main:app --reload     # or run `python -m app.worker` separately
cd ../frontend && npm install && npm run dev        # http://localhost:5173 (proxies /api)
```

### Tests
```bash
sudo -u postgres DB_NAME=genranq_test bash scripts/setup_db.sh
cd backend && python -m pytest -q
```
The suite has 28 tests. They cover:
- the workbook parser and diff, and spec-refresh review
- Row-level security between tenants
- RBAC and the client role
- refresh-token reuse detection
- MFA, including replay protection and lockout
- vault step-up and encryption at rest
- the immutable activity log and the content state machine
- Pricing Matrix quotas
- job retries, the rate limiter and the scheduler
- SSRF guards
- an end-to-end audit of a fixture site with known defects, through to the action plan, tasks and PDF/Excel reports

## Architecture
```
React SPA (frontend/)  →  FastAPI (/api)  →  services (audit engine, crawler, entitlements, integrations, vault)
                                   ↓                      ↓
                     Postgres (RLS per org)  ←  job queue (SKIP LOCKED) ← worker(s) + scheduler
```
- **Tenancy.** Each request runs in a transaction with `app.org_id` set. Every tenant table has a Postgres RLS policy, so even a query with no filter returns only the caller's rows.
- **Jobs.** A job moves QUEUED → RUNNING → COMPLETED / FAILED.
  - Failures retry with exponential backoff.
  - Rate-limited calls are re-queued without using up an attempt.
  - Running jobs send a heartbeat, so work held by a dead worker is recovered.
  - Failed jobs stay listed (the dead-letter list) and can be retried.
- **Spec refresh.** A platform admin uploads the workbook or pulls it from Drive. Genranq shows a change summary (modules, features, tables, integrations, roadmap, audit checks, pricing). Nothing applies until it is approved. Once approved, plans and the checklist follow the new workbook.

## Key environment variables
`.env.example` documents all of them. The main ones:
- `JWT_SECRET`, `VAULT_MASTER_KEY`: required in production.
- `*_OAUTH_CLIENT_ID/SECRET`: enable OAuth connections. Without them those providers show NOT CONNECTED.
- `SPEC_DRIVE_FILE_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`: read-only spec refresh from Google Drive.
- `SCREAMING_FROG_CLI`: path to the CLI on worker hosts.
- `ALLOW_PRIVATE_CRAWL=1`: development and tests only.

## Modules, monitoring and access (current release)

* **All 76 Module Catalog modules are routed** (74 live, 2 partial: Chrome extension store listing and
  white-label TLS are operator steps). Status per module is shown in the sidebar and on the Product spec page.
* **Project monitoring** — per-project GSC / GA4 / Ahrefs / Semrush / Screaming Frog sources (API or CSV import),
  anomaly detection, root cause, team routing, verification of fixes, blog checkpoints with automatic gap analysis,
  executive / project / page / team dashboards.
* **Fix playbooks & responsibility** — every issue task carries the solution, numbered steps per team, who is
  Responsible (named people), Accountable (project lead/PM), Consulted, the due date (SLA by severity) and the
  prevention strategy. Assignees get the task in the app, an in-app notification and an email.
* **Email** — set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` (starttls|ssl|none)
  and `APP_URL`. Without SMTP every email is recorded as NOT CONNECTED in the outbox (`/api/email/outbox`).
* **Access rights** — Team → Access rights: per user, all projects or selected projects, and all modules or selected
  modules. Enforced by the API (403 `module_denied`, 404 for projects outside scope); the sidebar hides what isn't allowed.
  Owners always have full access; roles still decide actions (view/create/edit/approve…).
* **Workstreams** — strategy templates (technical foundation, topical authority, content refresh, skyscraper, local,
  AI visibility AIO/AEO/GEO/SXO, link building, brand research, social, paid social, reporting) become activities done by
  a person, several people, a team or an AI agent / prompt skill.
* **Read-only snapshot build** — `VITE_DEMO=1 npx vite build --base ./` produces the UI served from recorded API
  responses (used for the shareable dashboard); writes are refused.
