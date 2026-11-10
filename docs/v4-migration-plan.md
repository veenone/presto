# PRESTO v4 — Migration Plan: Django + HTMX + Alpine.js

**Status:** In progress (rev. 7: Phases 0 to 5 complete, see §17)
**Date:** 2026-11-09
**Baseline:** v3.4.0 (FastAPI + SQLAlchemy backend, NiceGUI / Streamlit / C# WinForms frontends)

---

## Table of Contents

1. [Goals and Non-Goals](#1-goals-and-non-goals)
2. [Current State Analysis (v3.4)](#2-current-state-analysis-v34)
3. [Target Architecture (v4)](#3-target-architecture-v4)
4. [Key Design Decisions](#4-key-design-decisions)
5. [Feature Parity Matrix](#5-feature-parity-matrix)
6. [Migration Phases](#6-migration-phases)
7. [Step-by-Step Execution Guide](#7-step-by-step-execution-guide)
8. [Target Project Structure](#8-target-project-structure)
9. [HTMX + Alpine Patterns per Screen Type](#9-htmx--alpine-patterns-per-screen-type)
10. [Data Migration Strategy](#10-data-migration-strategy)
11. [Testing Strategy](#11-testing-strategy)
12. [Deployment and CI Changes](#12-deployment-and-ci-changes)
13. [Risks and Mitigations](#13-risks-and-mitigations)
14. [Definition of Done](#14-definition-of-done)
15. [Timeline Estimate](#15-timeline-estimate)
16. [Decisions](#16-decisions)

---

## 1. Goals and Non-Goals

### Goals

| # | Goal | Measure |
|---|------|---------|
| G1 | **Keep the full v3.4 feature set** | Every row in the parity matrix (§5) is checked off before release |
| G2 | **Django as the single backend** | One process serves pages, REST API, auth, reports, integrations |
| G3 | **HTMX + Alpine.js frontend** | Server-rendered templates, partial swaps for interactivity, Alpine for local client state; no SPA build step |
| G4 | **Zero data loss on upgrade** | Existing SQLite/MySQL databases upgrade in place or via a one-shot import command |
| G5 | **REST API kept as a feature** | `/api/*` with OpenAPI docs stays available for integrations (Redmine webhook) and future automation. No desktop client depends on it, so endpoint shapes may be cleaned up where it simplifies the port |
| G6 | **Simpler runtime** | One Docker-based web service + one optional scheduler, instead of three co-running frontends and a desktop client |
| G7 | **All administration inside the app** | Every admin task is done from PRESTO's own pages; Django admin is not installed |

### Non-Goals

- Redesigning the estimation formula, feasibility rules, or report content. The `engine/` package is moved, not rewritten.
- Keeping the C# desktop app. It is retired; the Docker-hosted web app replaces it (decision D1).
- Adding new features beyond parity. New work queues for v4.1+.
- Introducing a JavaScript build pipeline (Node, bundlers, React/Vue). HTMX and Alpine load from vendored static files.

---

## 2. Current State Analysis (v3.4)

### 2.1 Codebase shape

| Area | Location | Size | Notes |
|------|----------|------|-------|
| REST API | `backend/src/api/routes.py` | 4,784 lines, **138 router endpoints** (+ 2 unauthenticated app-level: healthcheck, host-config) in one file | Single `APIRouter`; Pydantic schemas in `schemas.py` (801 lines) |
| ORM models | `backend/src/database/models.py`, `auth/models.py` | **25 tables** | SQLAlchemy 2.0 declarative; JSON stored as text columns |
| Migrations | `backend/src/database/migrations.py` | Hand-written, `SCHEMA_VERSION = 24` | Version stored in `configuration` table; `_ensure_config_keys()` seeds config defaults |
| Estimation engine | `backend/src/engine/` | 4 modules, pure dataclasses | No DB imports — portable as-is |
| Auth | `backend/src/auth/` | JWT (PyJWT + bcrypt), LDAP (ldap3), OIDC (authlib) | RBAC matrix stored as JSON in `configuration.rbac_matrix`; 18 permission keys; 4 roles |
| Integrations | `backend/src/integrations/` | 5 adapters + dispatcher + asyncio poll scheduler | Redmine, Jira/Xray, SMTP, Outline, Snipe-IT |
| Reports | `backend/src/reports/` | Excel, Word, PDF, comparison, trend, executive summary | openpyxl / python-docx / ReportLab / matplotlib |
| Notifications | `backend/src/notifications/` | SMTP + HTML templates; in-app `webhook_notifications` table | Bell icon polls `/notifications/unread-count` every 30 s |
| Imports | `backend/src/imports/` | CSV/Excel preview + execute per entity type | |
| Desktop IPC | `backend/src/cli/ipc_handler.py` | 1,698 lines, 58 commands | Legacy; removed in v4 together with the desktop app |
| NiceGUI frontend | `frontend_nicegui/` | ~17,500 lines, 25 routed pages | `estimation.py` alone is 4,959 lines (wizard + detail + edit) |
| Streamlit frontend | `frontend_web/` | ~7,500 lines, 13 pages | Lags behind NiceGUI on features (no presets, risks, holidays, backup…) |
| Desktop frontend | `frontend_desktop/` | C# WinForms .NET 8 | Retired in v4 (D1); kept only on `v3-maintenance` |
| Tests | `backend/tests/` | **299 test functions**, 11 modules | pytest, FastAPI `TestClient`, temp SQLite per test |

### 2.2 Routed pages today (NiceGUI, the reference UI)

| Section | Routes |
|---------|--------|
| Overview | `/` dashboard, `/requests`, `/requests/{id}` |
| Estimation | `/estimations`, `/estimation/new`, `/estimation/{id}`, `/estimation/{id}/edit` |
| Data Management | `/features`, `/feature-presets`, `/tasks`, `/duts`, `/assets`, `/profiles`, `/history`, `/team`, `/risks`, `/pr-registry`, `/document-types`, `/public-holidays` |
| Administration | `/settings`, `/integrations`, `/users`, `/rbac`, `/audit`, `/admin/backup` |
| Auth | `/login` |

### 2.3 Behaviours that need deliberate re-implementation (not just template conversion)

These rely on NiceGUI's persistent WebSocket and server-side Python state. Each needs an explicit HTMX/Alpine design:

| Behaviour | Where | v3 mechanism | v4 approach (see §9) |
|-----------|-------|--------------|----------------------|
| Wizard autosave every 30 s + first save after 5 s | `/estimation/new`, `/edit` | `ui.timer` → `PUT /estimations/{id}/draft` | `hx-trigger="every 30s"` on the form, posts to a draft endpoint; Alpine tracks dirty state |
| Live "Calculate" preview | wizard step 7 | server call, re-render cards in place | `hx-post` to a `calculate-preview` partial, swap into result container |
| Notification bell badge | sidebar | `ui.timer(30s)` polling | `hx-get` with `hx-trigger="every 30s"` on a tiny partial |
| Jira PR import dialog | wizard step 5 | NiceGUI dialog + table | HTMX-loaded modal partial; Alpine for row selection |
| ECharts dashboard | `/` | `ui.echart` | Vendored ECharts, initialised by Alpine on `htmx:afterSettle`; data via JSON script tag |
| Dark/light theme | global | `app.storage.user` | Stored per user in the DB (`users.theme_preference`); `localStorage` as fallback before login or if unset; applied via `data-theme` |
| Per-tab auth state | global | `app.storage.user` cookie | Django session (HttpOnly cookie) for web; JWT kept for `/api/*` |
| Multi-select bulk edit (DUTs, users) | tables | Quasar table selection | Alpine `x-data` holding selected IDs; `hx-include` on bulk action buttons |
| File download with feedback | reports | `ui.run_javascript` | Plain `<a download>` to report view; Alpine spinner on `htmx:beforeRequest` for async variants |
| Markdown + Jira macro rendering | PR description | Python `render_jira_markup` | Same function as a Django template filter |
| Redmine auto-sync polling | backend | asyncio task in FastAPI lifespan | Management command run by a scheduler container (or `django-q2`), see §4.6 |

### 2.4 Pain points v4 should fix on the way

- One 4,784-line routes file and one 4,959-line page file make review and onboarding slow.
- Three frontends with uneven parity (Streamlit missing ~10 pages).
- Hand-rolled migration ladder with 24 steps and no down-migrations.
- JSON-in-text columns (`wizard_inputs_json`, `skills_json`, `feature_ids_json`, …) with ad-hoc parsing.
- Two sources of truth for the default RBAC matrix (`app.py` and `pages/rbac.py`).
- Config values typed as strings everywhere (`"true"`, `"587"`), parsed at every call site.

---

## 3. Target Architecture (v4)

```
┌─────────────────────────────────────────────────────────────────┐
│ Browser                                                         │
│   HTML (Django templates) + htmx 2.x + Alpine.js 3.x            │
│   Vendored static: htmx.min.js, alpine.min.js, echarts.min.js,  │
│   app.css (CSS variables, light/dark via data-theme)            │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP (full pages + HTML partials)
┌──────────────────────────────▼──────────────────────────────────┐
│ Django 5.2 LTS (ASGI via uvicorn), Docker service               │
│                                                                  │
│  presto/            project settings, urls, asgi                 │
│  apps/core/         Configuration, branding, dashboard           │
│  apps/accounts/     User, sessions, RBAC, LDAP, OIDC, audit      │
│  apps/catalog/      Feature, TaskTemplate, Preset, DUT, Profile, │
│                     DocumentType, RiskItem, PublicHoliday        │
│  apps/estimations/  Estimation, tasks, versions, wizard views    │
│  apps/inbox/        Request inbox, attachments, assignment       │
│  apps/teams/        Team, TeamMember, allocation                 │
│  apps/integrations/ adapters, sync, webhooks, scheduler cmd      │
│  apps/reports/      xlsx/docx/pdf/trend/comparison/exec summary  │
│  apps/notifications/ in-app + SMTP                               │
│  apps/api/          REST API (/api/*) — django-ninja, JWT auth   │
│  engine/            PURE calculation package (moved verbatim)    │
│                                                                  │
│  Templates: templates/<app>/{page}.html + partials/_*.html       │
└──────────────────────────────┬──────────────────────────────────┘
                               │ Django ORM
┌──────────────────────────────▼──────────────────────────────────┐
│ SQLite (default)  /  MySQL (DB_URL)                             │
└─────────────────────────────────────────────────────────────────┘

Automation / scripts ──── HTTP /api/* (JWT) ─────▶ apps/api
Redmine webhook ───────── POST /api/webhooks/redmine ─▶ apps/api
Scheduler (optional container) ── `manage.py sync_redmine --loop`
```

**Request flow for a typical page:**

1. Full navigation → Django view renders `page.html` extending `base.html` (sidebar, bell, theme).
2. User action → `hx-get/post` to a view returning a partial (`partials/_table.html`, `_form.html`, `_modal.html`).
3. Server-side validation → re-render the form partial with errors, HTTP 422 handled by `htmx:responseError` → inline banner.
4. Side effects (toast, close modal, refresh another region) → `HX-Trigger` response header consumed by Alpine listeners.

---

## 4. Key Design Decisions

### 4.1 ORM: Django ORM, not SQLAlchemy inside Django

**Decision:** Port all 25 tables to Django models. Keep identical table names and column names via `Meta.db_table` and explicit `db_column` where needed, so an existing v3 database can be adopted with `migrate --fake-initial`.

**Why:** Mixing SQLAlchemy into Django forfeits the admin, forms, auth integration, migrations, and test fixtures that justify Django in the first place.

**Changes allowed in the port:**
- JSON text columns (`*_json`) become `models.JSONField` with the same `db_column`. Django reads existing text JSON correctly on SQLite and MySQL.
- `configuration.value` stays a string column; a typed accessor service (`core.config.get(key, type)`) replaces the per-call parsing.

### 4.2 Auth: Django sessions for web, JWT for `/api/*`

- **Web UI**: `django.contrib.auth` with a custom `User` model (`AUTH_USER_MODEL = "accounts.User"`) mapping the existing `users` table (`username`, `email`, `display_name`, `password_hash` → `password`, `auth_provider`, `external_id`, `role`, `is_active`, `team_member_id`, `last_login_at`). Session cookie, CSRF on, HTMX sends the token via `hx-headers` in `base.html`.
- **Password hashes**: v4 keeps the v3 format (a raw bcrypt string in `password_hash`). `User.set_password` / `check_password` use bcrypt directly, so v3 and v4 verify the same hashes and a rollback never locks users out.
- **API**: keep `POST /api/auth/login` returning access + refresh JWT with the same claims and expiry, signed with the same `jwt_secret` config key. Refresh tokens persist in `user_sessions` as today. With the desktop app retired, the API's only consumers are integrations and future automation, so this is kept for feature parity rather than client compatibility.
- **LDAP / OIDC**: port `LDAPProvider` and `OIDCProvider` as Django authentication backends. Group→role mapping, "preserve admin-assigned role" rule, LDAP sync and reset-synced-users commands all move 1:1.
- **RBAC**: single source of truth in `accounts/permissions.py` (the 18 keys). Enforced by a `@require_permission("manage_duts")` view decorator, a `{% if perms_has "manage_duts" %}` template tag for nav/buttons, and the same check in `apps/api`.

### 4.3 REST API: django-ninja

**Decision:** Use `django-ninja` for `/api/*`.

**Why:** It is Pydantic-based, so the 801 lines of existing schemas port with minimal edits, and it generates the same OpenAPI docs at `/docs`. DRF would force a rewrite of every schema into serializers.

**Coverage rule:** Every v3 endpoint has a v4 equivalent with the same capability. Paths stay the same by default; a path or shape may change only if it is listed in the CHANGELOG. The Redmine webhook path and token check must not change. A coverage test (§11) diffs v3 and v4 OpenAPI documents and fails on any unlisted removal.

**Priority:** the HTMX UI calls services directly, not the API. The API is therefore off the critical path and can be finished in parallel with the UI phases.

### 4.4 Views: thin views, fat services

Business logic that currently lives inline in `routes.py` (estimation create/revise/status, feature promotion, import, backup/restore, dashboard stats) moves into `apps/<app>/services.py`. Both the HTML views and the API views call the same service functions. This guarantees the web UI and the API behave identically.

### 4.5 Frontend stack

| Concern | Choice |
|---------|--------|
| Markup | Django templates with `django-template-partials` for inline partial blocks |
| Interactivity | htmx 2.x (`hx-boost` on the sidebar for SPA-like navigation) |
| Client state | Alpine.js 3.x: stores for theme and selection, `x-data` per component |
| Admin | No `django.contrib.admin`. Users, RBAC, settings, integrations, audit, backup/restore and LDAP tools are all in-app pages (D4) |
| Styling | Hand-written CSS with custom properties (port the existing `ed-*` design tokens), no Tailwind build. Optional: Tailwind via CDN Play is rejected for production. |
| Icons | Material Symbols font (same icon names as today) |
| Charts | ECharts (vendored), configured from JSON emitted by the view |
| Tables | Server-rendered with sorting/pagination/search via `hx-get` query params; `django-filter` for filter forms |
| Forms | Django `ModelForm`s rendered by a shared `_form_field.html` partial |
| Dates | Native `<input type="date">`, with the configurable labels from `label_*` config keys |

### 4.6 Background work

Redmine polling is the only background job. Options, in order of preference:

1. **Management command in a sidecar container**: `python manage.py sync_redmine --loop` reads `poll_interval_minutes` from `integration_config`. Zero new dependencies, same container image, second service in `docker-compose.yml`.
2. `django-q2` with the ORM broker, if more scheduled jobs appear (deadline reminders, scheduled exports).

Decision: option 1 for v4.0.

### 4.7 Streamlit, desktop app and the IPC handler

- `frontend_web/` (Streamlit) and `frontend_nicegui/` are **removed** in v4 once parity is signed off. They are kept on the `v3-maintenance` branch.
- `frontend_desktop/` (C# WinForms) and `backend/src/cli/ipc_handler.py` are **removed** (D1). Users move to the Docker-hosted web app. Both stay on `v3-maintenance`.

### 4.8 Theme preference (D6)

- New column `users.theme_preference` (`light` | `dark` | `NULL`), added by a v4-only migration.
- Resolution order: user DB value → browser `localStorage` → system `prefers-color-scheme` → dark (v3 default).
- The toggle writes `localStorage` immediately and sends `hx-post /me/theme` to persist. The server renders `data-theme` from the DB value, so there is no flash after login.

### 4.9 No Django admin (D4)

- `django.contrib.admin` is not in `INSTALLED_APPS`; no `/admin/` URL is mounted.
- Any admin task found during the port that has no in-app page gets one. Recovery tasks with no UI (create or reset the admin account, restore a backup) are management commands run with `docker compose exec app python manage.py …`.

---

## 5. Feature Parity Matrix

Legend: **Keep** = move as is; **Port** = re-implement in Django/HTMX; **Replace** = different mechanism, same outcome.

### 5.1 Estimation core

| Feature | v3 location | v4 action |
|---------|-------------|-----------|
| 7-step wizard (Project Info, Features, References, DUT×Profile, PR Fixes, Delivery & Team, Review & Save) | `pages/estimation.py` | Port: one view per step, `estimations/wizard/` |
| Draft autosave (30 s, first at 5 s) and "Save draft" | wizard | Replace: `hx-trigger="every 30s[dirty]"` |
| Feature presets bar (apply / save selection as preset) | wizard step 2 | Port |
| Add custom feature scoped to the estimation; request / promote to global | wizard step 2 | Port (service exists in routes) |
| Reference project selection + calibration ratio | step 3 | Port |
| DUT × Profile matrix with combination count | step 4 | Port; Alpine computes the count client-side, server recomputes |
| PR fixes with complexity, priority, `test_available`, per-PR detail rows | step 5 | Port |
| Import PRs from Jira dialog (JQL, select rows, description modal) | step 5 | Port as HTMX modal |
| Configurable date labels, start / testing start / deadline pickers, working-days vs proposed duration | step 6 | Port |
| Team members, team presets, team selection | step 6 | Port |
| Live calculate preview, hours cards, task table, risk flags | step 7 | Port |
| Estimation list with status/feasibility badges, filters | `/estimations` | Port |
| Detail view: tasks, synthetic doc tasks, document deliverables with overlap notes, PR table, team allocation, risks, working weeks, versions | `/estimation/{id}` | Port |
| Edit page (draft edit vs new revision) | `/estimation/{id}/edit` | Port, shares wizard step templates |
| Status workflow DRAFT→FINAL→APPROVED→REVISED→DRAFT with approver stamping | API | Keep (service) |
| Versions list and version diff | API | Keep + Port UI |
| Calibrate endpoint | API | Keep |
| Archive estimation | API | Keep |
| Assign user to estimation | API + UI | Keep + Port |
| Export / Export to External / Export Tasks (Redmine, Jira) | detail | Keep + Port |
| Compare estimations | detail | Keep + Port |
| Send report by email | detail | Keep + Port |
| Reports: xlsx, docx, pdf, executive summary, comparison, trend | `reports/` | Keep (move package) |
| Collapsible PR description with markdown + Jira macros | detail | Port as template filter |

### 5.2 Catalog and data management

| Feature | v4 action |
|---------|-----------|
| Feature catalog CRUD, categories, product type, study hours, task template links with per-feature hour override | Port |
| Feature presets CRUD (estimator self-service, product-type bound, description, admin page) | Port |
| Task templates CRUD, task presets | Port |
| DUT registry CRUD, configurable categories, bulk edit / bulk delete, reinit | Port |
| DUT assets (Snipe-IT list + categories) | Port |
| Test profiles CRUD | Port |
| Historical projects CRUD | Port |
| Teams + team members CRUD, skills, team presets | Port |
| Risk registry CRUD | Port |
| PR registry page | Port |
| Document types CRUD (+ `/all` including inactive) | Port |
| Public holidays CRUD, recurring, ICS import, working-weeks calculator | Port |
| CSV / Excel bulk import with preview, per entity type | Keep + Port UI |

### 5.3 Requests

| Feature | v4 action |
|---------|-----------|
| Request inbox list, detail, create, update, status tracking, reinit | Port |
| Attachments upload per request | Port (Django `FileField`, same `data/attachments/` layout) |
| Assign request to user | Keep + Port |
| External sync from Redmine / Jira with dedupe | Keep |
| Redmine webhook with shared-secret token | Keep (API, CSRF-exempt) |
| Request → estimation linking | Keep |

### 5.4 Administration

| Feature | v4 action |
|---------|-----------|
| Settings page grouped by `SECTION_KEYS`, typed inputs, PR parameters, branding (logo upload + height), calendar colours, date labels, release effort | Port |
| Integrations page: 5 tabs, connection / additional / auto-sync / webhook watchers / field mappings / task breakdown export / X-Ray / PR settings, Test and Sync buttons, status | Port |
| Users: CRUD, search, bulk role assignment, assignable list | Port |
| RBAC matrix editor (18 permissions × 4 roles) | Port |
| Audit log viewer with filters, client IP | Port |
| Backup (JSON download) and restore | Keep + Port |
| LDAP test, sync, reset-synced-users; OIDC login | Keep + Port |
| Change password | Port |
| Notifications: bell, unread count, list, mark read / all read, test email | Port |
| Dashboard: stats cards, ECharts donut/bar for estimations, requests, features, tasks, risks; recent tables | Port |
| Light / dark theme, persistent | Replace: per-user DB preference, browser fallback (§4.8) |
| Error pages 400/401/403/404/422/5xx with Pydantic detail parsing | Port (HTMX `responseError` banner + full-page templates) |
| Build version in sidebar footer (`APP_VERSION`) | Port |
| Healthcheck, host-config (unauthenticated) | Keep |

### 5.5 Infrastructure

| Feature | v4 action |
|---------|-----------|
| SQLite default / MySQL via `DB_URL` | Keep (`dj-database-url`) |
| HTTPS via `SSL_CERTFILE` / `SSL_KEYFILE` | Keep (uvicorn flags) or terminate at nginx |
| CORS via `CORS_ORIGINS` | Keep (`django-cors-headers`, API only) |
| Uploaded files under `data/uploads` | Keep (`MEDIA_ROOT`) |
| Docker image, compose, nginx, Jenkins pipeline | Port (§12) |
| Seed data and default admin on first run | Keep (`manage.py seed`) |
| C# desktop client | Retired (D1) |

---

## 6. Migration Phases

```
Phase 0  Prep & freeze          ─┐
Phase 1  Django skeleton + models│  foundation
Phase 2  API parity (ninja)     ─┘
Phase 3  UI shell + auth        ─┐
Phase 4  Catalog & admin pages   │  frontend
Phase 5  Estimation wizard       │
Phase 6  Requests, dashboard,    │
         integrations UI        ─┘
Phase 7  Data migration & cutover
Phase 8  Cleanup & release
```

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| 0 | `v3-maintenance` branch, v3 OpenAPI snapshot, screenshot baseline, this plan approved | Snapshots committed under `docs/v3-baseline/` |
| 1 | Django project, 25 models, initial migration, `seed` + `create_admin` commands, engine moved | `migrate` on a copy of a production DB succeeds with `--fake-initial`; model tests green |
| 2 | `/api/*` on django-ninja, JWT auth, RBAC, all 140 endpoints | Coverage test passes; 299 ported backend tests green; Redmine webhook verified |
| 3 | `base.html`, sidebar, login (local/LDAP/OIDC), theme, bell, error pages | Login → dashboard placeholder works in both themes |
| 4 | All catalog + admin pages (§5.2, §5.4 except dashboard) | Page-by-page parity checklist signed |
| 5 | Wizard new/edit/detail/list, reports download, exports | Worked example from README reproduces 511.5 h / AT_RISK |
| 6 | Requests, dashboard charts, integrations page, notifications | Parity checklist signed |
| 7 | Upgrade path for existing DBs, compose/Jenkins, nginx, docs | Staging deployed from CI; rollback rehearsed |
| 8 | Remove NiceGUI/Streamlit/desktop/IPC, bump 4.0.0, CHANGELOG, README, screenshots | Release tag |

---

## 7. Step-by-Step Execution Guide

Each step lists the command or file to touch. Work on branch `v4/<phase>-<topic>` and merge to `main` behind a `v4/` folder until Phase 7 flips the default.

### Phase 0 — Prepare and freeze

1. Create `v3-maintenance` from current `main`; tag `v3.4.0`.
2. Snapshot the API contract:
   ```bash
   cd backend && uvicorn src.api.app:app --port 8000 &
   curl -s localhost:8000/openapi.json > ../docs/v3-baseline/openapi-v3.json
   ```
3. Capture screenshots of all 25 pages with the existing Playwright script into `docs/v3-baseline/screenshots/`.
4. Dump a sanitised copy of a real database (`data/estimation.db`) for migration rehearsal; store outside git.
5. Record the current test count (299) and coverage as the floor for v4.

### Phase 1 — Django skeleton and models

1. Create the project in a new top-level folder so v3 keeps running during migration:
   ```bash
   mkdir v4 && cd v4
   python -m venv .venv && . .venv/bin/activate
   pip install "django>=5.2,<5.3" django-ninja dj-database-url django-cors-headers \
               django-template-partials django-filter PyJWT bcrypt ldap3 authlib \
               python-docx openpyxl reportlab matplotlib requests pytest-django
   django-admin startproject presto .
   for a in core accounts catalog estimations requests teams integrations reports notifications api; do
     python manage.py startapp $a apps/$a
   done
   ```
2. Settings (`presto/settings.py`):
   - `DATABASES` from `DB_URL` (fallback `sqlite:///data/estimation.db`), SQLite pragmas `journal_mode=WAL`, `foreign_keys=ON` via a `connection_created` signal.
   - `AUTH_USER_MODEL = "accounts.User"`; `PASSWORD_HASHERS` with legacy bcrypt first.
   - `MEDIA_ROOT = data/uploads`, `STATIC_ROOT`, `WhiteNoise` for static files.
   - `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_HTTPONLY`, `SECURE_PROXY_SSL_HEADER`.
3. Port models table by table. Rule: `class Meta: db_table = "<v3 table>"`, same column names, `JSONField(db_column="..._json")` for JSON text. Order: `configuration`, `users`, `user_sessions`, `audit_log`, then catalog tables, then `estimations` and children, `requests`, `integration_config`, `webhook_notifications`, `public_holidays`, `estimation_version_snapshots`.
4. `python manage.py makemigrations` → one initial migration per app. Verify against a v3 DB:
   ```bash
   cp /path/to/prod-copy.db data/estimation.db
   python manage.py migrate --fake-initial
   python manage.py check && python manage.py showmigrations
   ```
   Any `OperationalError` here means a column mismatch; fix the model, not the DB.
5. Move `backend/src/engine/` to `v4/engine/` unchanged. Move `backend/tests/test_calculator.py`, `test_feasibility.py`, `test_calibration.py`, `test_allocator.py` and run them: they need no Django.
6. Write `core/config.py`: typed getters (`get_str`, `get_float`, `get_bool`, `get_json`, `get_list`) plus `ensure_defaults()` seeded from the union of v3 `_migrate_v1_to_v2` and `_ensure_config_keys()`.
7. Management commands: `seed` (port `_load_seed_data`), `ensure_defaults`, `create_admin` (port `scripts/create_admin.py`), `check_v3_compat`. `sync_redmine --loop` moves to Phase 2, where the integration adapters are ported.

### Phase 2 — REST API parity

1. `apps/api/schemas.py`: copy `backend/src/api/schemas.py`; keep Pydantic v2 models as-is.
2. `apps/api/auth.py`: `HttpBearer` security class that validates the JWT exactly like `AuthService.validate_access_token` and sets `request.user`.
3. Split the v3 `routes.py` into routers by domain: `auth`, `users`, `catalog`, `estimations`, `requests`, `teams`, `configuration`, `integrations`, `notifications`, `reports`, `dashboard`, `admin`. Mount all under `/api` with `NinjaAPI(docs_url="/docs")`.
4. For each endpoint: move the body into `apps/<app>/services.py`, leave the router function as parse → call service → serialise.
5. Port `test_api.py`, `test_phase6_api.py`, `test_auth.py`, `test_integrations.py`, `test_models.py`, `test_reports.py` to `pytest-django`. Replace `TestClient` with `django.test.Client` or ninja's `TestClient`.
6. Coverage test: load `docs/v3-baseline/openapi-v3.json`, compare paths × methods against live `/api/openapi.json`; fail on any removal not listed in the CHANGELOG.
7. Verify the Redmine webhook end to end against a test Redmine (token check, sync result).
8. This phase runs in parallel with Phases 3 to 6; it does not block the UI.

### Phase 3 — UI shell and auth

1. `templates/base.html`: `<html data-theme>`, vendored `htmx.min.js`, `alpine.min.js`, `app.css`; `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` on `<body>`; `hx-boost="true"` on the sidebar.
2. Port the `ed-*` CSS tokens from `frontend_nicegui/app.py` (lines 49–420) into `static/css/app.css` with light/dark variable sets.
3. `templates/partials/_sidebar.html`: sections and items from `app.py` lines 1480–1514, gated by `{% has_perm %}` tag; logo from branding config; version footer.
4. `accounts/views.py`: login (local → LDAP fallback as today), OIDC start/callback, logout, change password. Login page design from `app.py` lines 901–1190.
5. Theme (§4.8): migration adding `users.theme_preference`; `POST /me/theme` view; Alpine store reads DB value from `data-theme`, falls back to `localStorage`, then `prefers-color-scheme`.
6. Notification bell partial: `hx-get="/notifications/badge" hx-trigger="load, every 30s"`; dropdown via `hx-get="/notifications/list"`.
7. Error handling: `templates/errors/{400,401,403,404,422,500}.html`; HTMX `responseError` listener shows the banner partial with parsed detail.

### Phase 4 — Catalog and admin pages

Implement with one reusable "CRUD page" pattern (list partial + modal form partial + delete confirm), then specialise:

1. Build the pattern once on **Test Profiles** (smallest page). Files: `views.py` (list, create, update, delete), `forms.py`, `templates/catalog/profiles.html`, `partials/_profiles_table.html`, `partials/_profile_form.html`.
2. Apply to: features (+ task-template links and override hours), task templates, task presets, feature presets, DUTs (+ bulk select/edit/delete, categories, reinit), historical projects, teams/members (+ presets), risks, PR registry, document types, public holidays (+ ICS upload, working-weeks calculator).
3. Admin: users (+ search via `hx-get` on `keyup changed delay:300ms`, bulk role), RBAC matrix (checkbox grid posting JSON), audit log (filters + pagination), settings (sections from `SECTION_KEYS`, logo upload, test SMTP/LDAP buttons), backup/restore.
4. Bulk import: upload → preview table partial → confirm.

### Phase 5 — Estimation wizard, detail, edit

1. Server-side wizard state = the **draft estimation row** (`wizard_inputs_json`). Step 1 "Next" creates the draft; every later step `PUT`s the draft. This replaces NiceGUI's in-memory `state` dict and makes autosave trivial.
2. URLs: `/estimation/new` (creates draft, redirects to step 1), `/estimation/<id>/wizard/<step>` renders the step partial; `hx-boost` keeps it feeling like one page. `?mode=revise` for the edit flow.
3. Step partials: `_step1_project.html` … `_step7_review.html`. Alpine for: matrix combination count, PR row add/remove, team member rows, dirty flag for autosave.
4. Autosave: `<form hx-put=".../draft" hx-trigger="every 30s[dirty], change delay:5s" hx-swap="none">` with `HX-Trigger: draft-saved` → Alpine updates the "✓ Draft autosaved" label.
5. Step 7: `hx-post=".../calculate-preview"` swaps `_review_results.html` (hours cards, tasks table, risk flags, feasibility badge). "Save" commits draft → `DRAFT`, or `/revise`.
6. Detail page: port tabs/sections; report buttons are plain links to `/estimation/<id>/report/<fmt>`; export/assign/compare/send-report as HTMX modals.
7. Jira PR import modal: `hx-get="/estimation/<id>/wizard/5/jira-import"`, rows with Alpine selection, "Import Selected" posts IDs.
8. Verify the README worked example and the three report formats byte-compare (except timestamps) with v3 output for the same input.

### Phase 6 — Requests, dashboard, integrations, notifications

1. Requests inbox: list with status filters, detail with attachments, assign, create estimation from request.
2. Dashboard: view assembles the same `dashboard/stats` dict; template emits `<script type="application/json" id="stats">`; Alpine `x-init` builds ECharts series; re-init on `htmx:afterSettle` and on theme change.
3. Integrations: 5 tabs via `hx-get` per tab; Test / Sync buttons return a result partial; status table.
4. Notifications list page and mark-read actions.

### Phase 7 — Data migration, deployment, cutover

1. Upgrade path for existing installations (see §10).
2. Dockerfile: single `python:3.12-slim` stage installing `v4/requirements.txt`; entrypoint runs `migrate`, `ensure_defaults`, `collectstatic`, then `uvicorn presto.asgi:application`.
3. `docker-compose.yml`: services `app` (port 8000), `scheduler` (`manage.py sync_redmine --loop`), optional `mysql`, `nginx`. Drop ports 8501/8502.
4. `nginx.conf`: `/` and `/api/` to `app:8000`; remove the WebSocket upgrade block.
5. Jenkinsfile: test stage runs `pytest` inside the built image; keep Harbor push stages; keep `APP_VERSION` build arg.
6. Deploy to staging, run the parity checklist, rehearse rollback to the v3 image with the backed-up DB.

### Phase 8 — Cleanup and release

1. Delete `frontend_nicegui/`, `frontend_web/`, `.streamlit/`, `frontend_desktop/`, `backend/src/cli/ipc_handler.py`, `_check_admin.py`; move `v4/` contents to repo root (`presto/`, `apps/`, `engine/`, `templates/`, `static/`).
2. Update `README.md`, `FEATURES.md`, `docs/design-document.md`, `docs/SRS.md` tech stack sections, regenerate screenshots.
3. Bump version to `4.0.0` in `pyproject.toml`, `healthcheck`, `host-config`. Write the CHANGELOG entry.
4. Tag `v4.0.0`.

---

## 8. Target Project Structure

```
presto/
├── manage.py
├── pyproject.toml                 # single Python project (django, ninja, reports, auth deps)
├── presto/                        # Django project
│   ├── settings.py  urls.py  asgi.py
├── engine/                        # pure estimation logic (moved from backend/src/engine)
│   ├── calculator.py  feasibility.py  calibration.py  allocator.py
├── apps/
│   ├── core/            models(Configuration), config.py, dashboard views, branding, management/commands/{seed,ensure_defaults,create_admin}
│   ├── accounts/        User, UserSession, AuditLog, permissions.py, backends/{ldap,oidc}.py, views (login, oidc, password), rbac views
│   ├── catalog/         Feature, TaskTemplate, TaskTemplateFeature, FeaturePreset, TaskPreset, DutType, TestProfile, DocumentType, RiskItem, PublicHoliday
│   ├── estimations/     Estimation, EstimationTask, EstimationRisk, EstimationTeamAllocation, EstimationVersionSnapshot, HistoricalProject; services.py; wizard/ views
│   ├── inbox/           Request, attachments, views
│   ├── teams/           Team, TeamMember, presets
│   ├── integrations/    IntegrationConfig, adapters/ (5), service.py, webhooks.py, management/commands/sync_redmine.py
│   ├── reports/         excel.py word.py pdf.py trend.py comparison.py executive.py, views (downloads)
│   ├── notifications/   WebhookNotification, smtp.py, templates.py, views (badge, list, mark-read)
│   ├── imports/         csv_importer.py, service.py, views (preview/execute)
│   └── api/             NinjaAPI, auth.py (JWT), schemas.py, routers/{auth,users,catalog,estimations,requests,teams,configuration,integrations,notifications,reports,dashboard,admin}.py
├── templates/
│   ├── base.html  errors/
│   ├── partials/  _sidebar.html _bell.html _toast.html _modal.html _form_field.html _table_controls.html
│   └── <app>/     page templates + partials/_*.html
├── static/
│   ├── vendor/    htmx.min.js alpine.min.js echarts.min.js material-symbols.woff2
│   ├── css/app.css
│   └── js/app.js  (Alpine stores: theme, selection; htmx event handlers)
├── tests/                         # pytest-django; engine tests need no DB
├── data/  seed_data.json
├── docs/
├── Dockerfile  docker-compose.yml  nginx.conf  Jenkinsfile
```

---

## 9. HTMX + Alpine Patterns per Screen Type

### 9.1 CRUD list page

```html
<div id="profiles-table"
     hx-get="{% url 'catalog:profiles_table' %}" hx-trigger="load, refresh from:body"
     hx-include="#profiles-filters"></div>

<button hx-get="{% url 'catalog:profile_form' %}" hx-target="#modal" hx-swap="innerHTML">Add</button>
<div id="modal"></div>
```
- Form partial posts back; on success the view returns `204` with `HX-Trigger: {"refresh": true, "toast": "Profile saved"}`.
- On validation error the view returns the form partial with `422` and `HX-Retarget: #modal`.

### 9.2 Bulk selection (DUTs, users)

```html
<table x-data="{ sel: [] }">
  <tr><td><input type="checkbox" :value="{{ dut.id }}" x-model="sel"></td>…</tr>
</table>
<button x-show="sel.length" hx-post="{% url 'catalog:duts_bulk_edit' %}"
        :hx-vals="JSON.stringify({ids: sel})">Bulk edit</button>
```

### 9.3 Wizard step

```html
<form id="step4" x-data="matrix()" hx-put="{% url 'estimations:draft' est.id %}"
      hx-trigger="change delay:2s, every 30s [dirty]" hx-swap="none"
      @htmx:after-request="dirty=false">
  … checkboxes for DUTs and profiles …
  <p>Combinations: <span x-text="duts.length * profiles.length"></span></p>
  <button hx-post="{% url 'estimations:wizard_step' est.id 5 %}" hx-target="#wizard">Next</button>
</form>
```

### 9.4 Live preview

```html
<button hx-post="{% url 'estimations:calculate_preview' est.id %}"
        hx-include="#wizard" hx-target="#review-results" hx-indicator="#calc-spinner">Calculate</button>
```

### 9.5 Dashboard charts

```html
<div x-data="chart('est-status')" x-init="init($refs.el, JSON.parse($refs.data.textContent))">
  <script type="application/json" x-ref="data">{{ stats.estimations_by_status|json_script_raw }}</script>
  <div x-ref="el" class="h-64"></div>
</div>
```
`chart()` is an Alpine component in `app.js` that calls `echarts.init` and listens to the theme store for palette swaps.

### 9.6 Toasts and errors

- Global `<div id="toasts" x-data @toast.window="push($event.detail)">`.
- `document.body.addEventListener('htmx:responseError', …)` reads JSON `detail` (ninja/Pydantic format) and renders the same banner text as the v3 error pages.

---

## 10. Data Migration Strategy

> **Status (D7):** v3 has not been rolled out, so no production database exists yet. v4 will most likely be the first production release and start from a fresh database (`migrate` + `seed`). Both paths below stay implemented and tested (Phase 1 already covers Path A against a real v3 schema) so that any staging or pilot v3 data can still be carried over, but rehearsal on a production copy is no longer a release gate.

Two supported paths; both tested in Phase 7.

### Path A — In-place adoption (default)

Works because v4 models reuse v3 table and column names.

```bash
# backup first
cp data/estimation.db data/estimation.v3.bak
python manage.py migrate --fake-initial       # adopt existing tables
python manage.py migrate                      # apply v4-only additions (django_session, etc.)
python manage.py ensure_defaults              # config keys
python manage.py check_v3_compat              # custom: asserts schema_version == 24, reports anomalies
```

Pre-condition: the database must be at v3 schema version 24. Older databases must run v3.4 once first.

### Path B — Export / import (fallback)

Use the existing `GET /api/admin/backup` JSON on v3, then `POST /api/admin/restore` (or a `restore_backup` command) on a fresh v4 database. This is the path for MySQL installations whose column types differ from what Django expects.

### Compatibility shims

- `password_hash` → Django `password` field via `db_column="password_hash"`, still stored as raw bcrypt (no format change).
- `configuration.schema_version` row is kept and set to `"django"` after adoption so a v3 binary refuses to start on a v4 database.
- `user_sessions.refresh_token` remains the refresh store for the API.

---

## 11. Testing Strategy

| Layer | Tool | Floor |
|-------|------|-------|
| Engine | pytest, no DB | 55 existing tests moved verbatim |
| Services + models | pytest-django, SQLite in-memory | port of `test_models.py`, `test_auth.py`, `test_integrations.py` |
| API coverage | pytest + OpenAPI diff | every v3 path × method present or listed as changed; Redmine webhook asserted |
| API behaviour | ninja `TestClient` | port of `test_api.py`, `test_phase6_api.py`, `test_reports.py` |
| HTML views | Django `Client` + `BeautifulSoup`/`django-htmx` test helpers | each page renders 200 for an authorised role and 403 for an unauthorised one |
| Browser | Playwright (already in the toolchain) | wizard happy path, login, theme toggle, one CRUD page, dashboard charts render |
| Visual | Playwright screenshots vs `docs/v3-baseline/screenshots/` | manual review, not pixel gate |

Total target: ≥ 299 tests before Phase 8, with the parity checklist as the manual gate.

---

## 12. Deployment and CI Changes

| File | Change |
|------|--------|
| `Dockerfile` | One Python stage; `pip install .`; entrypoint: `migrate` → `ensure_defaults` → `collectstatic --noinput` → `uvicorn presto.asgi:application --host 0.0.0.0 --port 8000 [--ssl-*]`. Expose 8000 only. Health check unchanged (`/api/healthcheck`). Corporate PyPI mirror args unchanged. |
| `docker-compose.yml` | `app`, `scheduler` (same image, `command: python manage.py sync_redmine --loop`), `mysql` profile, `nginx`. Remove 8501/8502 mappings. |
| `nginx.conf` | Single server block; `location /` → `app:8000`; drop WebSocket upgrade headers; add `client_max_body_size` for attachment uploads. |
| `Jenkinsfile` | Test stage: `docker run --rm --entrypoint sh presto:test -lc 'pytest -q'`; add `python manage.py check --deploy`. Everything else (packaging, staging copy, Harbor push, `APP_VERSION`) unchanged. |
| Env vars | Keep `DB_URL`, `CORS_ORIGINS`, `SSL_CERTFILE`, `SSL_KEYFILE`, `APP_VERSION`. Add `DJANGO_SECRET_KEY` (required in prod), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`. Drop `API_URL`, `NICEGUI_PORT`. |

---

## 13. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Wizard UX regresses without WebSocket push | Users notice lag or lost input | Draft-row-as-state design (§7 Phase 5); autosave on `change`; Playwright happy-path test |
| Hidden behaviour in 4,784-line `routes.py` missed during split | Parity gap | Contract test + move logic to services with the existing API tests as a net; parity checklist per endpoint |
| Existing MySQL schemas differ in column types from Django defaults | `migrate --fake-initial` adopts but queries fail | Rehearse on a MySQL dump in Phase 1; Path B fallback |
| bcrypt hash compatibility | Users cannot log in after upgrade | Legacy hasher with unit tests against hashes exported from v3 |
| Desktop users not yet on the web app | Users lose their tool at cutover | Announce retirement at Phase 7 staging; share the web URL and login steps |
| An admin task only reachable via DB or Django admin | Admins blocked without Django admin | Audit every v3 admin action into §5.4; add in-app page or management command for each |
| ECharts without NiceGUI wrapper | Charts blank in dark mode or after HTMX swap | Re-init on `htmx:afterSettle` and theme change; Playwright asserts canvas present |
| Scope creep ("while we're here…") | Timeline slips | Non-goals in §1; new ideas go to a `v4.1-backlog.md` |
| Team unfamiliar with HTMX idioms | Inconsistent pages | Build the CRUD pattern once (Phase 4 step 1), document it, review the first three pages closely |

---

## 14. Definition of Done

- [ ] Every row in §5 is checked by the owner on staging.
- [ ] Coverage test: every v3 API path present or listed as changed; Redmine webhook works.
- [ ] `pytest` ≥ 299 passing; Playwright suite green.
- [ ] Worked example (README) reproduces 511.5 h, 73.1 person-days, 14.6 weeks, AT_RISK.
- [ ] Data carry-over tested: Path A on a v3 schema 24 database and Path B with a v3 backup file (no production v3 database exists, decision D7).
- [ ] Docker image runs with only port 8000; nginx serves UI and API on the same origin.
- [ ] README, FEATURES, CHANGELOG, design document, SRS tech-stack section, screenshots updated.
- [ ] `frontend_nicegui/`, `frontend_web/`, `frontend_desktop/`, IPC handler removed from `main`; preserved on `v3-maintenance`.
- [ ] No `django.contrib.admin`; every admin task reachable from in-app pages or a documented management command.
- [ ] Theme follows the user's saved preference across browsers; browser fallback works before login.

---

## 15. Timeline Estimate

Assumes one developer full-time plus one reviewer. Numbers are working weeks.

| Phase | Weeks | Notes |
|-------|-------|-------|
| 0 Prep | 0.5 | |
| 1 Skeleton + models | 1.5 | Model port is mechanical; migration rehearsal takes the time |
| 2 API parity | 3 | 140 endpoints, service extraction, test port; runs in parallel, off the critical path |
| 3 UI shell + auth | 1.5 | LDAP/OIDC backends included |
| 4 Catalog + admin pages | 3 | 19 pages on one pattern |
| 5 Wizard + detail + edit | 3 | Largest single UI risk |
| 6 Requests, dashboard, integrations | 2 | |
| 7 Migration + deployment | 1.5 | Staging soak included |
| 8 Cleanup + release | 0.5 | |
| **Total** | **16.5** | ≈ 4 months; add 20 % contingency → 5 months |

Parallelisation: Phases 3–4 can start once Phase 1 models exist, overlapping Phase 2. With two developers the critical path drops to roughly 11 weeks.

---

## 16. Decisions

### 16.1 Resolved

| # | Question | Decision | Effect on plan |
|---|----------|----------|----------------|
| D1 | Is the desktop app still needed? | **No.** Retire it; the Docker-hosted web service replaces it | `frontend_desktop/` and IPC handler removed (§4.7); API kept for integrations only (G5) |
| D2 | Which Django version? | **5.2 LTS** | Pinned `django>=5.2,<5.3` (§7 Phase 1) |
| D4 | Ship Django admin? | **No.** All admin features live in the app | No `django.contrib.admin` (§4.9); admin parity in §5.4 |
| D6 | Where is theme preference stored? | **Per user in the DB, browser as fallback** | `users.theme_preference` column (§4.8) |
| D7 | Is there a production v3 database to migrate? | **No.** v3 is not rolled out yet | Production-copy rehearsal dropped as a gate; Path A/B kept for staging or pilot data (§10) |

### 16.2 Still open

| # | Question | Needed by | Default if unanswered |
|---|----------|-----------|------------------------|
| D3 | Keep direct TLS in uvicorn, or terminate only at nginx? | Phase 7 | Keep both, as v3 does |
| D5 | Streamlit and desktop retirement: who needs notice? | Phase 7 | Announce at Phase 7 staging |

---

## 17. Progress Log

### Phase 0 — done (2026-11-09)

- `v3-maintenance` branch pushed; `v3.4.0` tag created locally (push blocked by the session's git proxy, owner to push).
- Baseline in `docs/v3-baseline/`: OpenAPI snapshot (144 operations), 26 screenshots, 299 passing tests, 51 % coverage.
- Production database copy: not applicable, v3 has no production deployment (D7).

### Phase 1 — done (2026-11-09)

Delivered in `v4/` (see `v4/README.md`):

- Django 5.2 LTS project, 8 apps, 25 models mapped 1:1 onto the v3 tables and columns.
- Initial migrations adopt a v3 database with `migrate --fake-initial`; the v4-only `users.theme_preference` column is a separate, real migration.
- Commands: `seed`, `ensure_defaults`, `create_admin`, `check_v3_compat`.
- Typed configuration accessor (`apps.core.config`) and the 61 v3 default keys in `apps/core/config_defaults.json`.
- Engine moved verbatim; its 55 tests pass unchanged.
- 81 tests in total, including tests that build a real v3 database with the v3 code and then:
  - adopt it and read every sample row back through Django;
  - restart v3 on the adopted database and verify a password changed by v4;
  - compare a fresh v4 schema with a fresh v3 schema (tables, columns, NOT NULL rules);
  - compare a fresh v4 seed with a fresh v3 install, row by row.
- Adoption rehearsed on the Phase 0 sample database (estimation created through the v3 API).

Deviations from the original plan:

| Plan said | Done instead | Why |
|-----------|--------------|-----|
| 24 tables | 25 tables | Actual count in a v3 schema 24 database |
| `apps/requests` | `apps/inbox` | Avoids confusion with the `requests` HTTP library |
| Legacy bcrypt hasher with upgrade on login | Raw bcrypt kept | v3 and v4 share hashes; rollback stays safe |
| `HistoricalProject` in `catalog` | In `estimations` | Avoids a circular dependency between app migrations |
| v3 index names everywhere | 3 FK indexes use Django names | Django caps explicit index names at 30 characters |
| `sync_redmine` in Phase 1 | Phase 2 | Needs the ported Redmine adapter |

Not yet verified: adoption of a MySQL database (no MySQL instance in this environment). A production copy does not exist (D7).

### Phase 2 — done (2026-11-09)

REST API on django-ninja in `v4/apps/api/`, mounted at `/api/`. Business logic lives in service modules with no ninja imports, ready for the HTML views.

| Domain | Endpoints | Notes |
|--------|----------:|-------|
| Auth, users, LDAP/OIDC, audit log | 17 | Local then LDAP login; OIDC provider ported for the web UI |
| Catalog, teams, configuration, holidays | 65 | Includes logo upload, ICS import, working weeks |
| Estimation lifecycle | 17 | Create, recalculate, draft, revise with snapshots, status workflow, versions and diff |
| Reports | 7 | Generators copied unchanged from v3 |
| Inbox, integrations, notifications, dashboard, import, backup | 33 | Adapters copied unchanged; `sync_redmine` command replaces the in-process scheduler |

Verification:

- 729 tests pass; ruff is clean; DeprecationWarnings fail the suite.
- Every v3 operation exists in v4 (`test_openapi_coverage.py`).
- Every v3 operation requires the same role in v4 (`test_access_parity.py`, 133 operations).
- A side-by-side test runs the v3 app and v4 on the same estimation and compares create, detail, preview, recalculate, revise, versions and diff field by field: identical.
- A backup produced by the real v3 `/api/admin/backup` restores in v4 (data carry-over Path B).
- The v4 seed equals a fresh v3 install for every column of every table.

Intentional deviations from v3 (all turn crashes into clean errors or close holes; each is covered by a test):

- Duplicate names and unknown foreign keys return 400/404/409 instead of 500.
- Attachment uploads strip directory parts from filenames (v3 allowed path traversal).
- The LDAP login filter escapes the username (v3 allowed filter injection).
- `PUT /integrations/{name}` rejects `additional_config_json` that is not a JSON object.
- Empty-string dates in estimation payloads are treated as empty instead of 422.

Known follow-ups:

- Uploaded logos (SVG allowed, ADMIN only) and attachments are served by Django; move to nginx or WhiteNoise in Phase 7.
- `GET /estimations` builds the full payload per row, as v3 did; optimise if lists grow large.

### Phase 3 — done (2026-11-10)

Web UI shell in `v4/` with Django templates, htmx 2.0.4 and Alpine.js 3.14.9 (vendored, no CDN). Screenshots: `docs/v4-screenshots/phase3/`.

| Area | Delivered |
|------|-----------|
| Layout | Sidebar ported from v3 (logo, user card, collapsible sections, footer), drawer toggle, phone layout with slide-in menu |
| Sign-in | Local and LDAP (Internal/LDAP switch when LDAP is configured), single sign-on through OIDC, logout, password change |
| Access | Sign-in gate for all pages (JSON API untouched), v3 menu rules from the 18-permission matrix, admin-only pages return 403 |
| Theme | Light/dark with the D6 order (user setting, browser, OS, dark); Settings colour keys become CSS variables |
| Notifications | Bell badge polled every 30 s, panel with mark read and mark all read |
| Errors | 400/403/404/500 pages in the v3 wording, CSRF failure page, htmx error toasts, session expiry redirect |
| Pages | Every v3 URL answers now; pages not yet rebuilt show a placeholder with the same access rule |

Verification: 777 tests pass (48 new for the shell); browser run in both themes, at desktop and phone width, with no console errors other than intended 401/403/404 responses. The browser run caught a bug that the tests had missed (mark-as-read answered 405), now fixed with a status assertion added.

Changes from the plan:

- `verify_credentials()` was split out of the API login so the web sign-in reuses the same local/LDAP flow and audit entries without issuing API tokens.
- Material Symbols (variable font, 3.3 MB) is vendored whole so later pages can use any icon.
- Text on primary-coloured buttons is computed from the configured colour (v3 always used white, unreadable on the default dark-mode light blue).

### Phase 4 — done (2026-11-10)

All 17 catalog and administration pages are real pages; only the estimation (Phase 5) and requests, dashboard and integrations (Phase 6) pages remain placeholders. Screenshots in both themes: `docs/v4-screenshots/phase4/`.

| Group | Pages | Highlights |
|-------|-------|-----------|
| Catalog | Feature Catalog, Feature Presets, Task Templates, Test Profiles | Product type and scope filters, project-feature promotion, template links with per-feature hour overrides, category manager |
| Registries | DUT Registry, DUT Assets, Document Types, Risk Registry, PR Registry | DUT bulk edit and delete, Snipe-IT assets with import as DUT, Jira PR list with detail dialog |
| Planning | Historical Projects, Team Members, Public Holidays | Accuracy ratio, skills chips, teams with membership, month calendar with ICS import |
| People admin | Users, RBAC, Audit Log | Search, bulk role, self-lockout protection, LDAP actions, 18x4 permission matrix, audit filters and JSON details |
| System admin | Settings, Backup & Restore | Typed inputs per key, colour pickers, secrets never echoed, SMTP/LDAP tests, guarded reinit actions, two-step restore preview |

Built on one shared list/edit framework (`apps/web/crud.py`) with server-side search, filters, sorting and paging, and htmx dialogs. Writes go through the domain services, so validation messages match the API, and every write action requires the same role as its API endpoint.

Verification:

- 1,138 tests pass (344 new for the pages); ruff is clean.
- Browser sweep through the sidebar as admin and as viewer over all 17 pages: no script errors, page styles present, no horizontal scroll at desktop or phone width; the viewer sees only the pages its role allows.
- Browser runs found and fixed: the modal host breaking the grid, inherited htmx attributes hijacking dialog buttons, toasts lost when a dialog closed, and page stylesheets missing after sidebar navigation.

Intentional differences from v3 (all covered by tests):

- Write buttons are hidden from roles the API would reject; v3 showed them and failed on save.
- Historical projects and document types can be edited or deleted (the API always allowed it); deletes of teams and holidays ask for confirmation.
- Users: you cannot change your own role or deactivate yourself. Audit Log gains user and date filters with real paging, and stays admin-only as in the v3 menu.
- Settings saves all changes at once or none, and shows secrets only as set / not set.
- Feature presets show as a table; Manage Categories is admin-only (its save API always was).
- LDAP test, sync and reset are available on both Users and Settings.

Follow-ups: shared-framework improvements listed in `v4/docs/UI_CONVENTIONS.md`; per-process cache for DUT assets (move to a shared cache in Phase 7); Snipe-IT adapter hides most HTTP errors (inherited from v3).

### Phase 5 — done (2026-11-10)

Estimations are fully usable in the web UI. Screenshots: `docs/v4-screenshots/phase5/`.

| Page | Delivered |
|------|-----------|
| Estimations list | v3 columns, status and feasibility badges, status tiles and filters, search, sort, paging |
| Wizard (`/estimation/new`, `/estimation/<id>/edit`) | All 7 v3 steps with presets, custom project features, DUT x Profile matrix, PR details with Jira import, documents, team allocation, risks, live preview; autosave for new estimations and drafts; revision flow for REVISED estimations |
| Estimation detail | Header card, tabs (Overview, Tasks, Documents, PR Fixes, Team, Risks, Versions, Workflow), status workflow, Excel/Word/PDF/executive summary downloads, export to external system, export tasks, assign, send by email, compare, calibrate, archive, version diff |

Design as planned: the wizard state is one API-shaped payload in the session; autosave creates exactly one DRAFT and then edits it in place; a revision stays in the session until "Save Revision", which snapshots the previous version.

Verification:

- 1,247 tests pass (109 new); ruff is clean.
- A saved estimation reopened in the wizard and saved unchanged gives identical totals, tasks and child rows; the wizard preview equals `POST /api/estimations/calculate`.
- End-to-end browser run: wizard to detail, DRAFT to FINAL to REVISED, revision back through the wizard (version 2, one snapshot), version list and Excel download; no console errors.

Intentional differences from v3:

- Moving forward in the wizard (including via step headers) validates the current step; Save re-checks all steps.
- Drafts autosave when resumed (v3 only autosaved new estimations).
- The detail page adds Risks and Versions tabs, calibration, send by email, compare and the executive summary (all existed in the API).
- PR fix hours on the detail page come from Settings instead of fixed 2/4/8; approving records the approver's name; the assign list works for approvers.

Known limitation kept from v3: the wizard preview leaves out PR test-creation hours, which are added when saving (the preview labels that task "added on save").
