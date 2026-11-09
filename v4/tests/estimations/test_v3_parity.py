"""Numbers parity with v3: the same inputs through the v3 FastAPI app and the v4 API.

The v3 side runs in a subprocess (``backend/`` with its own SQLAlchemy models)
on a fresh v3 database with the same extra catalog rows; every computed field
of create, detail, preview, recalculate, revise, versions and diff must match.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from apps.catalog.models import (
    DocumentType,
    Feature,
    PublicHoliday,
    RiskItem,
    TaskTemplate,
    TaskTemplateFeature,
)

V4_DIR = Path(__file__).resolve().parents[2]
V3_DIR = V4_DIR.parent / "backend"

try:
    import fastapi  # noqa: F401
    import sqlalchemy  # noqa: F401
    HAVE_V3 = (V3_DIR / "src" / "api" / "app.py").exists()
except ImportError:
    HAVE_V3 = False

pytestmark = [
    pytest.mark.v3compat,
    pytest.mark.skipif(not HAVE_V3, reason="v3 backend, FastAPI or SQLAlchemy not available"),
]

# Extra catalog rows (explicit ids so both databases agree).
CATALOG = {
    "features": [
        {"id": 50, "name": "Custom eSIM", "category": "Custom", "complexity_weight": 1.5,
         "base_effort_hours": 10.0, "study_effort_hours": 20.0},
    ],
    "task_templates": [
        {"id": 60, "name": "Feature conformance", "task_type": "EXECUTION", "base_effort_hours": 6.0,
         "scales_with_dut": True, "scales_with_profile": True, "is_parallelizable": True},
        {"id": 61, "name": "PR regression pass", "task_type": "EXECUTION", "base_effort_hours": 3.0,
         "scales_with_dut": True, "is_pr_fix": True},
    ],
    "links": [[60, 1, None], [60, 2, 9.5], [60, 3, None]],
    "document_types": [
        {"id": 101, "name": "Parity Plan", "category": "Planning", "base_effort_hours": 6.0,
         "task_template_id": 7},
        {"id": 102, "name": "Parity Matrix", "category": "Report", "base_effort_hours": 4.0,
         "task_template_id": 7},
        {"id": 103, "name": "Parity Notes", "category": "Report", "base_effort_hours": 3.0,
         "task_template_id": None},
    ],
    "holidays": [["2026-12-25", "Christmas", True], ["2027-01-01", "New Year", False]],
    "risk_items": [{"id": 201, "name": "Parity risk", "category": "Schedule", "likelihood": "HIGH",
                    "impact": "LOW"}],
}

CREATE = {
    "project_name": "Parity", "project_type": "EVOLUTION",
    "feature_ids": [1, 2, 3, 50], "new_feature_ids": [2, 50], "reference_project_ids": [],
    "dut_ids": [1, 2, 3], "profile_ids": [1, 2], "dut_profile_matrix": [[1, 1], [2, 1], [3, 2]],
    "pr_fixes": {"simple": 2, "medium": 1, "complex": 1},
    "pr_details": [
        {"pr_number": "PR-1", "complexity": "simple", "test_available": False},
        {"pr_number": "PR-2", "complexity": "medium", "test_available": True},
        {"pr_number": "PR-3", "complexity": "complex", "test_available": False},
    ],
    "team_size": 2, "has_leader": True, "working_days": 15, "expected_releases": 3,
    "start_date": "2026-11-02", "expected_delivery": "2026-12-15",
    "testing_start_date": "2026-12-10", "risk_item_ids": [201],
    "document_type_ids": [101, 102, 103], "document_counts": {"101": 2},
    "task_assigned_testers": {"Feature conformance": 2},
}
REVISE = {**CREATE, "project_name": "Parity v2", "feature_ids": [1, 4], "new_feature_ids": [],
          "pr_fixes": {"simple": 0, "medium": 0, "complex": 0}, "pr_details": [],
          "document_type_ids": [102], "document_counts": {}}
PREVIEW = {k: v for k, v in CREATE.items() if k not in ("pr_details", "testing_start_date")}

V3_SCRIPT = textwrap.dedent("""
    import json, os, sys
    from datetime import date
    from sqlalchemy.orm import Session
    from src.database.migrations import init_database, get_engine
    from src.database.models import (DocumentType, Feature, PublicHoliday, RiskItem, TaskTemplate,
                                     TaskTemplateFeature)
    cat = json.loads(os.environ["PARITY_CATALOG"])
    payloads = json.loads(os.environ["PARITY_PAYLOADS"])
    init_database()
    with Session(get_engine()) as s:
        for f in cat["features"]:
            s.add(Feature(**f))
        for t in cat["task_templates"]:
            s.add(TaskTemplate(**t))
        s.flush()
        for tid, fid, ov in cat["links"]:
            s.add(TaskTemplateFeature(task_template_id=tid, feature_id=fid, base_hours_override=ov))
        for d in cat["document_types"]:
            s.add(DocumentType(**d))
        for d, n, r in cat["holidays"]:
            s.add(PublicHoliday(date=date.fromisoformat(d), name=n, is_recurring=r))
        for r in cat["risk_items"]:
            s.add(RiskItem(**r))
        s.commit()
    from fastapi.testclient import TestClient
    from src.api.app import app
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
    h = {"Authorization": "Bearer " + tok}
    out = {}
    est = c.post("/api/estimations", headers=h, json=payloads["create"]).json()
    eid = est["id"]
    out["create"] = est
    out["detail"] = c.get(f"/api/estimations/{eid}", headers=h).json()
    out["preview"] = c.post("/api/estimations/calculate", headers=h, json=payloads["preview"]).json()
    out["recalc"] = c.post(f"/api/estimations/{eid}/calculate", headers=h).json()
    c.post(f"/api/estimations/{eid}/status", headers=h, json={"status": "REVISED"})
    out["revised"] = c.put(f"/api/estimations/{eid}/revise", headers=h, json=payloads["revise"]).json()
    out["versions"] = c.get(f"/api/estimations/{eid}/versions", headers=h).json()
    out["diff"] = c.get(f"/api/estimations/{eid}/versions/1/diff/2", headers=h).json()
    print("JSON=" + json.dumps(out))
