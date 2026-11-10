# UI Conventions (htmx + Alpine.js)

How HTML pages are built in v4. Read with `API_CONVENTIONS.md`: pages call the same service modules as the API, never the API itself.

## Building blocks

| Piece | Where | Use |
|-------|-------|-----|
| Page layout | `templates/base.html` | `{% extends "base.html" %}` and fill `{% block content %}`; set `page_title` in the view context |
| Bare layout | `templates/base_bare.html` | Pages without the sidebar (login, standalone errors) |
| Icons | `{% load presto %}{% icon "devices" %}` | Material Symbols names, filled style, vendored font |
| Theme colours | CSS variables `--primary`, `--on-primary`, `--sidebar-bg`, `--content-bg`, `--table-header-bg`, `--calendar-*` | Set from the Settings colour keys per theme; never hard-code these colours |
| Design tokens | `static/css/app.css` | `ed-card`, `ed-strip`, `ed-eyebrow`, `ed-cap`, `ed-empty`, `ed-status-now`, `btn`, `btn-primary`, `btn-flat`, `btn-outline`, `btn-round`, `field`, `input`, `alert`, `badge` |
| Vendored JS | `static/vendor/` | htmx 2.0.4, Alpine 3.14.9, ECharts 5.6.0; no CDNs (hosts may be offline) |

## Access

- Every page needs a signed-in user (`PageLoginRequiredMiddleware`); public views use `@login_not_required`.
- Page and menu visibility follow the v3 UI permission matrix: `apps.accounts.permissions` (`has_permission(user, "manage_duts")`, `ui_perms|has_perm:"manage_duts"` in templates). Admin-only pages check `user.role == "ADMIN"`.
- Business-rule checks stay in services and the API role hierarchy; the matrix only decides what the UI shows.
- To replace a placeholder page: add the real view and URL before the placeholder loop in `presto/urls.py` (keep the same path and URL name) and set its `phase` to `0` in `apps/web/navigation.py`.

## htmx patterns

- CSRF: `<body hx-headers>` sends the token on every htmx request; plain forms use `{% csrf_token %}`.
- Navigation links in the sidebar are boosted (`hx-boost`); full page loads also work.
- Partials live in `templates/<app>/partials/_name.html` (shared ones in `templates/partials/`). One view per partial.
- Side effects on other parts of the page: return `HX-Trigger` events with `apps.web.htmx.trigger(response, **events)`; toasts with `apps.web.htmx.toast(response, "Saved")`. Listen with `hx-trigger="my-event from:body"`.
- Errors: a failed htmx request shows a toast with the server's `detail` text (same as the v3 error banner). Return 4xx with a JSON `{"detail": ...}` or plain text.
- Session expiry during an htmx request returns `HX-Redirect` to the login page.
- Do not put a GET-only view inside a POST view's response path (render a shared helper instead); every view states its methods with `require_GET` / `require_POST`.

## List + edit pages (`apps/web/crud.py`)

The v3 data-management pattern (toolbar with Add and TOTAL, stat tiles, segmented filter, search, sortable paged table, add/edit dialog, delete confirmation) is one class. Reference implementation: `apps/catalog/pages/profiles.py` and `tests/web/test_profiles_page.py`.

```python
class ProfilesPage(CrudPage):
    url_name = "profiles"                 # navigation item name
    title, noun = "Test Profiles", "Profile"
    write_role = "APPROVER"               # same minimum role as the API write endpoints
    form_class = ProfileForm              # a StyledForm
    columns = [Column("name", "Name", sortable=True, kind="strong"), ...]
    search_fields = ["name", "description"]
    def get_rows(self, request) -> list[dict]: ...          # rows as dicts (keys = column keys + "id")
    def get_segments(self, rows) -> list[Segment]: ...       # segmented filter (+ "all")
    def get_tiles(self, rows) -> list[Tile]: ...             # KPI strip; Tile(segment=...) makes it a filter
    def get_initial(self, request, obj_id) -> dict: ...      # raise Http404 for unknown ids
    def create/update/delete(self, request, ...): ...         # call the domain service; ServiceError -> shown in the dialog
```

- Register with `urlpatterns = [*ProfilesPage.urls("profiles")]` in the page group's module under `apps/web/urls/`. Unregistered nav items keep their placeholder automatically.
- Column kinds: `text`, `strong`, `mono`, `truncate` (60 chars + tooltip), `bool` (check/cross icon), `badge`; `fmt` formats numbers (`"{:.2f}"`).
- Override `template_name` / `results_template` / `form_template` (extend the `web/crud/*` templates and their blocks: `toolbar_extra`, `above_results`, `filters_extra`, `row_lead`, `row_actions`, `fields`) for pages that need more (bulk selection, extra dialogs).
- The results container uses `hx-disinherit="*"`; give every control inside it explicit `hx-target`/`hx-swap`.
- Page-specific CSS goes in `static/css/pages/<group>.css`, loaded from the page template's `{% block head %}`; shared tokens stay in `app.css`.
- Successful saves answer 204 with `HX-Trigger` `crud-refresh` + `close-modal` + `toast`; invalid forms answer 422 with the dialog re-rendered (htmx is configured to swap 422).

## Gotchas found in Phase 4

- `runserver --noreload` keeps serving cached templates: restart it after template edits when testing in a browser.
- Elements placed inside `.app` become grid cells; overlays (modal host) live outside it.
- htmx attributes inherit to descendants; a container with `hx-swap="outerHTML"` will hijack nested buttons unless `hx-disinherit` is set.

## Alpine patterns

- Global stores: `$store.theme` (`toggle()`, `set("light")`) and `$store.toasts` (`push(message, level)`).
- Reusable components in `static/js/app.js` (`drawer`, `navSection`, `passwordField`); add new ones there with `Alpine.data(...)` rather than long inline expressions.
- Hide until Alpine runs with `x-cloak`.
- Browser storage is only for per-browser conveniences (drawer and section state, theme copy) and always wrapped in try/catch.

## Theme

Resolution order (decision D6): the user's saved choice (`users.theme_preference`) → this browser's copy → the OS setting → dark. The toggle updates all three at once. The login page always uses the dark look, as in v3.

## Checks before merging a page

1. Tests for each view: access for allowed and refused roles, happy path, validation errors, htmx partial responses.
2. Open it in both themes and at phone width (no horizontal scroll); look for console errors.
3. Compare with the v3 screenshot in `docs/v3-baseline/screenshots/`.
