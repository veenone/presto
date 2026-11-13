"""Adopting a real v3 database (built by the v3 code) with the v4 models.

These tests need the v3 backend (``../backend``) and its dependencies, which
stay in the repo until the v3 code is removed at the end of the migration.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from django.db import connection

from apps.core import bootstrap

V4_DIR = Path(__file__).resolve().parent.parent
V3_DIR = V4_DIR.parent / "backend"

try:
    import sqlalchemy  # noqa: F401
    HAVE_V3 = (V3_DIR / "src" / "database" / "migrations.py").exists()
except ImportError:
    HAVE_V3 = False

pytestmark = [
    pytest.mark.v3compat,
    pytest.mark.skipif(not HAVE_V3, reason="v3 backend or SQLAlchemy not available"),
]

V3_SAMPLE_DATA = textwrap.dedent("""
    from datetime import date
    from sqlalchemy.orm import Session
    from src.database.migrations import init_database, get_engine
    from src.database.models import (Request, Estimation, EstimationTask, TeamMember,
                                     TaskTemplateFeature)
    from src.auth.models import AuditLog
    init_database()
    with Session(get_engine()) as s:
        s.add(TeamMember(name="Alice", role="TESTER", skills_json='["Automation"]'))
        r = Request(request_number="REQ_26/0001", title="SIM Toolkit v2.5",
                    requester_name="PO", received_date=date(2026, 10, 20))
        s.add(r); s.flush()
        e = Estimation(project_name="SIM Toolkit v2.5", project_type="EVOLUTION", request_id=r.id,
                       estimation_number="EST-2026-001", wizard_inputs_json='{"step": 3}',
                       reference_project_ids="[1]", grand_total_hours=515.9)
        s.add(e); s.flush()
        s.add(EstimationTask(estimation_id=e.id, task_name="Exec", task_type="EXECUTION",
                             base_hours=4, calculated_hours=24))
        s.add(TaskTemplateFeature(task_template_id=1, feature_id=1, base_hours_override=3.5))
        s.add(AuditLog(user_id=1, action="LOGIN", details_json='{"ip": "10.0.0.1"}'))
        s.commit()
""")

V4_READBACK = textwrap.dedent("""
    import json
    from apps.accounts.models import User, AuditLog
    from apps.catalog.models import Feature, TaskTemplateFeature
    from apps.estimations.models import Estimation
    from apps.teams.models import TeamMember
    admin = User.objects.get(username="admin")
    est = Estimation.objects.get(estimation_number="EST-2026-001")
    out = {
        "admin_password_ok": admin.check_password("admin"),
        "admin_role": admin.role,
        "theme": admin.theme_preference,
        "skills": TeamMember.objects.get(name="Alice").skills,
        "wizard": est.wizard_inputs,
        "refs": est.reference_project_ids,
        "request": est.request.request_number,
        "tasks": est.tasks.count(),
        "total": est.grand_total_hours,
        "override": TaskTemplateFeature.objects.get(pk=(1, 1)).base_hours_override,
        "audit": AuditLog.objects.get(action="LOGIN").details,
        "features": Feature.objects.count(),
    }
    admin.set_password("changed-in-v4"); admin.save()
    print("JSON=" + json.dumps(out))
""")

V3_VERIFY = textwrap.dedent("""
    from sqlalchemy.orm import Session
    from src.database.migrations import init_database, get_engine
    from src.auth.models import User
    from src.auth.service import AuthService
    init_database()
    with Session(get_engine()) as s:
        u = s.query(User).filter_by(username="admin").one()
        assert AuthService.verify_password("changed-in-v4", u.password_hash)
    print("v3-ok")
