# Requirement traceability

Generated from `Genranq_Build_Plan.xlsx` (all nine sheets). Regenerate with `python -m app.spec.export`.

Modules: 76 · live 17 · partial 4 · planned 55  
Unmapped backlog features: none · unmapped integrations: none

## 0. Foundation — Platform core (5 weeks, depends on —)

Auth/RBAC, tenancy, projects, tasks, time, queue, CI/CD, observability

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 1 | Overview & KPIs | P1 | **live** | F-005 | activity_log, notification | — | — | / |
| 2 | Tasks | P1 | **live** | F-003 | task | — | — | /tasks |
| 3 | Time Tracking | P1 | **live** | F-003 | time_entry | — | — | /time |
| 4 | Projects | P1 | **live** | F-003 | project | — | — | /projects |
| 5 | Team | P1 | **live** | F-001, F-002 | user, organisation, invite | — | — | /team |
| 45 | Approvals | P1 | **live** | F-028 | approval | — | — | /approvals |

## 1. SEO Core & Audit — Rank & audit (9 weeks, depends on Phase 0)

Rank tracker, crawl, multi-source audit (SF+Ahrefs+Semrush+PSI+GSC), checklist engine, SEO plan, reports

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 6 | Rank Tracker | P1 | **live** | F-011, F-012 | keyword, rank_history | Google Search Console, Semrush | rank_check, scheduled_rank_check | /rank-tracker |
| 7 | SEO Audit | P1 | **live** | F-006, F-007, F-008, F-009, F-010 | audit_run, audit_issue, audit_result, audit_checklist_item, report | Screaming Frog, Ahrefs, Semrush, Google PageSpeed Insights, Google Search Console, Claude (Anthropic) | audit, report | /audits |
| 8 | Screaming Frog Automation | P1 | **live** — Built-in crawler is live; Screaming Frog CLI engine activates when connected in Integrations. | F-006 | crawl_run, crawl_page | Screaming Frog | audit | /crawls |
| 11 | Keywords | P2 | **partial** — Keyword management only; research/clustering/difficulty need Ahrefs/Semrush data (Phase 2) and AI (Phase 4). | F-050 | keyword | Ahrefs, Semrush | — | /keywords |
| 12 | Rankings | P1 | **live** | F-011 | rank_history | Google Search Console | — | /rank-tracker |
| 49 | Integrations Hub | P1 | **live** | F-038 | integration_connection, rate_limit_bucket | Xero / MYOB / QuickBooks | integration_health | /integrations |
| 55 | PageSpeed / Core Web Vitals | P1 | **live** | F-046 | pagespeed_result | Google PageSpeed Insights | pagespeed | /pagespeed |
| 56 | Search Console Audit | P1 | **live** | F-047 | gsc_issue | Google Search Console | audit | /integrations/gsc |
| 57 | Automated Audit Checklist | P1 | **live** | F-044, F-045 | audit_checklist_item, audit_result | Screaming Frog, Ahrefs, Semrush, Google PageSpeed Insights, Google Search Console, Google Analytics 4 | audit | /checklist |
| 58 | Competitor Audit (site & page) | P2 | **partial** — Whole-site competitor audit + score comparison live; page-to-page comparison pending. | F-048 | competitor, audit_run | Ahrefs, Semrush | audit | /competitors |
| 59 | SEO Action Plan | P1 | **live** | F-049 | seo_plan, task | — | audit | /action-plans |

## 2. Backlink CRM — Differentiator (9 weeks, depends on Phase 0)

Backlink DB, supplier CRM, live/removed verify, files, toxic, vault

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 9 | Ahrefs Integration | P2 | **planned** | F-044 | backlink | Ahrefs | — | /m/9 |
| 10 | Semrush Integration | P2 | **planned** | F-016, F-044 | backlink | Semrush | — | /m/10 |
| 13 | Backlinks (CRM) | P1 | **planned** | F-013, F-014, F-015, F-016, F-017, F-019 | supplier, backlink, backlink_check_history, backlink_file, credential | Semrush, Ahrefs | — | /m/13 |
| 48 | Credential Vault | P1 | **live** | F-018 | credential, vault.secret | — | — | /vault |

## 3. Local SEO — Differentiator (9 weeks, depends on Phase 1)