""")

VOLATILE = {"id", "created_at", "approved_at", "estimation_id", "assigned_to_id"}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _run_v3(tmp_path) -> dict:
    env = {**os.environ, "DB_PATH": str(tmp_path / "v3.db"), "PYTHONPATH": str(V3_DIR),
           "PARITY_CATALOG": json.dumps(CATALOG),
           "PARITY_PAYLOADS": json.dumps({"create": CREATE, "preview": PREVIEW, "revise": REVISE})}
    env.pop("DB_URL", None)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    proc = subprocess.run([sys.executable, "-c", V3_SCRIPT], cwd=V3_DIR, env=env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.split("JSON=", 1)[1])


def _load_catalog() -> None:
    for f in CATALOG["features"]:
        Feature.objects.create(**f)
    for t in CATALOG["task_templates"]:
        TaskTemplate.objects.create(**t)
    for tid, fid, ov in CATALOG["links"]:
        TaskTemplateFeature.objects.create(task_template_id=tid, feature_id=fid, base_hours_override=ov)
    for d in CATALOG["document_types"]:
        DocumentType.objects.create(**d)
    for d, n, r in CATALOG["holidays"]:
        PublicHoliday.objects.create(date=date.fromisoformat(d), name=n, is_recurring=r)
    for r in CATALOG["risk_items"]:
        RiskItem.objects.create(**r)


def _run_v4(api) -> dict:
    _load_catalog()
    out = {}
    est = api.post("/api/estimations", json=CREATE)
    assert est.status_code == 201, est.content
    est = est.json()
    eid = est["id"]
    out["create"] = est
    out["detail"] = api.get(f"/api/estimations/{eid}").json()
    out["preview"] = api.post("/api/estimations/calculate", json=PREVIEW).json()
    out["recalc"] = api.post(f"/api/estimations/{eid}/calculate").json()
    api.post(f"/api/estimations/{eid}/status", json={"status": "REVISED"})
    out["revised"] = api.put(f"/api/estimations/{eid}/revise", json=REVISE).json()
    out["versions"] = api.get(f"/api/estimations/{eid}/versions").json()
    out["diff"] = api.get(f"/api/estimations/{eid}/versions/1/diff/2").json()
    return out


def test_v4_matches_v3_numbers(api, tmp_path):
    v3 = _run_v3(tmp_path)
    v4 = _run_v4(api)
    # Sanity: the scenario exercises overrides, custom features, docs, PR no-test and holidays.
    detail = v4["detail"]
    assert any(t["task_type"] == "DOCUMENTATION" for t in detail["tasks"])
    assert any(t["task_type"] == "PR_TEST_CREATION" for t in detail["tasks"])
    assert any(t["task_name"] == "Custom eSIM" for t in detail["tasks"])
    assert detail["estimated_completion_date"] is not None
    assert detail["documentation_hours"] > 0
    for key in ("create", "detail", "preview", "recalc", "revised", "versions", "diff"):
        assert _strip(v4[key]) == _strip(v3[key]), key
