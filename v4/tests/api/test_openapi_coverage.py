"""Every v3 endpoint must exist in v4 (path parameter names may differ).

The v3 snapshot lives in docs/v3-baseline/openapi-v3.json. Intentional removals
go in ALLOWED_REMOVALS with a reason (and in the CHANGELOG).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from apps.api.api import api

V3_SPEC = Path(__file__).resolve().parents[3] / "docs" / "v3-baseline" / "openapi-v3.json"
METHODS = {"get", "post", "put", "patch", "delete"}
ALLOWED_REMOVALS: dict[tuple[str, str], str] = {
    # Streamlit health polling stubs, not features.
    ("GET", "/_stcore/health"): "Streamlit removed",
    ("GET", "/_stcore/host-config"): "Streamlit removed",
    ("GET", "/{}/_stcore/health"): "Streamlit removed",
    ("GET", "/{}/_stcore/host-config"): "Streamlit removed",
}


def _ops(paths: dict) -> set[tuple[str, str]]:
    return {
        (m.upper(), re.sub(r"\{[^}]+\}", "{}", p))
        for p, item in paths.items() for m in item if m in METHODS
    }


def missing_operations() -> list[tuple[str, str]]:
    v3 = _ops(json.loads(V3_SPEC.read_text())["paths"])
    v4 = _ops(api.get_openapi_schema()["paths"])
    return sorted(op for op in v3 - v4 if op not in ALLOWED_REMOVALS)


def test_all_v3_operations_exist():
    missing = missing_operations()
    assert not missing, f"{len(missing)} v3 operations missing in v4:\n" + "\n".join(f"  {m} {p}" for m, p in missing)


if __name__ == "__main__":  # quick report: python -m tests.api.test_openapi_coverage
    import django
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "presto.settings_test")
    django.setup()
    for m, p in missing_operations():
        print(m, p)