GMB/GBP audit, category, review/post audit, geo-grid Teleport, Website↔GBP

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 15 | GMB/GBP Audit | P1 | **planned** | F-021 | gbp_location | Google Business Profile | — | /m/15 |
| 16 | Category Finder | P2 | **planned** | F-023 | gbp_location | Google Business Profile, Google Maps / Places | — | /m/16 |
| 17 | Review Audit | P1 | **planned** | F-022 | gbp_review | Google Business Profile, Claude (Anthropic) | — | /m/17 |
| 18 | Post Audit | P2 | **planned** | F-021 | gbp_post | Google Business Profile | — | /m/18 |
| 19 | Local Scan | P2 | **planned** | F-021 | gbp_location, competitor | Google Maps / Places | — | /m/19 |
| 20 | Rank / Teleport (geo-grid) | P1 | **planned** | F-020 | grid_scan, grid_point | Google Maps / Places | — | /m/20 |
| 21 | Competitor Research | P2 | **planned** | F-023 | competitor | Google Maps / Places | — | /m/21 |
| 22 | Website ↔ GBP Audit | P2 | **planned** | F-024 | gbp_location, site | Google Business Profile | — | /m/22 |

## 4. AI Engine & Studio — AI + content (10 weeks, depends on Phase 0)

AI provider hub, prompt library, skills, agent teams; content state machine + generators, topical map, content plan, image gen

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 24 | Blog Generator | P1 | **planned** | F-025, F-026 | content_item, content_version | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/24 |
| 25 | News Generator | P2 | **planned** | F-026 | content_item, content_version | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/25 |
| 26 | Whitepaper Generator | P3 | **planned** | F-026 | content_item, content_version | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/26 |
| 27 | Publication Generator | P3 | **planned** | F-026 | content_item | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/27 |
| 28 | Playbook Generator | P3 | **planned** | F-026 | content_item, content_version | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/28 |
| 29 | Website Generator | P2 | **planned** | F-052 | content_item | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/29 |
| 30 | Social Posts | P1 | **planned** | F-026 | content_item | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/30 |
| 31 | GMB Posts | P1 | **planned** | F-026 | content_item, gbp_post | Google Business Profile, Claude (Anthropic) | — | /m/31 |
| 32 | Image Generator | P2 | **planned** | F-027 | content_item | Google Gemini, OpenAI (ChatGPT) | — | /m/32 |
| 50 | AI Provider Hub | P1 | **planned** | F-039 | ai_provider | Claude (Anthropic), OpenAI (ChatGPT), Perplexity, Google Gemini | — | /m/50 |
| 51 | Prompt Library | P1 | **planned** | F-040 | prompt | — | — | /m/51 |
| 52 | Skills System | P1 | **planned** | F-041 | skill | — | — | /m/52 |
| 53 | AI Agent Teams | P2 | **planned** | F-042 | agent, agent_team, agent_run | — | — | /m/53 |
| 54 | AI Cost & Usage | P2 | **planned** | F-043 | ai_usage | Claude (Anthropic), OpenAI (ChatGPT), Perplexity, Google Gemini | — | /m/54 |
| 60 | Landing Page Generator | P1 | **planned** | F-052 | content_item | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/60 |
| 61 | Topical Map | P1 | **planned** | F-050 | topical_map, topic_node | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/61 |
| 62 | Content Plan Builder | P1 | **planned** | F-051 | content_plan, content_brief | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/62 |

## 5. Social & Automation — Publish (7 weeks, depends on Phase 4)

Connectors, calendar, scheduler, bulk gen+schedule, alerts, report automation

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 33 | Content Automation | P1 | **planned** | F-025, F-028 | content_item, schedule, publish_job | Claude (Anthropic) | — | /m/33 |
| 34 | SEO Automation | P1 | **live** | F-004 | job_schedule, job | Screaming Frog, Google Search Console, Google PageSpeed Insights | scheduled_audit, scheduled_rank_check | /automation |
| 35 | GMB Automation | P2 | **planned** | F-030 | gbp_post, schedule | Google Business Profile | — | /m/35 |
| 36 | Social Automation | P1 | **planned** | F-030 | schedule, publish_job, campaign | Meta (Facebook/Instagram), LinkedIn, X (Twitter) | — | /m/36 |
| 37 | Report Automation | P1 | **partial** — On-demand PDF/Excel export live; scheduled report delivery needs email/Teams delivery (Phase 5/6). | F-010, F-036 | report, job_schedule | Looker Studio | report | /reports |
| 38 | Social Accounts | P1 | **planned** | F-029 | integration_connection | Meta (Facebook/Instagram), LinkedIn, X (Twitter) | — | /m/38 |
| 39 | Content Calendar | P1 | **planned** | F-030, F-028 | schedule, content_item | — | — | /m/39 |
| 40 | Analytics | P2 | **planned** | F-031 | — | Meta (Facebook/Instagram), LinkedIn, X (Twitter) | — | /m/40 |

