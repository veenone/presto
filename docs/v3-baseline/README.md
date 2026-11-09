# v3 Baseline (Phase 0)

Frozen reference for the v4 migration. Everything in v4 is measured against this folder.

| Item | Value |
|------|-------|
| Source commit | `ef9872e` (`main`) |
| Maintenance branch | `v3-maintenance` (pushed) |
| Release tag | `v3.4.0` (annotated, created locally; push pending, see Open items) |
| Captured on | 2026-11-09 |
| Python | 3.13 |
| Key packages | fastapi 0.162.2, SQLAlchemy 2.1.8, pydantic 2.14.3, nicegui 4.0.0, reportlab 5.1.0, openpyxl 3.1.5, python-docx 1.2.0 |

## Contents

| File | Purpose |
|------|---------|
| `openapi-v3.json` | Full OpenAPI document of the v3 API. Input for the v4 coverage test |
| `api-inventory.md` | Human-readable list of all 144 operations, grouped |
| `screenshots/` | 26 captures of every NiceGUI page, dark theme, 1440 px wide at 2x |

## Test baseline

All tests pass: **299 passed** in 94 s.

| Module | Tests |
|--------|------:|
| `tests/test_allocator.py` | 12 |
| `tests/test_api.py` | 37 |
| `tests/test_auth.py` | 104 |
| `tests/test_calculator.py` | 16 |
| `tests/test_calibration.py` | 14 |
| `tests/test_feasibility.py` | 13 |
| `tests/test_integrations.py` | 64 |
| `tests/test_models.py` | 10 |
| `tests/test_phase6_api.py` | 22 |
| `tests/test_reports.py` | 7 |
| **Total** | **299** |

## Coverage baseline

Total statement coverage: **51 %** (3,957 of 7,699 statements).

| Area | Coverage | Note for v4 |
|------|---------:|-------------|
| `engine/` (calculator, feasibility, calibration, allocator) | 96–100 % | Moves verbatim; keep at this level |
| `database/models.py`, `auth/models.py`, `auth/service.py` | 100 % | |
| `api/schemas.py` | 96 % | |
| `reports/` excel, word, pdf | 80–82 % | |
| `database/migrations.py` | 79 % | Replaced by Django migrations |
| `api/routes.py` | 42 % | Raise when logic moves into services |
| `integrations/` | 8–58 % | Outline 8 %, Snipe-IT 23 %, service 26 % |
| `notifications/service.py` | 36 % | |
| `auth/ldap_provider.py`, `auth/oidc_provider.py` | 0 % | Add tests when ported to Django auth backends |
| `imports/` | 0 % | Add tests when ported |
| `reports/` comparison, trend, executive summary, templates | 0 % | Add tests when ported |
| `integrations/sync_scheduler.py` | 0 % | Replaced by `sync_redmine` command |
| `cli/ipc_handler.py` | 0 % | Removed in v4 |

v4 floor: at least 299 tests and at least 51 % coverage, with no area dropping below its v3 value.

## Screenshot index

Captured with sample data: 1 request-linked estimation (SIM Toolkit v2.5, 515.9 h, FEASIBLE), 2 requests, 4 team members, 1 historical project, 1 risk, 1 holiday, 1 document type, on top of the standard seed data.

| # | Page | Route | File |
|---|------|-------|------|
| 00 | Login | `/login` | `00_login.png` |
| 01 | Dashboard | `/` | `01_dashboard.png` |
| 02 | Request inbox | `/requests` | `02_requests.png` |
| 03 | Request detail | `/requests/1` | `03_request_detail.png` |
| 04 | Estimations list | `/estimations` | `04_estimations.png` |
| 05 | New estimation wizard | `/estimation/new` | `05_estimation_new.png` |
| 06 | Estimation detail | `/estimation/1` | `06_estimation_detail.png` |
| 07 | Estimation edit | `/estimation/1/edit` | `07_estimation_edit.png` |
| 08 | Feature catalog | `/features` | `08_features.png` |
| 09 | Feature presets | `/feature-presets` | `09_feature_presets.png` |
| 10 | Task templates | `/tasks` | `10_tasks.png` |
| 11 | DUT registry | `/duts` | `11_duts.png` |
| 12 | DUT assets | `/assets` | `12_assets.png` |
| 13 | Test profiles | `/profiles` | `13_profiles.png` |
| 14 | Historical projects | `/history` | `14_history.png` |
| 15 | Team members | `/team` | `15_team.png` |
| 16 | Risk registry | `/risks` | `16_risks.png` |
| 17 | PR registry | `/pr-registry` | `17_pr_registry.png` |
| 18 | Document types | `/document-types` | `18_document_types.png` |
| 19 | Public holidays | `/public-holidays` | `19_public_holidays.png` |
| 20 | Settings | `/settings` | `20_settings.png` |
| 21 | Integrations | `/integrations` | `21_integrations.png` |
| 22 | Users | `/users` | `22_users.png` |
| 23 | RBAC matrix | `/rbac` | `23_rbac.png` |
| 24 | Audit log | `/audit` | `24_audit.png` |
| 25 | Backup and restore | `/admin/backup` | `25_backup.png` |

### Capture notes

- The sidebar scrolls independently, so full-page captures show it only down to "PR Registry". The full navigation list is in `docs/v4-migration-plan.md` §2.2.
- Captures used nicegui 4.0.0. The production image installs nicegui unpinned (`>=2.0.0`), so the deployed version may differ. In this capture the wizard renders both its horizontal step header and the vertical stepper; compare with production before treating that as intended behaviour.

## How to reproduce

```bash
# backend + frontend against a throwaway DB
cd backend && DB_PATH=/tmp/v3.db uvicorn src.api.app:app --port 8000 &
cd .. && API_URL=http://127.0.0.1:8000/api python frontend_nicegui/app.py &

# OpenAPI snapshot
cd backend && DB_PATH=/tmp/v3.db python -c "import json; from src.api.app import app; \
  json.dump(app.openapi(), open('../docs/v3-baseline/openapi-v3.json','w'), indent=2, sort_keys=True)"

# tests + coverage
cd backend && python -m pytest -q --cov=src --cov-report=term
```

## Open items

| Item | Owner | Status |
|------|-------|--------|
| Push tag `v3.4.0` to GitHub | Repo owner | The session's git proxy rejects tag pushes. Run `git push origin v3.4.0` from a local clone, or create the tag on `ef9872e` in the GitHub UI |
| Sanitised copy of a production database for upgrade rehearsal | Repo owner | Pending. Keep it out of git; share it with the session when Phase 1 reaches the migration rehearsal step |
