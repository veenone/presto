"""Every v3 endpoint requires the same access level in v4.

Reads the v3 route signatures (RequireRole / get_current_user / public) from
``backend/src/api/routes.py`` and compares them with the auth on each ninja
operation, including auth inherited from the router default.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from ninja.constants import NOT_SET

from apps.api.api import api
from apps.api.auth import JWTAuth

V3_ROUTES = Path(__file__).resolve().parents[3] / "backend" / "src" / "api" / "routes.py"
METHODS = {"get", "post", "put", "patch", "delete"}

pytestmark = [
    pytest.mark.v3compat,
    pytest.mark.skipif(not V3_ROUTES.exists(), reason="v3 backend not available"),
]


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def v3_access() -> dict[tuple[str, str], str]:
    src = V3_ROUTES.read_text()
    lines = src.splitlines()
    result = {}
    for fn in ast.parse(src).body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        signature = "\n".join(lines[fn.lineno - 1: fn.body[0].lineno - 1])
        role = re.search(r'RequireRole\("(\w+)"\)', signature)
        if role:
            level = role.group(1)
        elif "get_current_user" in signature:
            level = "USER"
        elif "get_optional_user" in signature:
            level = "OPTIONAL"
        else:
            level = "PUBLIC"
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") in METHODS:
                result[(dec.func.attr.upper(), _norm("/api" + dec.args[0].value))] = level
    return result


def v4_access() -> dict[tuple[str, str], str]:
    result = {}
    for _prefix, router in api._routers:
        for path, view in router.path_operations.items():
            for op in view.operations:
                auth = op.auth_param if op.auth_param is not NOT_SET else router.auth
                if auth in (None, NOT_SET):
                    auths = []
                else:
                    auths = list(auth) if isinstance(auth, list | tuple) else [auth]
                if not auths:
                    level = "PUBLIC"
                elif isinstance(auths[0], JWTAuth):
                    level = auths[0].min_role or "USER"
                else:
                    level = type(auths[0]).__name__
                for method in op.methods:
                    result[(method, _norm("/api" + path))] = level
    return result


def test_access_levels_match_v3():
    v4 = v4_access()
    diffs = [
        f"{method} {path}: v3={level} v4={v4.get((method, path), 'MISSING')}"
        for (method, path), level in sorted(v3_access().items())
        if v4.get((method, path)) != level
    ]
    assert not diffs, "access level differs from v3:\n" + "\n".join(diffs)
