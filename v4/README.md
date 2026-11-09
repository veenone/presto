# PRESTO v4 (Django)

Work-in-progress Django port of PRESTO. See `docs/v4-migration-plan.md` for the plan and progress log. The v3 code (`backend/`, `frontend_nicegui/`) keeps running until the cutover.

## Layout

| Path | Contents |
|------|----------|
| `presto/` | Django project: settings, URLs, ASGI/WSGI, database config |
| `apps/core` | `Configuration` model, typed config access, first-run bootstrap, admin commands |
| `apps/accounts` | `User` (v3-compatible bcrypt passwords), `UserSession`, `AuditLog` |
| `apps/teams` | `Team`, `TeamMember` |
| `apps/catalog` | Features, task templates, presets, DUTs, profiles, documents, risks, holidays |
| `apps/inbox` | Request inbox |
| `apps/estimations` | Estimations, tasks, allocations, risks, version snapshots, historical projects |
| `apps/integrations` | `IntegrationConfig` |
| `apps/notifications` | In-app notifications and SMTP notification service |
| `apps/api` | REST API (`/api/*`, django-ninja): routers, schemas, JWT role auth. Docs at `/api/docs` |
| `apps/reports` | Excel, Word, PDF, executive summary, comparison and trend reports (generators unchanged from v3) |
| `apps/imports` | CSV and Excel bulk import |
| `engine/` | Pure estimation logic, moved unchanged from v3 |
| `tests/` | pytest-django suite (`tests/engine` needs no database) |

## Quick start (local)

```bash
cd v4
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DJANGO_DEBUG=1                 # dev only; production needs DJANGO_SECRET_KEY
python manage.py migrate
python manage.py seed                 # catalog, default config keys, admin/admin
python manage.py runserver            # http://127.0.0.1:8000/api/healthcheck
```

## Upgrading an existing v3 database

The v4 models reuse the v3 table and column names, so the database is adopted in place. Take a backup first.

```bash
export DB_PATH=/path/to/estimation.db          # or DB_URL=...
python manage.py check_v3_compat --before-migrate   # needs v3 schema 24
python manage.py migrate --fake-initial
python manage.py check_v3_compat
python manage.py ensure_defaults
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | none (required unless `DJANGO_DEBUG=1`) | Session and CSRF signing |
| `DJANGO_DEBUG` | `0` | Development mode |
| `DB_URL` | none | Database URL; v3 style `mysql+pymysql://...` accepted |
| `DB_PATH` | `v4/data/estimation.db` | SQLite file when `DB_URL` is unset |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names |
| `CSRF_TRUSTED_ORIGINS` | none | Comma-separated origins behind a proxy |
| `APP_VERSION` | `4.0.0.dev0` | Version shown by the healthcheck and sidebar |
| `SEED_DATA_PATH` | `../data/seed_data.json` | Seed catalog file |

## Commands

| Command | What it does |
|---------|--------------|
| `seed` | Loads the seed catalog if empty, then runs `ensure_defaults` |
| `ensure_defaults` | Adds missing config keys; creates admin/admin when there are no users |
| `create_admin [--username U] [--password P]` | Creates an admin or resets its password, role and active flag |
| `check_v3_compat [--before-migrate]` | Compares database tables and columns with the models |
| `sync_redmine --once` / `--loop` | Imports Redmine issues into the request inbox; `--loop` repeats every `poll_interval_minutes` (replaces the v3 in-process scheduler) |

## API

The REST API lives at `/api/` with the same paths, payloads and roles as v3. See `docs/API_CONVENTIONS.md`. Obtain a token with `POST /api/auth/login` and send `Authorization: Bearer <token>`.

## Tests

```bash
cd v4 && python -m pytest -q
```

Tests marked `v3compat` build a real v3 database with the code in `../backend` and are skipped when it or SQLAlchemy is unavailable.
