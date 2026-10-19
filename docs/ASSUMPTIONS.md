# Build assumptions and decisions

The Build Plan workbook is the source of truth. This file lists every place the build had to decide something the workbook does not state. In the code, the matching spots are marked `[ASSUMPTION]`.

## Source and scope
- **Source file.** The prompt's Google Drive link was the `[PASTE…]` placeholder and no Drive connector was available, so the attached `Genranq_Build_Plan.xlsx` was used. It is bundled at `backend/app/spec/Genranq_Build_Plan.xlsx`. `POST /api/spec/refresh/drive` reads it from Drive once `SPEC_DRIVE_FILE_ID` and a read-only service account are configured. The service account uses the `drive.readonly` scope and never writes.
- **Build order follows the Roadmap sheet.** Phase 0 (Foundation) and Phase 1 (SEO Core & Audit) are fully functional. The Credential Vault and Integrations Hub were pulled forward because Phase 1 depends on them. Phases 2–6 have:
  - their complete database tables,
  - navigation entries,
  - module pages showing their backlog features, tables, integrations and roadmap phase.

  They are labelled "planned", not faked.
- **Cost Estimate.** It is parsed for planning only. It is removed from every customer API (`public_spec`) and is visible only to platform administrators at `/spec/internal/cost-estimate`.

## Database
- **Tenant key on every table.** `organisation_id` is added to every tenant-owned table, including those the sheet links only through `project_id`. Postgres row-level security then enforces isolation with one predicate. The sheet's `form.org_id` is stored as `organisation_id` for consistency.
- **Compound column names are split.** Spec columns such as `name/email/phone`, `country/lang`, `dr/da/authority` and `first_detected/last_verified` become one column each, keeping the sheet's names.
- **Surrogate primary keys** are added where the sheet gives none:
  - `rank_history`, `backlink_check_history`, `grid_point` and `ai_usage` get bigserial keys;
  - `wallet` gets a uuid.
- **Time-series tables** that the sheet marks for ClickHouse live in Postgres with composite indexes. This applies to `rank_history`, `backlink_check_history`, `ai_usage` and `pagespeed_result`. A ClickHouse sink is a later infrastructure step.
- **Platform tables the spec references but does not define:**
  - `task`, `time_entry`, `approval`, `notification`, `invite`, `refresh_token`
  - `job`, `job_schedule`, `rate_limit_bucket`, `integration_connection`, `oauth_state`
  - `stored_file`, `report`, `usage_counter`, `spec_snapshot`, `crawl_page`
  - `vault.secret`
- **Credentials are stored as references.** `credential.vault_ref` points into a separate `vault` schema. Envelope encryption is used: AES-256-GCM data keys, wrapped by `VAULT_MASTER_KEY`, with the tenant id as associated data. The API database role has no access to that schema. In production the master key belongs in a KMS.

## Roles and permissions
- **Roles** are those in the sheet: owner, admin, manager, staff, client. The permission matrix (`backend/app/rbac.py`) is not in the sheet:

  | Role | Permissions |
  |---|---|
  | Owner | everything |
  | Admin | everything except billing |
  | Manager | create, edit, delete, approve, publish, export, credentials |
  | Staff | view, create, edit, export |
  | Client | view and export, limited to assigned projects |

- **Platform administrators** (Genranq operator staff) are set only with the CLI: `python -m app.admin grant-platform-admin <email> <org-slug>`. The API database role cannot write that column.

## Pricing Matrix → entitlements
- **Prices.** The sheet has no prices, so none are shown. Plan changes are recorded, and payment is marked "Stripe subscription billing NOT CONNECTED". The sheet scopes Stripe to the wallet and marketplace (Phase 6).
- **"250 / mo" keyword rows** are treated as the maximum number of tracked keywords. "SEO Audit pages N / mo" is metered per month. Each audit reserves pages up front and refunds the unused ones.
- **"Custom" (Enterprise)** means unlimited until a per-organisation override is set.
- **"Audit frequency: Real-time only"** blocks scheduled audits on Essentials.
- **Rows mapped to enforced checks:**

  | Pricing Matrix row | Enforced as |
  |---|---|
  | Client user access | Enterprise-only `client` role |
  | Report white-label | white-label export and branding |
  | Multiple assignee / filters / bulk | multi-assignee tasks and bulk edit |

## Audit engine
- **Severity.** The sheet has High, Med and Low. The prompt also asks for Critical, so a High check becomes **critical** when it fails site-wide or on at least 25% of the pages it applies to.
- **Score.**
  - Weights: High 10, Med 5, Low 2.
  - Each check scores: pass 1, partial 0.5, fail 0.5 × (1 − prevalence). A site-level fail scores 0.
  - Checks graded not run, manual or N/A are excluded from the score and reported as **coverage**.
- **Priority** follows F-008: impact × (0.2 + 0.8 × prevalence) × 1/effort.
  - Impact: critical 10, high 7, medium 4, low 1.5.
  - Effort by fix owner: SEO Tech 1, SEO 1.5, UX 2, Content 2.5, Web Dev 2.5.
- **Checks the data sources cannot fully answer** are graded honestly:
  - CHK-27 (external duplicate content) and CHK-53 (GSC international targeting) have no API source, so they are `manual`.
  - Checks needing GSC, PSI, Ahrefs, Semrush or GBP are `not_run` until that integration is connected.
  - Crawler-only evidence is marked `partial` where the sheet expects a second source.
- **Action-plan buckets.** "Now (week 1-2)" holds critical and high issues with low effort. "Next (week 3-6)" holds the rest of critical, high and medium. "Later" holds low.

## Crawler
- **Built-in engine.** The crawler is built in. Screaming Frog becomes the engine once its CLI is installed on the worker (path set by the operator through `SCREAMING_FROG_CLI`) and the integration is connected.
- **SSRF protection:**
  - Only globally routable addresses are allowed.
  - The address is checked on every redirect hop and again on the connected socket, which covers DNS rebinding.
  - The rendering browser has request interception.
  - Response bodies are capped at 5 MB.
- **JavaScript rendering comparison** (CHK-39) uses Playwright when it is installed.

## Integrations
- All 23 providers in the API Integrations sheet have a connector with:
  - credential storage, a health check and connection status,
  - retry with backoff, token-bucket rate limiting,
  - last sync time and disconnect.
- **OAuth providers** need platform OAuth app credentials in the environment. Until those are set they show "NOT CONNECTED". The OAuth `state` value is single-use and bound to the initiating browser; X uses PKCE.
- **Rank data sources** are Google Search Console, then Semrush. Without either, positions come from CSV import or manual entry. The sheet lists no SERP-scraping API, and none was added.

## Not built in this release (per Roadmap)
- Phases 2–6. Their tables already exist.
- Email delivery: invites return a one-time link to share.
- Custom-domain provisioning.
- SSO/OIDC login flow: only the status endpoint exists.
- ClickHouse sink.
