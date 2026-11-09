"""Backup and restore (/admin/backup, /admin/restore), including restoring a v3 backup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from apps.accounts.models import AuditLog
from apps.catalog.models import (
    DocumentType,
    Feature,
    PublicHoliday,
    RiskItem,
    TaskTemplate,
    TaskTemplateFeature,
)
from apps.teams.models import Team, TeamMember

V3_DIR = Path(__file__).resolve().parents[3] / "backend"

try:
    import sqlalchemy  # noqa: F401
    HAVE_V3 = (V3_DIR / "src" / "api" / "app.py").exists()
except ImportError:
    HAVE_V3 = False


def _backup(api) -> dict:
    resp = api.get("/api/admin/backup")
    assert resp.status_code == 200, resp.content
    return json.loads(resp.content)


def _restore(api, document, filename="backup.json"):
    content = document if isinstance(document, bytes) else json.dumps(document).encode()
    return api.post("/api/admin/restore", files={"file": (filename, content, "application/json")})


class TestBackup:
    def test_backup_download(self, api):
        resp = api.get("/api/admin/backup")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"
        assert resp["Content-Disposition"].startswith('attachment; filename="presto_backup_')
        data = json.loads(resp.content)
        assert data["version"] == "3.4.0"
        assert list(data["tables"]) == [
            "features", "task_templates", "task_template_features", "dut_types", "test_profiles",
            "teams", "team_members", "risk_items", "document_types", "public_holidays",
        ]
        assert len(data["tables"]["features"]) == Feature.objects.count()
        assert AuditLog.objects.filter(action="BACKUP").exists()

    def test_backup_uses_v3_column_names(self, api):
        team = Team.objects.create(name="QA")
        TeamMember.objects.create(name="Alice", role="TESTER", skills=["Automation"], team=team)
        data = _backup(api)
        member = data["tables"]["team_members"][0]
        assert member["skills_json"] == '["Automation"]'
        assert member["team_id"] == team.id
        assert "skills" not in member

    def test_backup_requires_admin(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).get("/api/admin/backup").status_code == 403


class TestRestore:
    def test_round_trip(self, api):
        team = Team.objects.create(name="QA", description="d")
        TeamMember.objects.create(name="Alice", role="TESTER", skills=["Automation"], team=team)
        tpl = TaskTemplate.objects.first()
        feat = Feature.objects.first()
        TaskTemplateFeature.objects.create(task_template=tpl, feature=feat, base_hours_override=3.5)
        DocumentType.objects.create(name="Test Plan", task_template=tpl)
        PublicHoliday.objects.create(date=date(2026, 12, 25), name="Xmas", is_recurring=True)
        RiskItem.objects.create(name="Late DUT", category="Schedule")
        snapshot = _backup(api)

        TeamMember.objects.all().delete()
        Team.objects.all().delete()
        PublicHoliday.objects.all().delete()
        Feature.objects.create(name="Will be removed")

        resp = _restore(api, snapshot)
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok"
        assert data["tables_restored"] == list(snapshot["tables"])
        assert data["rows_restored"]["team_members"] == 1

        assert not Feature.objects.filter(name="Will be removed").exists()
        member = TeamMember.objects.get(name="Alice")
        assert member.skills == ["Automation"] and member.team.name == "QA"
        assert TaskTemplateFeature.objects.get(task_template=tpl, feature=feat).base_hours_override == 3.5
        assert DocumentType.objects.get(name="Test Plan").task_template_id == tpl.id
        assert PublicHoliday.objects.get(name="Xmas").date == date(2026, 12, 25)
        assert _backup(api)["tables"] == snapshot["tables"]
        assert AuditLog.objects.filter(action="RESTORE").exists()

    def test_restored_ids_do_not_collide_with_new_rows(self, api):
        snapshot = _backup(api)
        _restore(api, snapshot)
        max_id = max(r["id"] for r in snapshot["tables"]["features"])
        assert Feature.objects.create(name="New after restore").id > max_id

    def test_partial_backup_only_replaces_listed_tables(self, api):
        Team.objects.create(name="Keep me")
        resp = _restore(api, {"tables": {"risk_items": [{"id": 1, "name": "Only risk"}]}})
        assert resp.status_code == 200
        assert list(RiskItem.objects.values_list("name", flat=True)) == ["Only risk"]
        assert Team.objects.filter(name="Keep me").exists()

    def test_dangling_nullable_fk_is_dropped(self, api):
        resp = _restore(api, {"tables": {"team_members": [
            {"id": 1, "name": "Bob", "role": "TESTER", "skills_json": "[]", "team_id": 999},
        ]}})
        assert resp.status_code == 200
        assert TeamMember.objects.get(name="Bob").team_id is None

    def test_invalid_json(self, api):
        resp = _restore(api, b"{not json")
        assert resp.status_code == 400
        assert resp.json()["detail"].startswith("Invalid JSON file:")

    def test_missing_tables_key(self, api):
        resp = _restore(api, {"version": "3.4.0"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid backup format: missing 'tables' key"

    def test_bad_rows_roll_back(self, api):
        before = Feature.objects.count()
        resp = _restore(api, {"tables": {"features": [{"id": 1, "name": None}]}})
        assert resp.status_code == 500
        assert resp.json()["detail"].startswith("Restore failed:")
        assert Feature.objects.count() == before

    def test_restore_requires_admin(self, api_as, make_user):
        resp = _restore(api_as(make_user("APPROVER")), {"tables": {}})
        assert resp.status_code == 403


V3_MAKE_BACKUP = textwrap.dedent("""
    import json
    from datetime import date
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from src.database.migrations import init_database, get_engine
    from src.database.models import (Team, TeamMember, RiskItem, PublicHoliday, DocumentType,
                                     TaskTemplateFeature)
    init_database()
    with Session(get_engine()) as s:
        t = Team(name="V3 Team", description="from v3"); s.add(t); s.flush()
        s.add(TeamMember(name="Carol", role="LEADER", skills_json='["Automation", "Security"]',
                         team_id=t.id, available_hours_per_day=6.5))
        s.add(RiskItem(name="V3 risk", category="Schedule", likelihood="HIGH", impact="LOW"))
        s.add(PublicHoliday(date=date(2026, 5, 1), name="Labour Day", country="FR", is_recurring=True))
        s.add(DocumentType(name="V3 Plan", task_template_id=1, base_effort_hours=6))
        s.add(TaskTemplateFeature(task_template_id=1, feature_id=2, base_hours_override=2.5))
        s.commit()
    from src.api.app import app
    client = TestClient(app)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()
    resp = client.get("/api/admin/backup",
                      headers={"Authorization": "Bearer " + token["access_token"]})
    assert resp.status_code == 200, resp.text
    print("BACKUP=" + resp.text.replace("\\n", " "))
