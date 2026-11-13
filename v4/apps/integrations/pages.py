"""DUT Assets (v3 pages/assets.py) and PR Registry (v3 pages/pr_registry.py) pages.

Both list data fetched from an external system through ``apps.integrations.service``
(Snipe-IT hardware, Jira problem reports). The fetched rows are cached for a few
minutes so that filtering, sorting and paging do not call the remote system
again; the toolbar's Refresh button drops the cache and reloads the page.
"""

from __future__ import annotations

import re
from collections import Counter

from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path as url_path
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.roles import has_role
from apps.catalog import services as catalog_services
from apps.catalog.models import DutType
from apps.catalog.pages.risks import ToneBadge
from apps.core.errors import ServiceError
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.htmx import trigger

from . import service

ASSETS_CACHE_KEY = "registries:snipeit-assets"
PR_CACHE_KEY = "registries:jira-pr-items"
CACHE_SECONDS = 600


class _ExternalListPage(CrudPage):
    """A read-only list of rows fetched from an integration."""

    can_add = can_edit = can_delete = False
    cache_key: str = ""

    def user_can_write(self, request) -> bool:
        return False  # no add/edit/delete: the Actions column stays hidden

    def _refresh_response(self) -> HttpResponse:
        resp = HttpResponse(status=204)
        resp["HX-Refresh"] = "true"  # full reload: banners and tiles are part of the page
        return resp


# ── DUT assets (Snipe-IT) ────────────────────────────────────────────────────

class AssetsPage(_ExternalListPage):
    url_name = "assets"
    title = "DUT Assets"
    noun = "Asset"
    import_role = "APPROVER"  # import creates DUT types: POST /api/dut-types
    cache_key = ASSETS_CACHE_KEY
    template_name = "registries/assets_page.html"
    results_template = "registries/_assets_results.html"
    search_placeholder = "Search by name, serial, model, status…"
    default_sort = "name"
    columns = [
        Column("name", "Name", sortable=True, kind="strong"),
        Column("serial", "Serial", sortable=True, kind="mono"),
        Column("model_name", "Model", sortable=True),
        Column("category", "Category", sortable=True),
        Column("status", "Status", sortable=True),
        Column("asset_tag", "Asset Tag", sortable=True, kind="mono"),
    ]
    search_fields = ["name", "serial", "model_name", "category", "status", "asset_tag"]

    def load(self, request) -> dict:
        """Rows plus state: ``{"rows", "configured", "error", "categories"}`` (memoised per request)."""
        if hasattr(request, "_assets_state"):
            return request._assets_state
        categories = service.snipeit_saved_categories()
        state = {"rows": [], "configured": True, "error": "", "categories": categories}
        cached = cache.get(self.cache_key)
        if cached is not None:
            state["rows"] = cached
        else:
            try:
                rows = service.snipeit_assets(categories)
            except ServiceError as exc:
                if exc.status_code == 400:
                    state["configured"] = False
                else:
                    state["error"] = exc.detail
            else:
                state["rows"] = [r for r in rows if r.get("id") is not None]
                cache.set(self.cache_key, state["rows"], CACHE_SECONDS)
        request._assets_state = state
        return state

    def get_rows(self, request):
        return [dict(r) for r in self.load(request)["rows"]]

    @staticmethod
    def _categories(rows) -> list[str]:
        saved = service.snipeit_saved_categories()
        return saved or sorted({r.get("category") or "" for r in rows} - {""})

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(c, c, lambda r, c=c: (r.get("category") or "") == c) for c in self._categories(rows)
        ]

    def get_tiles(self, rows):
        cats = service.snipeit_saved_categories()
        tiles = [Tile("Total Assets", str(len(rows)), f"{len(cats)} categories tracked", "all")]
        for status, n in Counter((r.get("status") or "Unknown") for r in rows).most_common(3):
            tiles.append(Tile(status, str(n), "by status"))
        return tiles

    def user_can_import(self, request) -> bool:
        return has_role(request.user.role, self.import_role)

    def extra_context(self, request):
        st = self.load(request)
        return {"can_import": self.user_can_import(request), "configured": st["configured"],
                "load_error": st["error"]}

    # -- actions ----------------------------------------------------------------
    def refresh_view(self, request):
        self.check_view(request)
        cache.delete(self.cache_key)
        return self._refresh_response()

    def import_view(self, request):
        """Create a DUT type for each selected asset (v3 "Import as DUT")."""
        self.check_view(request)
        if not self.user_can_import(request):
            raise PermissionDenied
        wanted = set(request.POST.getlist("ids"))
        if not wanted:
            return JsonResponse({"detail": "No assets selected."}, status=400)
        assets = [r for r in self.load(request)["rows"] if str(r.get("id")) in wanted]
        if not assets:
            return JsonResponse({"detail": "The selected assets are no longer listed. Refresh and try again."},
                                status=400)
        existing = {n.lower() for n in DutType.objects.values_list("name", flat=True)}
        created, skipped, failed = 0, [], []
        for asset in assets:
            name = (asset.get("name") or "Unknown").strip() or "Unknown"
            if name.lower() in existing:
                skipped.append(name)
                continue
            try:
                catalog_services.create_dut_type({
                    "name": name, "category": asset.get("category") or "Other",
                    "complexity_multiplier": 1.0, "product_type": None,
                })
            except ServiceError as exc:
                failed.append(f"{name}: {exc.detail}")
            else:
                created += 1
                existing.add(name.lower())
        lines = []
        if created:
            lines.append(f"Successfully imported {created} asset(s) as DUT entries.")
        if skipped:
            lines.append(f"Skipped {len(skipped)} duplicate(s): {', '.join(skipped)}")
        if failed:
            lines.append(f"Failed {len(failed)}: {'; '.join(failed)}")
        level = "error" if failed and not created else "warning" if (failed or skipped) else "success"
        resp = HttpResponse(status=204)
        return trigger(resp, **{"assets-imported": True}, toast={"message": "\n".join(lines), "level": level})

    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        return [
            url_path(prefix, require_GET(page.list_view), name=cls.url_name),
            url_path(f"{prefix}/refresh", require_POST(page.refresh_view), name="assets_refresh"),
            url_path(f"{prefix}/import", require_POST(page.import_view), name="assets_import"),
        ]


