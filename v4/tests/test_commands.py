import pytest
from django.core.management import call_command

from apps.accounts.models import Role, User
from apps.catalog import models as catalog
from apps.catalog.models import DutType, Feature, TaskTemplate
from apps.core.models import Configuration

pytestmark = pytest.mark.django_db


def test_seed_creates_v3_first_run_data_and_is_idempotent():
    call_command("seed")
    call_command("seed")
    assert Feature.objects.count() == 10
    assert TaskTemplate.objects.count() == 9
    assert DutType.objects.count() == 5
    assert catalog.TestProfile.objects.count() == 4
    assert Configuration.objects.count() == 62
    admin = User.objects.get()
    assert admin.username == "admin" and admin.role == Role.ADMIN and admin.check_password("admin")


def test_ensure_defaults_skips_admin_when_users_exist():
    User.objects.create_user("someone", "x")
    call_command("ensure_defaults")
    assert not User.objects.filter(username="admin").exists()


def test_create_admin_creates_then_resets():
    call_command("create_admin", "--username", "boss", "--password", "one")
    user = User.objects.get(username="boss")
    user.role, user.is_active = Role.VIEWER, False
    user.save()
    call_command("create_admin", "--username", "boss", "--password", "two")
    user.refresh_from_db()
    assert user.role == Role.ADMIN and user.is_active and user.check_password("two")


def test_check_v3_compat_passes_on_migrated_db(capsys):
    call_command("seed")
    call_command("check_v3_compat")
    assert "compatible" in capsys.readouterr().out


def test_healthcheck(client):
    resp = client.get("/api/healthcheck")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