## 6. Leads, Marketplace & Integrations — Growth (9 weeks, depends on Phase 1)

Forms & lead capture, attribution, HubSpot, Calendly, Teams, free tools, services & backlink marketplace, wallet, white-label

| # | Module | Pri | Status | Features | Tables | Integrations | Jobs | Route |
|---|---|---|---|---|---|---|---|---|
| 14 | Backlink Marketplace | P3 | **planned** | F-059, F-060 | marketplace_listing, order | Payment gateway (Stripe) | — | /m/14 |
| 23 | Chrome Extension | P3 | **planned** | — | — | — | — | /m/23 |
| 41 | Leads & Forms | P1 | **planned** | F-032 | lead, form | Meta (Facebook/Instagram), LinkedIn, Google Ads | — | /m/41 |
| 42 | Attribution | P2 | **planned** | F-033 | lead, campaign | Google Analytics 4, Google Ads, Microsoft Ads / Bing WMT | — | /m/42 |
| 43 | HubSpot | P2 | **planned** | F-034 | lead | HubSpot | — | /m/43 |
| 44 | Calendly | P3 | **planned** | F-035 | lead | Calendly | — | /m/44 |
| 46 | Microsoft Teams | P2 | **planned** | F-037 | integration_connection | Microsoft Teams | — | /m/46 |
| 47 | White-Label | P2 | **partial** — Report branding + white-label PDFs live; custom domains need DNS/TLS provisioning (Phase 6). | F-010 | organisation | — | — | /settings/branding |
| 63 | ReleVink AI (placement finder) | P2 | **planned** | F-053 | — | Claude (Anthropic), OpenAI (ChatGPT), Google Gemini, Perplexity | — | /m/63 |
| 64 | Form Builder | P1 | **planned** | F-054 | form, form_submission | — | — | /m/64 |
| 65 | Form Connectors | P2 | **planned** | F-055 | form, form_submission | — | — | /m/65 |
| 66 | Onboarding / Profiling | P2 | **planned** | F-056 | lead | — | — | /m/66 |
| 67 | Domain Age Checker | P2 | **planned** | F-057 | free_tool_lead | — | — | /m/67 |
| 68 | Broken Link Checker | P2 | **planned** | F-057 | free_tool_lead | — | — | /m/68 |
| 69 | Link Analyzer | P2 | **planned** | F-057 | free_tool_lead | — | — | /m/69 |
| 70 | Backlink Monitoring Tool | P2 | **planned** | F-058 | free_tool_lead | — | — | /m/70 |
| 71 | Dofollow/Nofollow Checker | P3 | **planned** | F-057 | free_tool_lead | — | — | /m/71 |
| 72 | Website Finder (extension) | P3 | **planned** | F-057 | free_tool_lead | — | — | /m/72 |
| 73 | Services Catalog | P2 | **planned** | F-062 | service | — | — | /m/73 |
| 74 | Listings & Filters | P2 | **planned** | F-059 | marketplace_listing | — | — | /m/74 |
| 75 | Cart & Orders | P2 | **planned** | F-060 | order | — | — | /m/75 |
| 76 | Wallet & Balance | P2 | **planned** | F-061 | wallet, wallet_txn | Payment gateway (Stripe) | — | /m/76 |

## Audit checklist → detector