# ── PR registry (Jira problem reports) ───────────────────────────────────────

PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
PRIORITY_TONES = {"CRITICAL": "negative", "HIGH": "negative", "MEDIUM": "warning"}
DONE_STATUSES = ("done", "closed", "resolved")


class KeyCell(str):
    """The issue key, rendered as a button that opens the detail dialog."""

    def __str__(self) -> str:
        return self

    def __html__(self) -> str:
        key = str.__str__(self)
        return format_html(
            '<button type="button" class="reg-key-link" hx-get="/pr-registry/item/{}" '
            'hx-target="#modal" title="Show details">{}</button>', key, key)


def render_jira_markup(text: str) -> str:
    """PR description as safe HTML: escaped text, line breaks, Jira ``{color}`` and ``{^}`` macros.

    v3 rendered Markdown first (markdown2, not a v4 dependency); plain text is kept here.
    """
    if not text:
        return ""
    body = escape(text)
    body = re.sub(r"\{color:([#\w]+)\}(.*?)\{color\}", r'<span style="color:\1">\2</span>', body, flags=re.S)
    body = re.sub(r"\{\^\}(.*?)\{\^\}", r"<sup>\1</sup>", body, flags=re.S)
    body = re.sub(r"\{color(:[#\w]+)?\}", "", body)
    return body.replace("\n", "<br>")


def _pri(row: dict) -> str:
    return str(row.get("priority") or "").upper()


