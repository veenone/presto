"""Shared fixtures.

``api`` is an ``ApiClient`` logged in as the seeded admin. Its methods mirror
the FastAPI ``TestClient`` used by the v3 tests (``json=``, ``params=``,
``files=``), so ported tests need few changes::

    def test_create(api):
        resp = api.post("/api/dut-types", json={"name": "X"})
        assert resp.status_code == 201
"""

from __future__ import annotations

import json as jsonlib
from urllib.parse import urlencode

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts import tokens
from apps.accounts.models import User
from apps.core import bootstrap


class ApiClient:
    def __init__(self, client, user: User | None = None, headers: dict | None = None):
        self.client = client
        self.user = user
        self.headers = dict(headers or {})
        if user is not None:
            self.headers["Authorization"] = f"Bearer {tokens.create_access_token(user)}"

    def _url(self, path: str, params: dict | None) -> str:
        if not params:
            return path
        return f"{path}{'&' if '?' in path else '?'}{urlencode(params, doseq=True)}"

    def _send(self, method, path, *, json=None, data=None, files=None, params=None, headers=None):
        call = getattr(self.client, method)
        hdrs = {**self.headers, **(headers or {})}
        url = self._url(path, params)
        if files:
            payload = dict(data or {})
            for name, spec in files.items():
                filename, content, *rest = spec
                if hasattr(content, "read"):
                    content = content.read()
                payload[name] = SimpleUploadedFile(filename, content, rest[0] if rest else "application/octet-stream")
            return call(url, data=payload, headers=hdrs)
        if json is not None:
            return call(url, data=jsonlib.dumps(json, default=str), content_type="application/json", headers=hdrs)
        if method == "get":
            return call(url, headers=hdrs)
        return call(url, data=data or {}, headers=hdrs) if data else call(url, headers=hdrs)

    def get(self, path, **kw):
        return self._send("get", path, **kw)

    def post(self, path, **kw):
        return self._send("post", path, **kw)

    def put(self, path, **kw):
        return self._send("put", path, **kw)

    def patch(self, path, **kw):
        return self._send("patch", path, **kw)

    def delete(self, path, **kw):
        return self._send("delete", path, **kw)


@pytest.fixture
def seeded(db) -> User:
    """Fresh-install data (seed catalog, config defaults); returns the admin user."""
    bootstrap.load_seed_data()
    bootstrap.ensure_defaults()
    return User.objects.get(username="admin")


@pytest.fixture
def admin_user(seeded) -> User:
    return seeded


@pytest.fixture
def make_user(db):
    """Factory: ``make_user("ESTIMATOR", username="est")``."""
    def _make(role: str = "VIEWER", username: str | None = None, password: str = "pw", **extra) -> User:
        username = username or f"{role.lower()}_{User.objects.count() + 1}"
        return User.objects.create_user(username, password, role=role, **extra)
    return _make


@pytest.fixture
def api(client, admin_user) -> ApiClient:
    """API client authenticated as the seeded admin."""
    return ApiClient(client, admin_user)


@pytest.fixture
def anon_api(client, db) -> ApiClient:
    return ApiClient(client)


@pytest.fixture
def api_as(client):
    """Factory: ``api_as(user)`` returns a client authenticated as ``user``."""
    return lambda user: ApiClient(client, user)


@pytest.fixture
def auth_headers(admin_user) -> dict:
    """v3-style headers dict for the admin."""
    return {"Authorization": f"Bearer {tokens.create_access_token(admin_user)}"}