| Code | Category | Check | Source | Auto | Severity | Owner |
|---|---|---|---|---|---|---|
| CHK-01 | Indexation | Important pages are indexed / not missing from index | GSC + Screaming Frog | Y | High | SEO Tech |
| CHK-02 | Indexation | Pages indexed that shouldn't be (index bloat) | GSC + Crawler | Y | Med | SEO Tech |
| CHK-03 | Indexation | Noindex on pages that should rank | Screaming Frog | Y | High | SEO Tech |
| CHK-04 | Indexation | Pages blocked by robots.txt that should be indexed | GSC + robots parse | Y | High | Web Dev |
| CHK-05 | Indexation | robots.txt exists and references sitemap | robots parse | Y | Low | SEO Tech |
| CHK-06 | Indexation | XML & HTML sitemaps exist and submitted to GSC | GSC + Crawler | Y | Med | SEO Tech |
| CHK-07 | Indexation | Sitemaps contain only valid, indexable URLs | Crawler + Sitemap crawl | Y | Med | SEO Tech |
| CHK-08 | Indexation | Soft 404 / error pages returning 200 | GSC + Crawler | Y | Med | Web Dev |
| CHK-09 | Indexation | Crawled / Discovered — currently not indexed | GSC | P | Med | SEO Tech |
| CHK-10 | Crawlability | Important content within ~4 clicks of home | Crawler (depth) | Y | Med | SEO |
| CHK-11 | Crawlability | Pages with >300 outlinks | Crawler (outlinks) | Y | Low | SEO |
| CHK-12 | Crawlability | Internal links to 4XX/5XX URLs | Crawler | Y | High | Web Dev |
| CHK-13 | Crawlability | Orphan pages (no internal links in) | Crawler (link graph) | Y | Med | SEO |
| CHK-14 | Crawlability | Site reachable / not timing out / not down | Crawler (status) | Y | High | Web Dev |
| CHK-15 | On-Page | Missing page titles | Crawler | Y | High | SEO Tech |
| CHK-16 | On-Page | Duplicate page titles | Crawler | Y | High | SEO Tech |
| CHK-17 | On-Page | Titles over ~60–65 characters | Crawler | Y | Low | SEO Tech |
| CHK-18 | On-Page | Missing meta descriptions | Crawler | Y | Med | SEO Tech |
| CHK-19 | On-Page | Duplicate meta descriptions | Crawler | Y | Med | SEO Tech |
| CHK-20 | On-Page | Missing H1 | Crawler | Y | High | SEO Tech |
| CHK-21 | On-Page | Duplicate / multiple H1 | Crawler | Y | Med | SEO Tech |
| CHK-22 | On-Page | Heading hierarchy (H1/H2/H3) correct | Crawler | P | Low | SEO Tech |
| CHK-23 | On-Page | Missing image ALT text | Crawler | Y | Med | SEO Tech |
| CHK-24 | On-Page | SEO-friendly, clean URL structure | Crawler | P | Med | Web Dev |
| CHK-25 | On-Page | tel: / mailto: tags present where relevant | Crawler | Y | Low | Web Dev |
| CHK-26 | Content | Internal duplicate content | Crawler (body hash) | Y | High | Content |
| CHK-27 | Content | External duplicate / scraped content | Semrush/Ahrefs + AI | P | Med | Content |
| CHK-28 | Content | Thin content pages | Crawler + AI | P | Med | Content |
| CHK-29 | Content | Keyword cannibalisation | Rankings + AI | P | Med | SEO |
| CHK-30 | Content | Keyword density positive/negative | Crawler + AI | P | Low | Content |
| CHK-31 | Technical | HTTPS everywhere; no insecure pages w/ SSL | Crawler | Y | High | Web Dev |
| CHK-32 | Technical | HTTP and HTTPS both serve (protocol dup) | Crawler | Y | High | Web Dev |
| CHK-33 | Technical | Canonical tags present & correct | Crawler | Y | High | SEO Tech |
| CHK-34 | Technical | Redirect chains / loops | Crawler (reports) | Y | Med | Web Dev |
| CHK-35 | Technical | Non-301 (302/307) / JS / meta-refresh redirects | Crawler | Y | Med | Web Dev |
| CHK-36 | Technical | Broken outbound links | Crawler | Y | Med | Web Dev |
| CHK-37 | Technical | 404 errors | Crawler + GSC | Y | Med | Web Dev |
| CHK-38 | Technical | Intrusive interstitials / aggressive pop-ups | Manual + AI | P | Med | UX |
| CHK-39 | Rendering | Content visible with JS rendering | Crawler (render) | Y | High | Web Dev |
| CHK-40 | Rendering | Crucial content not in iframe / Flash | Crawler | Y | Med | Web Dev |
| CHK-41 | Rendering | Cache / rendering issues per URL | Crawler + PSI | P | Low | Web Dev |
| CHK-42 | Speed & CWV | Loads under 3s (desktop & mobile) | PageSpeed Insights | Y | High | Web Dev |
| CHK-43 | Speed & CWV | Core Web Vitals: LCP / CLS / INP pass | PageSpeed Insights | Y | High | Web Dev |
| CHK-44 | Speed & CWV | GZIP / Brotli compression enabled | PSI + Crawler | Y | Low | Web Dev |
| CHK-45 | Speed & CWV | CSS / JS minified | PSI + Crawler | Y | Low | Web Dev |
| CHK-46 | Speed & CWV | Images compressed / next-gen formats | PSI | Y | Med | Web Dev |
| CHK-47 | Mobile | Mobile-friendly / responsive on all devices | PSI + Crawler | Y | High | Web Dev |
| CHK-48 | Mobile | Legible fonts & tappable targets | PSI | P | Low | UX |
| CHK-49 | Mobile | Mobile parity with desktop (links/content) | Crawler (UA) | P | Med | Web Dev |
| CHK-50 | Structured Data | Schema present on key templates | Crawler + Rich Results | Y | Med | SEO Tech |
| CHK-51 | Structured Data | Schema errors / not spammy | Crawler + Rich Results | Y | Med | SEO Tech |
| CHK-52 | International | hreflang implemented & reciprocal | Crawler | Y | Med | SEO Tech |
| CHK-53 | International | GSC international targeting set | GSC | P | Low | SEO |
| CHK-54 | Off-Page | Backlink profile & referring domains | Ahrefs + Semrush | Y | Med | SEO |
| CHK-55 | Off-Page | Toxic backlinks flagged | Semrush + engine | Y | High | SEO |
| CHK-56 | Off-Page | Lost / removed links | Ahrefs + Backlink CRM | Y | Med | SEO |
| CHK-57 | Off-Page | Link intersect vs competitors (gaps) | Ahrefs | P | Med | SEO |
| CHK-58 | Setup | Analytics (GA4) installed | Crawler + GA | Y | High | Web Dev |
| CHK-59 | Setup | Search Console verified & connected | GSC | Y | High | Web Dev |
| CHK-60 | Setup | Social profiles linked on site | Crawler | Y | Low | SEO |
| CHK-61 | Setup | Google Map linked (contact/footer) | Crawler | Y | Low | SEO |
| CHK-62 | Local | GBP complete & NAP consistent with site | GBP + Crawler | Y | High | SEO |