class PrRegistryPage(_ExternalListPage):
    url_name = "pr_registry"
    title = "PR Registry"
    noun = "PR"
    cache_key = PR_CACHE_KEY
    template_name = "registries/pr_page.html"
    search_placeholder = "Search by key or summary…"
    default_sort = "key"
    columns = [
        Column("key", "Key", sortable=True, kind="mono"),
        Column("summary", "Summary", sortable=True),
        Column("priority", "Priority", sortable=True),
        Column("status", "Status", sortable=True),
        Column("issue_type", "Type", sortable=True),
        Column("created", "Created", sortable=True, kind="mono"),
    ]
    search_fields = ["key", "summary"]

    def cached_items(self) -> list[dict] | None:
        return cache.get(self.cache_key)

    def get_rows(self, request):
        return [
            {**r, "id": r.get("key"), "key": KeyCell(r.get("key") or ""),
             "priority": ToneBadge(r.get("priority") or "", PRIORITY_TONES.get(_pri(r), "positive"))
             if r.get("priority") else None,
             "created": (r.get("created") or "")[:10]}
            for r in (self.cached_items() or [])
        ]

    def get_segments(self, rows):
        segs = [Segment("all", "All")]
        for p in PRIORITIES:
            if p == "HIGH":  # v3: the High filter also shows Critical items
                segs.append(Segment("high", "High", lambda r: _pri(r) in ("HIGH", "CRITICAL")))
            else:
                segs.append(Segment(p.lower(), p.title(), lambda r, p=p: _pri(r) == p))
        return segs

    def get_tiles(self, rows):
        def n(*ps):
            return sum(1 for r in rows if _pri(r) in ps)
        open_n = sum(1 for r in rows if str(r.get("status") or "").lower() not in DONE_STATUSES)
        loaded = self.cached_items() is not None
        tiles = [Tile("Total PRs", str(len(rows)), f"{open_n} open" if loaded else "click Refresh to load", "all")]
        if n("HIGH", "CRITICAL"):
            tiles.append(Tile("High / Critical", str(n("HIGH", "CRITICAL")), "filter to high priority", "high"))
        if n("MEDIUM"):
            tiles.append(Tile("Medium", str(n("MEDIUM")), "filter to medium", "medium"))
        if n("LOW"):
            tiles.append(Tile("Low", str(n("LOW")), "filter to low", "low"))
        return tiles

    def extra_context(self, request):
        return {"configured": service.jira_pr_configured()}

    # -- actions ----------------------------------------------------------------
    def refresh_view(self, request):
        self.check_view(request)
        try:
            items = service.jira_pr_items()
        except ServiceError as exc:
            return JsonResponse({"detail": f"Failed to load PR items: {exc.detail}"}, status=exc.status_code)
        cache.set(self.cache_key, items, CACHE_SECONDS)
        messages.success(request, f"Loaded {len(items)} PR item(s).")
        return self._refresh_response()

    def detail_view(self, request, key: str):
        self.check_view(request)
        row = next((r for r in (self.cached_items() or []) if r.get("key") == key), None)
        if row is None:
            return JsonResponse({"detail": "PR item not found. Click Refresh to reload the list."}, status=404)
        desc = (row.get("description") or "").strip()
        lines = len(desc.splitlines())
        return render(request, "registries/_pr_detail.html", self.context(
            request, item=row, tone=PRIORITY_TONES.get(_pri(row), "positive"),
            description=mark_safe(render_jira_markup(desc)),  # noqa: S308 - escaped above
            desc_caption="Description" + (f" · {lines} lines" if "\n" in desc else ""),
            created=(row.get("created") or "")[:10]))

    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        return [
            url_path(prefix, require_GET(page.list_view), name=cls.url_name),
            url_path(f"{prefix}/refresh", require_POST(page.refresh_view), name="pr_registry_refresh"),
            url_path(f"{prefix}/item/<str:key>", require_GET(page.detail_view), name="pr_registry_item"),
        ]
