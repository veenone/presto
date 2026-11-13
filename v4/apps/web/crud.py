"""Reusable list + add/edit/delete pages (the v3 data-management page pattern).

A page is a :class:`CrudPage` subclass that declares columns, search fields,
filter segments, stat tiles, a form and the service calls. Saving always goes
through the domain service layer, so rules and messages match the REST API.

Request flow (htmx):

* ``GET /<path>``                 full page; with ``HX-Target: crud-results`` only the results partial
* ``GET|POST /<path>/new``        add form in the modal
* ``GET|POST /<path>/<id>/edit``  edit form in the modal
* ``GET|POST /<path>/<id>/delete`` delete confirmation in the modal

A successful POST answers 204 with ``HX-Trigger`` events ``crud-refresh``,
``close-modal`` and ``toast``. Validation or service errors answer 422 with the
form re-rendered inside the modal.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import path as url_path

from apps.accounts.roles import has_role
from apps.core.errors import ServiceError

from .htmx import is_htmx, trigger
from .navigation import ITEMS_BY_NAME, allowed

PAGE_SIZE = 25


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    sortable: bool = False
    kind: str = "text"  # text | strong | mono | truncate | bool | badge | number
    align: str = "left"
    fmt: str = ""  # format string for number/mono values, e.g. "{:.2f}"


@dataclass(frozen=True)
class Segment:
    key: str
    label: str
    predicate: Callable[[dict], bool] | None = None  # None = all rows


@dataclass
class Tile:
    label: str
    value: str
    sub: str = ""
    segment: str | None = None  # clicking filters to this segment


@dataclass
class ListState:
    q: str = ""
    seg: str = "all"
    sort: str = ""
    dir: str = "asc"
    page: int = 1

    def query(self, **overrides) -> str:
        """Query string for this state with some values replaced (defaults omitted)."""
        p = {"q": self.q, "seg": self.seg, "sort": self.sort, "dir": self.dir, "page": self.page, **overrides}
        out: dict[str, Any] = {}
        if p["q"]:
            out["q"] = p["q"]
        if p["seg"] and p["seg"] != "all":
            out["seg"] = p["seg"]
        if p["sort"]:
            out["sort"] = p["sort"]
            if p["dir"] == "desc":
                out["dir"] = "desc"
        if int(p["page"] or 1) > 1:
            out["page"] = p["page"]
        return urlencode(out)


@dataclass
class ResultPage:
    rows: list[dict]
    total: int
    filtered: int
    page: int
    pages: int
    start: int
    end: int
    tiles: list[Tile] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)


class CrudPage:
    # -- identity -----------------------------------------------------------
    url_name: ClassVar[str]          # nav item name (apps.web.navigation)
    title: ClassVar[str]
    noun: ClassVar[str]              # "Profile"
    columns: ClassVar[list[Column]]
    search_fields: ClassVar[list[str]] = []
    default_sort: ClassVar[str] = "id"
    form_class: ClassVar[type[forms.Form] | None] = None
    write_role: ClassVar[str] = "ESTIMATOR"   # minimum role for add/edit/delete (API parity)
    can_add: ClassVar[bool] = True
    can_edit: ClassVar[bool] = True
    can_delete: ClassVar[bool] = True
    search_placeholder: ClassVar[str] = "Search…"
    template_name: ClassVar[str] = "web/crud/page.html"
    results_template: ClassVar[str] = "web/crud/_results.html"
    form_template: ClassVar[str] = "web/crud/_form_modal.html"

    # -- data hooks (override) ----------------------------------------------
    def get_rows(self, request) -> list[dict]:
        raise NotImplementedError

    def get_segments(self, rows: list[dict]) -> list[Segment]:
        return [Segment("all", "All")]

    def get_tiles(self, rows: list[dict]) -> list[Tile]:
        return [Tile(f"Total {self.title}", str(len(rows)), segment="all")]

    def get_initial(self, request, obj_id: int) -> dict:
        raise NotImplementedError

    def get_form(self, request, data=None, initial=None, obj_id: int | None = None) -> forms.Form:
        assert self.form_class is not None
        return self.form_class(data=data, initial=initial)

    def create(self, request, data: dict) -> Any:
        raise NotImplementedError

    def update(self, request, obj_id: int, data: dict) -> Any:
        raise NotImplementedError

    def delete(self, request, obj_id: int) -> None:
        raise NotImplementedError

    def object_label(self, request, obj_id: int) -> str:
        return f"{self.noun} {obj_id}"

    def extra_context(self, request) -> dict:
        return {}

    # -- access ---------------------------------------------------------------
    def check_view(self, request) -> None:
        item = ITEMS_BY_NAME.get(self.url_name)
        if item and not allowed(request.user, item.requires):
            raise PermissionDenied

    def user_can_write(self, request) -> bool:
        return has_role(request.user.role, self.write_role)

    def check_write(self, request) -> None:
        self.check_view(request)
        if not self.user_can_write(request):
            raise PermissionDenied

    # -- list -----------------------------------------------------------------
    def parse_state(self, request) -> ListState:
        g = request.GET
        sortable = {c.key for c in self.columns if c.sortable}
        sort = g.get("sort", "")
        try:
            page = max(1, int(g.get("page", 1)))
        except ValueError:
            page = 1
        return ListState(
            q=g.get("q", "").strip(), seg=g.get("seg", "all") or "all",
            sort=sort if sort in sortable else "", dir="desc" if g.get("dir") == "desc" else "asc", page=page,
        )

    def _matches(self, row: dict, q: str) -> bool:
        q = q.lower()
        return any(q in str(row.get(f) if row.get(f) is not None else "").lower() for f in self.search_fields)

    def build_results(self, request, state: ListState) -> ResultPage:
        rows = self.get_rows(request)
        segments = self.get_segments(rows)
        seg_map = {s.key: s for s in segments}
        if state.seg not in seg_map:
            state.seg = "all"
        seg = seg_map.get(state.seg)
        filtered = [r for r in rows if seg is None or seg.predicate is None or seg.predicate(r)]
        if state.q:
            filtered = [r for r in filtered if self._matches(r, state.q)]
        key = state.sort or self.default_sort
        filtered.sort(key=lambda r: _sort_key(r.get(key)), reverse=state.dir == "desc")
        pages = max(1, math.ceil(len(filtered) / PAGE_SIZE))
        state.page = min(state.page, pages)
        start = (state.page - 1) * PAGE_SIZE
        return ResultPage(
            rows=filtered[start:start + PAGE_SIZE], total=len(rows), filtered=len(filtered),
            page=state.page, pages=pages, start=start + 1 if filtered else 0,
            end=min(start + PAGE_SIZE, len(filtered)), tiles=self.get_tiles(rows),
            segments=[
                {"key": s.key, "label": s.label,
                 "count": sum(1 for r in rows if s.predicate is None or s.predicate(r))}
                for s in segments
            ],
        )

    def context(self, request, **extra) -> dict:
        return {"crud": self, "page_title": self.title, "can_write": self.user_can_write(request),
                "list_path": request.path if not extra.get("list_path") else extra["list_path"],
                **self.extra_context(request), **extra}

    def links(self, base: str, state: ListState, results: ResultPage) -> dict:
        """Precomputed URLs for segments, sortable headers and paging."""
        def url(**o) -> str:
            qs = state.query(**o)
            return f"{base}?{qs}" if qs else base
        sort_urls = {}
        for col in self.columns:
            if col.sortable:
                desc = state.sort == col.key and state.dir == "asc"
                sort_urls[col.key] = url(sort=col.key, dir="desc" if desc else "asc", page=1)
        return {
            "self": url(),
            "segments": {s["key"]: url(seg=s["key"], page=1) for s in results.segments},
            "sort": sort_urls,
            "prev": url(page=results.page - 1) if results.page > 1 else "",
            "next": url(page=results.page + 1) if results.page < results.pages else "",
        }

    def list_view(self, request):
        self.check_view(request)
        state = self.parse_state(request)
        results = self.build_results(request, state)
        ctx = self.context(request, state=state, results=results, list_path=request.path,
                           links=self.links(request.path, state, results))
        if is_htmx(request) and request.headers.get("HX-Target") == "crud-results":
            return render(request, self.results_template, ctx)
        return render(request, self.template_name, ctx)

    # -- forms ----------------------------------------------------------------
    def _form_response(self, request, form, action: str, heading: str, status: int = 200):
        return render(request, self.form_template, self.context(
            request, form=form, action=action, heading=heading), status=status)

    def _saved(self, message: str) -> HttpResponse:
        resp = HttpResponse(status=204)
        return trigger(resp, **{"crud-refresh": True, "close-modal": True},
                       toast={"message": message, "level": "success"})

    def _submit(self, request, form, save: Callable[[dict], Any], action: str, heading: str, message: str):
        if form.is_valid():
            try:
                save(form.cleaned_data)
            except ServiceError as exc:
                form.add_error(None, exc.detail)
            else:
                return self._saved(message)
        return self._form_response(request, form, action, heading, status=422)

    def new_view(self, request):
        self.check_write(request)
        if not self.can_add:
            raise Http404
        action, heading = request.path, f"Add {self.noun}"
        if request.method == "POST":
            form = self.get_form(request, data=request.POST)
            return self._submit(request, form, lambda d: self.create(request, d), action, heading,
                                f"{self.noun} created.")
        return self._form_response(request, self.get_form(request), action, heading)

    def edit_view(self, request, obj_id: int):
        self.check_write(request)
        if not self.can_edit:
            raise Http404
        initial = self.get_initial(request, obj_id)  # raises Http404 for unknown ids
        action, heading = request.path, f"Edit {self.noun}"
        if request.method == "POST":
            form = self.get_form(request, data=request.POST, initial=initial, obj_id=obj_id)
            return self._submit(request, form, lambda d: self.update(request, obj_id, d), action, heading,
                                f"{self.noun} updated.")
        return self._form_response(request, self.get_form(request, initial=initial, obj_id=obj_id),
                                   action, heading)

    def delete_view(self, request, obj_id: int):
        self.check_write(request)
        if not self.can_delete:
            raise Http404
        label = self.object_label(request, obj_id)
        if request.method == "POST":
            try:
                self.delete(request, obj_id)
            except ServiceError as exc:
                return render(request, "web/crud/_delete_modal.html", self.context(
                    request, label=label, action=request.path, error=exc.detail), status=422)
            return self._saved(f"{self.noun} deleted.")
        return render(request, "web/crud/_delete_modal.html",
                      self.context(request, label=label, action=request.path))

    # -- routing --------------------------------------------------------------
    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        return [
            url_path(f"{prefix}", page.list_view, name=cls.url_name),
            url_path(f"{prefix}/new", page.new_view, name=f"{cls.url_name}_new"),
            url_path(f"{prefix}/<int:obj_id>/edit", page.edit_view, name=f"{cls.url_name}_edit"),
            url_path(f"{prefix}/<int:obj_id>/delete", page.delete_view, name=f"{cls.url_name}_delete"),
        ]


def _sort_key(value: Any) -> tuple:
    """Sort None last; numbers numerically; everything else case-insensitively."""
    if value is None or value == "":
        return (2, "")
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, int | float):
        return (0, value)
    return (1, str(value).lower())


def rows_from(objects: Iterable[Any], fields: list[str]) -> list[dict]:
    return [{f: getattr(o, f) for f in fields} for o in objects]
