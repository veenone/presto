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
