from datetime import date

import bcrypt
import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import AuditLog, Role, User
from apps.catalog.models import Feature, TaskTemplate, TaskTemplateFeature
from apps.estimations.models import Estimation, EstimationTask
from apps.inbox.models import Request
from apps.teams.models import Team, TeamMember

pytestmark = pytest.mark.django_db


def make_request(**kw):
    defaults = {
        "request_number": "REQ_26/0001", "title": "T", "requester_name": "PO",
        "received_date": date(2026, 10, 20),
    }
    return Request.objects.create(**{**defaults, **kw})


class TestUser:
    def test_password_uses_v3_raw_bcrypt_format(self):
        user = User.objects.create_user("alice", "pw123")
        assert user.password.startswith("$2")
        assert bcrypt.checkpw(b"pw123", user.password.encode())  # what v3 does
        assert user.check_password("pw123")
        assert not user.check_password("wrong")

    def test_accepts_hash_written_by_v3(self):
        v3_hash = bcrypt.hashpw(b"legacy", bcrypt.gensalt()).decode()
        user = User.objects.create(username="bob", display_name="Bob", password=v3_hash)
        assert user.check_password("legacy")

    def test_external_user_without_password_cannot_log_in(self):
        user = User.objects.create_user("carol", None, auth_provider="ldap")
        assert user.password is None
        assert not user.check_password("anything")
        assert not user.check_password("")

    def test_defaults_and_roles(self):
        user = User.objects.create_user("dave", "x")
        assert user.role == Role.VIEWER and user.is_active and user.auth_provider == "local"
        assert user.display_name == "dave"
        assert user.theme_preference is None
        assert user.created_at is not None
        admin = User.objects.create_superuser("root", "x")
        assert admin.is_admin

    def test_username_is_unique(self):
        User.objects.create_user("erin", "x")
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user("erin", "y")

    def test_audit_details_json_roundtrip(self):
        user = User.objects.create_user("frank", "x")
        AuditLog.objects.create(user=user, action="LOGIN", details={"ip": "10.0.0.1"})
        assert AuditLog.objects.get().details == {"ip": "10.0.0.1"}


class TestCatalog:
    def test_global_feature_name_category_unique(self):
        Feature.objects.create(name="OTA", category="Telecom")
        with pytest.raises(IntegrityError), transaction.atomic():
            Feature.objects.create(name="OTA", category="Telecom")

    def test_project_feature_may_reuse_global_name(self):
        Feature.objects.create(name="OTA", category="Telecom")
        est = Estimation.objects.create(project_name="P", project_type="NEW")
        Feature.objects.create(name="OTA", category="Telecom", is_global=False, owner_estimation=est)
        assert Feature.objects.filter(name="OTA").count() == 2

    def test_template_feature_link_composite_key(self):
        feature = Feature.objects.create(name="SIM Toolkit")
        template = TaskTemplate.objects.create(name="Exec", task_type="EXECUTION", base_effort_hours=4)
        TaskTemplateFeature.objects.create(task_template=template, feature=feature, base_hours_override=3.5)
        link = TaskTemplateFeature.objects.get(pk=(template.pk, feature.pk))
        assert link.base_hours_override == 3.5
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskTemplateFeature.objects.create(task_template=template, feature=feature)

    def test_deleting_estimation_removes_its_project_features(self):
        est = Estimation.objects.create(project_name="P", project_type="NEW")
        Feature.objects.create(name="Custom", is_global=False, owner_estimation=est)
        est.delete()
        assert not Feature.objects.filter(name="Custom").exists()


class TestEstimation:
    def test_json_fields_default_and_roundtrip(self):
        est = Estimation.objects.create(project_name="P", project_type="EVOLUTION")
        assert est.wizard_inputs == {} and est.reference_project_ids == []
        est.wizard_inputs = {"step": 3, "dut_ids": [1, 2]}
        est.save()
        est.refresh_from_db()
        assert est.wizard_inputs["dut_ids"] == [1, 2]
        assert est.status == "DRAFT" and est.feasibility_status == "FEASIBLE" and est.version == 1

    def test_on_delete_rules_match_v3(self):
        req = make_request()
        team = Team.objects.create(name="QA")
        est = Estimation.objects.create(project_name="P", project_type="NEW", request=req, team=team)
        EstimationTask.objects.create(estimation=est, task_name="Exec", task_type="EXECUTION")
        req.delete()
        team.delete()
        est.refresh_from_db()
        assert est.request is None and est.team is None  # SET NULL
        est.delete()
        assert EstimationTask.objects.count() == 0  # CASCADE

    def test_team_member_skills(self):
        member = TeamMember.objects.create(name="Alice", role="TESTER", skills=["Automation"])
        member.refresh_from_db()
        assert member.skills == ["Automation"] and member.available_hours_per_day == 7.0
