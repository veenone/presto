"""CrudPage with extra select filters next to the search box (e.g. product type).

The shared ``ListState`` only round-trips ``q``/``seg``/``sort``/``dir``/``page``.
Pages here also need select filters (v3 "Product Type" and similar), so this
module extends the state with an ``extra`` dict that every generated link keeps.
The selects post with the ``#crud-state`` form (``form="crud-state"``), so the
search box sends them too.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import ClassVar
from urllib.parse import urlencode

from apps.web.crud import PAGE_SIZE, CrudPage, ListState, ResultPage, _sort_key


@dataclass(frozen=True)
class SelectFilter:
    name: str      # query parameter
    label: str
    default: str = "all"


@dataclass
class FilteredState(ListState):
    extra: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)

    def query(self, **overrides) -> str:
        base = super().query(**{k: v for k, v in overrides.items() if k not in self.extra})
        values = {k: overrides.get(k, v) for k, v in self.extra.items()}
        extra = urlencode({k: v for k, v in values.items() if v and v != self.defaults.get(k)})
        return "&".join(p for p in (base, extra) if p)


class FilteredCrudPage(CrudPage):
    select_filters: ClassVar[list[SelectFilter]] = []

    # -- hooks ----------------------------------------------------------------
    def filter_choices(self, request, name: str) -> list[tuple[str, str]]:
        raise NotImplementedError

    def filter_match(self, row: dict, name: str, value: str) -> bool:
        return (row.get(name) or "") == value

    # -- state ----------------------------------------------------------------
    def parse_state(self, request) -> FilteredState:
        base = super().parse_state(request)
        extra, defaults = {}, {}
        for f in self.select_filters:
            value = request.GET.get(f.name, f.default) or f.default
            if value not in dict(self.filter_choices(request, f.name)):
                value = f.default
            extra[f.name], defaults[f.name] = value, f.default
        return FilteredState(**asdict(base), extra=extra, defaults=defaults)

    def build_results(self, request, state: ListState) -> ResultPage:
        """``CrudPage.build_results`` plus the select filters (tiles and counts stay on all rows, as v3)."""
        rows = self.get_rows(request)
        segments = self.get_segments(rows)
        seg_map = {s.key: s for s in segments}
        if state.seg not in seg_map:
            state.seg = "all"
        seg = seg_map.get(state.seg)
        filtered = [r for r in rows if seg is None or seg.predicate is None or seg.predicate(r)]
        extra = getattr(state, "extra", {})
        defaults = getattr(state, "defaults", {})
        for name, value in extra.items():
            if value != defaults.get(name):
                filtered = [r for r in filtered if self.filter_match(r, name, value)]
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
        ctx = super().context(request, **extra)
        state = extra.get("state")
        if state is not None:
            ctx["select_filters"] = [
                {"name": f.name, "label": f.label, "value": state.extra.get(f.name, f.default),
                 "choices": self.filter_choices(request, f.name)}
                for f in self.select_filters
            ]
        return ctx
