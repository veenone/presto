# API Conventions (Phase 2 port)

How the v3 FastAPI endpoints are ported to Django + django-ninja. Applies to every router in `apps/api/routers/`.

## Goal

Same paths, methods, request bodies, response shapes, status codes and error messages as v3 (`backend/src/api/routes.py`). The v3 OpenAPI snapshot is `docs/v3-baseline/openapi-v3.json`; `tests/api/test_openapi_coverage.py` fails until every v3 operation exists.

## Layout

| Layer | Location | Rule |
|-------|----------|------|
| Router | `apps/api/routers/<domain>.py` | Thin: parse input, check access, call a service, shape the response |
| Schemas | `apps/api/schemas/<domain>.py` | Port the matching classes from v3 `api/schemas.py` / `auth/schemas.py` |
| Services | `apps/<app>/services.py` (or a named module) | All business logic and ORM work; no ninja imports; reusable by the HTML views later |
| Tests | `tests/api/test_<domain>.py`, `tests/<app>/...` | Port the v3 tests for the domain, then add tests for untested v3 behaviour you touch |

## Routing rules

- Every router is mounted with an empty prefix and declares full paths (`/estimations/{estimation_id}/versions`). The API is mounted at `/api/`.
- Django matches the URL path before the method. Never declare the same path in two routers, and never declare a literal path that a parameterised path of another method also matches (for example `GET /configuration/product_types` next to `PUT /configuration/{key}`): put all methods for one path in one router, or route both through one parameterised path and dispatch inside.
- Use typed path params (`estimation_id: int`) so `/requests/reinit` and `/requests/{request_id}` do not collide.
- Keep v3 function names where possible; ninja derives operation ids from module + function name.

## Auth

```python
from apps.api.auth import authenticated, role, optional_user

router = Router(auth=authenticated, tags=["catalog"])   # any logged-in user

@router.post("/auth/login", auth=None)                    # public
@router.get("/users", auth=role("ADMIN"))                 # v3 RequireRole("ADMIN")
```

- v3 `Depends(get_current_user)` = router default `authenticated`. v3 `RequireRole("X")` = `auth=role("X")`. v3 `get_optional_user` = `optional_user(request)`.
- The user is `request.auth` (also `request.user`).
- Role hierarchy: `apps.accounts.roles.has_role(user.role, "APPROVER")`. The 18-key RBAC matrix is UI-only in v3; do not enforce it in the API.

## Errors

- `HTTPException(404, "Feature not found")` becomes `raise HttpError(404, "Feature not found")` (`from ninja.errors import HttpError`). Body: `{"detail": "..."}`, same as v3. Keep v3 messages verbatim.
- `apps.api.common.get_or_404(Model, pk, "Feature not found")` for lookups.
- Service code never imports ninja. It raises `apps.core.errors.ServiceError(status_code, detail)`; the API turns it into the same `{"detail": ...}` response (registered in `apps/api/api.py`).
- Validation errors are 422 `{"detail": [...]}` automatically.

## Status codes and responses

- `status_code=201` becomes `response={201: FeatureOut}`; return `Status(201, obj)` (`from ninja import Status`).
- `status_code=204` becomes `response={204: None}`; return `Status(204, None)`. Plain `(status, body)` tuples are deprecated in ninja and fail the test suite.
- File downloads: return a Django `HttpResponse` with `Content-Disposition`, as v3 did.
- Uploads: `file: UploadedFile = File(...)` (`from ninja import File, UploadedFile`).

## Schemas and JSON columns

- Response schemas subclass `ninja.Schema` (so related managers resolve to lists). Request schemas may stay plain pydantic `BaseModel`.
- v3 exposes JSON columns as JSON text in fields named `*_json` (`skills_json`, `wizard_inputs_json`, `details_json`...). Django models expose them as Python values (`skills`, `wizard_inputs`, `details`). Keep the v3 field names and types in schemas:
  - responses: `skills_json: JsonText = Field("[]", validation_alias=AliasChoices("skills_json", "skills"))`, or build the response dict in a serializer function;
  - requests: `apps.api.common.parse_json_text(data.skills_json, [])` before saving.
- `apps.api.common.OptionalDate` is the v3 `OptionalDate` ('' becomes None).
- Renamed model fields vs v3 columns: `User.last_login` (v3 `last_login_at`), `Estimation.creator` / `approver` (v3 relationship names `creator`/`approver`, columns `created_by_id` / `approved_by_id`), `Feature.owner_estimation`, `TaskTemplateFeature` (no ManyToMany field: use `template.feature_links` / `feature.template_links`). v3 validators that walk SQLAlchemy relationships must be rewritten as explicit serializer code.
- Prefer one serializer function per resource (`def feature_out(f) -> dict`) when the v3 schema computes derived fields.

## Shared helpers (already ported)

| Need | Use |
|------|-----|
| Config values | `apps.core.config.get_str/get_float/get_int/get_bool/get_list/get_json/set_value` |
| Document numbers | `apps.core.numbering.generate_number("estimation_number_prefix", Estimation, "EST")` |
| Audit trail | `apps.accounts.audit.log_action(request.auth, "CREATE", "feature", obj.id, details, client_ip(request))` |
| Client IP | `apps.api.common.client_ip(request)` (v3 `request.state.client_ip`) |
| Tokens | `apps.accounts.tokens` (create/decode access, issue/rotate/revoke refresh) |
| PR config, doc overlap, synthetic tasks, completion date | `apps.estimations.calc` |

## Cross-domain service contracts

Services keep the v3 function names and arguments, minus the SQLAlchemy `db`/`session` argument. Underscore-prefixed v3 helpers that other domains call become public.

| Module | Functions |
|--------|-----------|
| `apps.integrations.service` | `get_adapter(system_name)`, `test_integration(system_name)`, `sync_import(system_name)`, `sync_export(system_name, estimation_data)`, `sync_export_all(system_name)`, `sync_export_task_breakdown(system_name, estimation_data)`, `estimation_to_export_dict(estimation)`, `get_integration_status()` |
| `apps.integrations.adapters` | v3 adapter classes unchanged (`EmailAdapter`, `JiraAdapter`, `OutlineAdapter`, `RedmineAdapter`, `SnipeItAdapter`, `base`) |
| `apps.notifications.service` | `NotificationService()` with the v3 methods (`notify_estimation_status_changed`, `notify_feature_promotion_requested`, `notify_user_assigned`, `notify_request_imported`, `notify_deadline_approaching`, `test_connection`, `is_configured`) |
| `apps.estimations.services` | estimation create/update/revise/status logic |
| `apps.reports` | v3 report generators unchanged; `apps.reports.builders.build_report_data(estimation)` |

Call another domain's service lazily (import inside the function) when the v3 code did, and keep v3's error handling around side effects (for example notifications that must not break a status change).

## Tests

- Fixtures in `tests/conftest.py`: `api` (admin client), `anon_api`, `api_as(user)`, `make_user(role)`, `seeded` / `admin_user`, `auth_headers`.
- `ApiClient` mirrors the FastAPI `TestClient`: `api.post("/api/x", json={...})`, `params=`, `files={"file": ("a.txt", b"...", "text/plain")}`; responses have `.status_code` and `.json()`.
- External HTTP (Redmine, Jira, Outline, Snipe-IT, SMTP) must be mocked, as in v3.
- Run: `cd v4 && python -m pytest -q tests/...`. DeprecationWarnings raised by our code fail the suite.
- Guard tests: `tests/api/test_openapi_coverage.py` (every v3 operation exists) and `tests/api/test_access_parity.py` (every v3 operation requires the same role).
