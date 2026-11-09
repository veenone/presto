"""Unit tests for apps.estimations.services / versions (no HTTP)."""

from __future__ import annotations

import pytest

from apps.catalog.models import Feature, TaskTemplate, TaskTemplateFeature
from apps.estimations import services, versions
from apps.estimations.models import Estimation

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog():
    f1 = Feature.objects.create(name="F1", complexity_weight=2.0)
    f2 = Feature.objects.create(name="F2")
    custom = Feature.objects.create(name="Custom", base_effort_hours=5, is_global=False)
    glob = TaskTemplate.objects.create(name="Global", task_type="SETUP", base_effort_hours=4)
    linked = TaskTemplate.objects.create(name="Linked", task_type="EXECUTION", base_effort_hours=6)
    pr = TaskTemplate.objects.create(name="PR", task_type="EXECUTION", base_effort_hours=3, is_pr_fix=True)
    other = TaskTemplate.objects.create(name="Other", task_type="EXECUTION", base_effort_hours=1)
    TaskTemplateFeature.objects.create(task_template=linked, feature=f1)
    TaskTemplateFeature.objects.create(task_template=linked, feature=f2, base_hours_override=9)
    other_feature = Feature.objects.create(name="Unselected")
    TaskTemplateFeature.objects.create(task_template=other, feature=other_feature)
    return {"f1": f1, "f2": f2, "custom": custom, "glob": glob, "linked": linked, "pr": pr}


def test_resolve_templates_selects_linked_and_global(catalog):
    _, templates = services.resolve_features_and_templates([catalog["f1"].id])
    assert [t.name for t in templates] == ["Global", "Linked", "PR"]
    _, templates = services.resolve_features_and_templates([])
    assert [t.name for t in templates] == ["Global", "PR"]


def test_expand_templates(catalog):
    ids = [catalog["f1"].id, catalog["f2"].id, catalog["custom"].id]
    features, templates = services.resolve_features_and_templates(ids)
    tasks = services.expand_templates_to_tasks(templates, features, ids, [catalog["f2"].id], skip_pr_fix=True)
    rows = [(t.name, t.feature_name, t.base_effort_hours, t.complexity_weight, t.is_new_feature_study)
            for t in tasks]
    assert rows == [
        ("Global", None, 4, 1.0, False),
        ("Linked", "F1", 6, 2.0, False),
        ("Linked", "F2", 9, 1.0, True),
        ("Custom", "Custom", 5, 1.0, False),
    ]
    with_pr = services.expand_templates_to_tasks(templates, features, ids, [], skip_pr_fix=False)
    assert "PR" in [t.name for t in with_pr]


@pytest.mark.parametrize("current,target,ok", [
    ("DRAFT", "FINAL", True), ("DRAFT", "REVISED", True), ("DRAFT", "APPROVED", False),
    ("FINAL", "APPROVED", True), ("FINAL", "DRAFT", False), ("APPROVED", "REVISED", True),
    ("APPROVED", "DRAFT", False), ("REVISED", "DRAFT", True), ("REVISED", "FINAL", False),
    ("UNKNOWN", "DRAFT", False),
])
def test_transition_table(current, target, ok, admin_user):
    est = Estimation.objects.create(project_name="P", project_type="NEW", status=current)
    if ok:
        assert services.change_status(est, target, None, admin_user).status == target
    else:
        with pytest.raises(services.ServiceError) as exc:
            services.change_status(est, target, None, admin_user)
        assert exc.value.status_code == 400


def test_snapshot_contents(admin_user):
    est = Estimation.objects.create(project_name="P", project_type="NEW", wizard_inputs={"team_size": 2})
    est.tasks.create(task_name="T", task_type="EXECUTION", base_hours=1, calculated_hours=2)
    snap = versions.snapshot_estimation(est)
    assert snap["tasks"] == [{"task_name": "T", "task_type": "EXECUTION", "base_hours": 1.0,
                              "calculated_hours": 2.0}]
    assert snap["wizard_inputs"] == {"team_size": 2}
    assert snap["team_allocations"] == [] and snap["risks"] == []


def test_reinit_resets_id_counter(admin_user):
    Estimation.objects.create(project_name="A", project_type="NEW")
    Estimation.objects.create(project_name="B", project_type="NEW")
    assert services.reinit_estimations("REINITIALIZE", admin_user)["deleted"] == 2
    assert Estimation.objects.create(project_name="C", project_type="NEW").id == 1