Every code above has an executable detector in `backend/app/services/checks.py` (verified by `tests/test_spec.py`).

## Integrations → connector

| Provider | Category | Auth | Phase | Connector key |
|---|---|---|---|---|
| Claude (Anthropic) | AI | API key | 4 | `claude` |
| Ahrefs | SEO data | API key | 2 | `ahrefs` |
| Semrush | SEO data | API key | 2 | `semrush` |
| Google Search Console | SEO data | OAuth2 | 1 | `gsc` |
| Google Business Profile | Local | OAuth2 | 3 | `gbp` |
| Google Maps / Places | Local | API key | 3 | `google_maps` |
| Google Analytics 4 | Analytics | OAuth2 | 1 | `ga4` |
| Google Ads | Ads | OAuth2 | 6 | `google_ads` |
| Meta (Facebook/Instagram) | Social | OAuth2 | 5 | `meta` |
| LinkedIn | Social | OAuth2 | 5 | `linkedin` |
| X (Twitter) | Social | OAuth2 | 5 | `x` |
| Microsoft Ads / Bing WMT | SEO/Ads | OAuth2 | 6 | `microsoft_ads_bing` |
| HubSpot | CRM | OAuth2 | 6 | `hubspot` |
| Calendly | CRM | OAuth2/API | 6 | `calendly` |
| Microsoft Teams | Notify | OAuth2 / webhook | 6 | `ms_teams` |
| Screaming Frog | Crawl | License/CLI | 1 | `screaming_frog` |
| Xero / MYOB / QuickBooks | Billing | OAuth2 | 6 | `accounting` |
| Looker Studio | Reporting | OAuth2 | 1 | `looker_studio` |
| OpenAI (ChatGPT) | AI | API key | 4 | `openai` |
| Perplexity | AI | API key | 4 | `perplexity` |
| Google Gemini | AI | API key | 4 | `gemini` |
| Google PageSpeed Insights | SEO data | API key | 1 | `pagespeed` |
| Payment gateway (Stripe) | Billing | API key | 6 | `stripe` |