""")


@pytest.fixture(scope="module")
def v3_backup(tmp_path_factory) -> dict:
    db = tmp_path_factory.mktemp("v3") / "v3.db"
    env = {**os.environ, "DB_PATH": str(db), "PYTHONPATH": str(V3_DIR)}
    env.pop("DB_URL", None)
    proc = subprocess.run([sys.executable, "-c", V3_MAKE_BACKUP], cwd=V3_DIR, env=env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.split("BACKUP=", 1)[1])


@pytest.mark.v3compat
@pytest.mark.skipif(not HAVE_V3, reason="v3 backend or SQLAlchemy not available")
class TestV3Backup:
    def test_restore_v3_backup(self, api, v3_backup):
        resp = _restore(api, v3_backup)
        assert resp.status_code == 200, resp.content
        assert resp.json()["rows_restored"]["team_members"] == 1

        carol = TeamMember.objects.get(name="Carol")
        assert carol.skills == ["Automation", "Security"]
        assert carol.team.name == "V3 Team"
        assert carol.available_hours_per_day == 6.5
        assert RiskItem.objects.get(name="V3 risk").likelihood == "HIGH"
        holiday = PublicHoliday.objects.get(name="Labour Day")
        assert holiday.date == date(2026, 5, 1) and holiday.is_recurring and holiday.country == "FR"
        assert DocumentType.objects.get(name="V3 Plan").task_template_id == 1
        assert TaskTemplateFeature.objects.get(pk=(1, 2)).base_hours_override == 2.5
        assert Feature.objects.count() == len(v3_backup["tables"]["features"])

    def test_v4_backup_has_v3_shape(self, api, v3_backup):
        _restore(api, v3_backup)
        v4 = _backup(api)
        assert list(v4["tables"]) == list(v3_backup["tables"])
        for table, rows in v3_backup["tables"].items():
            if rows:
                assert set(v4["tables"][table][0]) == set(rows[0]), table
        # Same data back out, apart from timestamp formatting.
        for table in ("team_members", "risk_items", "public_holidays", "task_template_features"):
            strip = [{k: v for k, v in r.items() if k != "created_at"} for r in v3_backup["tables"][table]]
            got = [{k: v for k, v in r.items() if k != "created_at"} for r in v4["tables"][table]]
            assert got == strip, table