""")


def run(args, cwd, db_path, check=True):
    env = {**os.environ, "DB_PATH": str(db_path), "DJANGO_SETTINGS_MODULE": "presto.settings",
           "DJANGO_ALLOW_INSECURE_KEY": "1", "PYTHONPATH": str(cwd)}
    env.pop("DB_URL", None)
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    if check and proc.returncode != 0:
        raise AssertionError(f"{args} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


def manage(*args, db_path, check=True):
    return run([sys.executable, "manage.py", *args], V4_DIR, db_path, check)


@pytest.fixture
def v3_db(tmp_path):
    db = tmp_path / "v3.db"
    run([sys.executable, "-c", V3_SAMPLE_DATA], V3_DIR, db)
    return db


def test_adopt_v3_database_with_fake_initial(v3_db):
    assert "v3 schema 24" in manage("check_v3_compat", "--before-migrate", db_path=v3_db).stdout
    manage("migrate", "--fake-initial", db_path=v3_db)
    assert "compatible" in manage("check_v3_compat", db_path=v3_db).stdout

    out = manage("shell", "-c", V4_READBACK, db_path=v3_db).stdout
    data = json.loads(out.split("JSON=", 1)[1])
    assert data == {
        "admin_password_ok": True, "admin_role": "ADMIN", "theme": None,
        "skills": ["Automation"], "wizard": {"step": 3}, "refs": [1],
        "request": "REQ_26/0001", "tasks": 1, "total": 515.9, "override": 3.5,
        "audit": {"ip": "10.0.0.1"}, "features": 10,
    }
    # v3 still starts on the adopted DB and verifies a password set by v4.
    assert "v3-ok" in run([sys.executable, "-c", V3_VERIFY], V3_DIR, v3_db).stdout


def test_check_rejects_outdated_v3_schema(v3_db):
    with sqlite3.connect(v3_db) as c:
        c.execute("UPDATE configuration SET value='20' WHERE key='schema_version'")
    proc = manage("check_v3_compat", "--before-migrate", db_path=v3_db, check=False)
    assert proc.returncode != 0 and "start v3.4 once" in proc.stderr


def _v3_columns(db) -> dict[str, dict[str, bool]]:
    with sqlite3.connect(db) as c:
        tables = [r[0] for r in c.execute(
            "select name from sqlite_master where type='table' and name != 'sqlite_sequence'")]
        return {t: {r[1]: bool(r[3]) or bool(r[5]) for r in c.execute(f"pragma table_info({t})")}
                for t in tables}


@pytest.mark.django_db
def test_fresh_v4_schema_matches_v3(v3_db):
    """Same tables, same columns and same NOT NULL rules as a fresh v3 schema."""
    v3 = _v3_columns(v3_db)
    v4: dict[str, dict[str, bool]] = {}
    with connection.cursor() as cursor:
        for table in v3:
            desc = connection.introspection.get_table_description(cursor, table)
            v4[table] = {c.name: not c.null_ok for c in desc}
    v4["users"].pop("theme_preference")  # v4-only column (decision D6)
    for table in v3:
        assert set(v4[table]) == set(v3[table]), table
        mismatched = {c for c in v3[table] if v3[table][c] != v4[table][c] and c != "id"}
        assert not mismatched, f"{table}: NOT NULL differs for {sorted(mismatched)}"


@pytest.mark.django_db
def test_seed_matches_fresh_v3_install(tmp_path):
    """Every column of every seeded row equals a fresh v3 install (ids and timestamps aside)."""
    fresh = tmp_path / "fresh.db"
    run([sys.executable, "-c", "from src.database.migrations import init_database; init_database()"],
        V3_DIR, fresh)
    bootstrap.load_seed_data()
    bootstrap.ensure_defaults()
    skip = {"id", "created_at", "updated_at", "password_hash", "last_login_at"}
    with sqlite3.connect(fresh) as c, connection.cursor() as cursor:
        tables = [r[0] for r in c.execute(
            "select name from sqlite_master where type='table' and name != 'sqlite_sequence' order by name")]
        for table in tables:
            cols = [r[1] for r in c.execute(f"pragma table_info({table})") if r[1] not in skip]
            where = " where key != 'schema_version'" if table == "configuration" else ""
            order = "key" if table == "configuration" else "rowid"
            sql = f"select {', '.join(cols)} from {table}{where} order by {order}"
            cursor.execute(sql)
            v4_rows = [tuple(r) for r in cursor.fetchall()]
            v3_rows = [tuple(r) for r in c.execute(sql)]
            assert v4_rows == v3_rows, table
