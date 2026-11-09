"""Estimation lifecycle: calculation, create, edit, revise, status workflow, calibration.

Port of the estimation endpoints' logic in v3 ``api/routes.py``. Engine inputs
and outputs are built exactly as in v3 so the numbers are identical. Services
raise ``ServiceError`` (status code + v3 message); the API layer turns it into
an HTTP error.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from django.db import connection, transaction
from django.db.models import Q

from apps.accounts.audit import log_action
from apps.accounts.models import User
from apps.catalog.models import Feature, RiskItem, TaskTemplate, TaskTemplateFeature
from apps.core import config
from apps.core.numbering import generate_number
from apps.inbox.models import Request
from engine.calculator import EstimationInput, TaskInput, calculate_estimation
from engine.calculator import PRFixInput as CalcPRFixInput
from engine.calibration import HistoricalDataPoint, calibrate
from engine.feasibility import assess_risks

from . import calc
from .models import (
    Estimation,
    EstimationRisk,
    EstimationTask,
    EstimationTeamAllocation,
    EstimationVersionSnapshot,
    HistoricalProject,
)

logger = logging.getLogger(__name__)

REINIT_TOKEN = "REINITIALIZE"

VALID_TRANSITIONS: dict[str, list[str]] = {
    "DRAFT": ["FINAL", "REVISED"],
    "FINAL": ["APPROVED", "REVISED"],
    "APPROVED": ["REVISED"],
    "REVISED": ["DRAFT"],
}


from apps.core.errors import ServiceError  # noqa: E402,F401  (re-exported)


def get_estimation(estimation_id: int) -> Estimation:
    est = Estimation.objects.filter(pk=estimation_id).first()
    if est is None:
        raise ServiceError(404, "Estimation not found")
    return est


def _is_author_or_admin(estimation: Estimation, user: User) -> bool:
    return user.role == "ADMIN" or estimation.creator_id is None or estimation.creator_id == user.id


# ── Calculation inputs ───────────────────────────────────


def _calc_settings() -> dict[str, Any]:
    return {
        "leader_ratio": config.get_float("leader_effort_ratio", 0.5),
        "study_hours": config.get_float("new_feature_study_hours", 16.0),
        "hours_per_day": config.get_float("working_hours_per_day", 7.0),
        "buffer_pct": config.get_float("buffer_percentage", 10),
        "pr_scales_profile": config.get_str("pr_scales_with_profile", "false").lower() == "true",
    }


def _release_settings() -> tuple[float, list[str]]:
    factor = config.get_float("release_effort_factor", 0.5)
    raw = config.get_str("release_effort_task_types", "EXECUTION")
    return factor, [t.strip() for t in raw.split(",") if t.strip()]


def resolve_features_and_templates(feature_ids: list[int]) -> tuple[list[Feature], list[TaskTemplate]]:
    """Selected features, plus templates linked to any of them or to no feature at all."""
    linked = TaskTemplateFeature.objects.values("task_template_id")
    if feature_ids:
        features = list(Feature.objects.filter(id__in=feature_ids))
        selected_links = TaskTemplateFeature.objects.filter(feature_id__in=feature_ids)
        cond = Q(id__in=selected_links.values("task_template_id")) | ~Q(id__in=linked)
    else:
        features = []
        cond = ~Q(id__in=linked)
    templates = list(TaskTemplate.objects.filter(cond).order_by("id"))
    return features, templates


def expand_templates_to_tasks(
    templates: list[TaskTemplate],
    features: list[Feature],
    selected_feature_ids: list[int],
    new_feature_ids: list[int],
    skip_pr_fix: bool = False,
) -> list[TaskInput]:
    """Expand task templates into engine tasks (v3 ``_expand_templates_to_tasks``).

    Global templates (no linked features) produce one task; feature-linked
    templates produce one task per selected linked feature, using the
    per-feature hour override when set. Selected features without any
    template-driven task contribute their own baseline effort.
    """
    task_inputs: list[TaskInput] = []
    selected_set = set(selected_feature_ids)
    feature_map = {f.id: f for f in features}

    links: dict[int, list[int]] = {}
    override_map: dict[tuple[int, int], float] = {}
    tmpl_ids = [t.id for t in templates]
    if tmpl_ids:
        rows = (
            TaskTemplateFeature.objects.filter(task_template_id__in=tmpl_ids)
            .order_by("task_template_id", "feature_id")
            .values_list("task_template_id", "feature_id", "base_hours_override")
        )
        for tid, fid, override in rows:
            links.setdefault(tid, []).append(fid)
            if override is not None:
                override_map[(tid, fid)] = override

    features_with_task: set[int] = set()
    for tmpl in templates:
        if skip_pr_fix and tmpl.is_pr_fix:
            continue
        linked_fids = links.get(tmpl.id, [])
        if not linked_fids:
            task_inputs.append(TaskInput(
                name=tmpl.name,
                task_type=tmpl.task_type,
                base_effort_hours=tmpl.base_effort_hours,
                scales_with_dut=tmpl.scales_with_dut,
                scales_with_profile=tmpl.scales_with_profile,
                complexity_weight=1.0,
                is_new_feature_study=False,
                is_parallelizable=tmpl.is_parallelizable,
                template_id=tmpl.id,
            ))
            continue
        for fid in linked_fids:
            if fid not in selected_set:
                continue
            feat = feature_map.get(fid)
            if not feat:
                continue
            task_inputs.append(TaskInput(
                name=tmpl.name,
                task_type=tmpl.task_type,
                base_effort_hours=override_map.get((tmpl.id, fid), tmpl.base_effort_hours),
                scales_with_dut=tmpl.scales_with_dut,
                scales_with_profile=tmpl.scales_with_profile,
                complexity_weight=feat.complexity_weight,
                is_new_feature_study=fid in new_feature_ids,
                is_parallelizable=tmpl.is_parallelizable,
                template_id=tmpl.id,
                feature_id=fid,
                feature_name=feat.name,
            ))
            features_with_task.add(fid)

    for fid in selected_feature_ids:
        if fid in features_with_task:
            continue
        feat = feature_map.get(fid)
        base_hrs = (feat.base_effort_hours or 0) if feat else 0
        if feat and base_hrs > 0:
            task_inputs.append(TaskInput(
                name=feat.name,
                task_type="EXECUTION",
                base_effort_hours=base_hrs,
                scales_with_dut=False,
                scales_with_profile=False,
                complexity_weight=feat.complexity_weight,
                is_new_feature_study=False,
                is_parallelizable=False,
                feature_id=fid,
                feature_name=feat.name,
            ))
    return task_inputs


def _study_hours_list(new_feature_ids: list[int], features: list[Feature], default: float) -> list[float]:
    out: list[float] = []
    for fid in new_feature_ids:
        feat = next((f for f in features if f.id == fid), None)
        out.append(feat.study_effort_hours if feat and feat.study_effort_hours is not None else default)
    return out


def _pr_counts(pr_fixes) -> tuple[int, int, int]:
    return pr_fixes.simple, pr_fixes.medium, pr_fixes.complex_


def _risk_item_messages(items) -> list[str]:
    msgs = []
    for ri in items:
        label = f"[{ri.category}] " if ri.category else ""
        level = ""
        if ri.likelihood or ri.impact:
            level = f" (Likelihood: {ri.likelihood or '?'}, Impact: {ri.impact or '?'})"
        msgs.append(f"{label}{ri.name}{level}")
    return msgs


def _task_rows(result) -> list[dict]:
    return [
        {
            "name": t.name,
            "task_type": t.task_type,
            "base_hours": t.base_hours,
            "calculated_hours": t.calculated_hours,
            "formula": t.formula,
            "feature_name": t.feature_name,
        }
        for t in result.tasks
    ]


class _Run:
    """One engine run from wizard inputs (shared by preview, create, revise and draft edit)."""

    def __init__(self, data, feature_ids: list[int], new_feature_ids: list[int],
                 pr_details: list[dict] | None) -> None:
        s = _calc_settings()
        self.settings = s
        self.features, self.templates = resolve_features_and_templates(feature_ids)
        simple, medium, complex_ = _pr_counts(data.pr_fixes)
        self.pr_total = simple + medium + complex_
        tasks = expand_templates_to_tasks(
            self.templates, self.features, feature_ids, new_feature_ids,
            skip_pr_fix=(self.pr_total == 0),
        )
        self.dut_count = len(data.dut_ids) if data.dut_ids else 1
        self.profile_count = len(data.profile_ids) if data.profile_ids else 1
        self.combinations = (
            len(data.dut_profile_matrix) if data.dut_profile_matrix else self.dut_count * self.profile_count
        )
        self.new_feature_count = len(new_feature_ids)
        release_factor, release_types = _release_settings()
        pr_cfg = calc.get_pr_config()
        self.pr_details = pr_details or []
        self.input = EstimationInput(
            project_type=data.project_type,
            tasks=tasks,
            dut_count=self.dut_count,
            profile_count=self.profile_count,
            combination_count=self.combinations,
            pr_fixes=CalcPRFixInput(simple=simple, medium=medium, complex=complex_),
            new_feature_count=self.new_feature_count,
            feature_study_hours_list=_study_hours_list(new_feature_ids, self.features, s["study_hours"]),
            team_size=data.team_size,
            has_leader=data.has_leader,
            working_days=data.working_days,
            leader_effort_ratio=s["leader_ratio"],
            new_feature_study_hours=s["study_hours"],
            working_hours_per_day=s["hours_per_day"],
            buffer_percentage=s["buffer_pct"],
            pr_scales_with_profile=s["pr_scales_profile"],
            expected_releases=data.expected_releases,
            release_effort_factor=release_factor,
            release_effort_task_types=release_types,
            pr_complexity_hours=pr_cfg["complexity_hours"],
            pr_no_test_hours=pr_cfg["no_test_hours"],
            pr_no_test_count=calc.count_no_test_prs(self.pr_details) if pr_details is not None else 0,
        )
        self.result = calculate_estimation(self.input)
        self.documentation_hours, _ = calc.calc_doc_hours_and_deliverables(
            data.document_type_ids, data.document_counts, {t.id for t in self.templates},
        )
        hpd = s["hours_per_day"]
        self.grand_total = self.result.grand_total_hours + self.documentation_hours
        self.grand_total_days = self.grand_total / hpd if hpd else self.result.grand_total_days


def calculate_preview(data) -> dict[str, Any]:
    """POST /estimations/calculate: run the engine on wizard inputs, persist nothing."""
    feature_ids = data.resolved_feature_ids
    new_feature_ids = data.resolved_new_feature_ids
    run = _Run(data, feature_ids, new_feature_ids, pr_details=None)
    result = run.result
    risks = assess_risks(
        total_features=len(feature_ids),
        new_feature_count=run.new_feature_count,
        reference_project_count=len(data.reference_project_ids or []),
        delivery_date=data.expected_delivery or data.delivery_date,
        dut_profile_combinations=run.combinations,
    )
    messages = list(risks.messages)
    if data.risk_item_ids:
        messages += _risk_item_messages(RiskItem.objects.filter(id__in=data.risk_item_ids).order_by("id"))
    return {
        "tasks": _task_rows(result),
        "total_tester_hours": result.total_tester_hours,
        "total_leader_hours": result.total_leader_hours,
        "pr_fix_hours": result.pr_fix_hours,
        "pr_no_test_hours": result.pr_no_test_total_hours,
        "study_hours": result.study_hours,
        "release_extra_hours": result.release_extra_hours,
        "documentation_hours": run.documentation_hours,
        "buffer_hours": result.buffer_hours,
        "grand_total_hours": run.grand_total,
        "grand_total_days": run.grand_total_days,
        "feasibility_status": result.feasibility_status,
        "capacity_hours": result.capacity_hours,
        "utilization_pct": result.utilization_pct,
        "elapsed_hours": result.elapsed_hours,
        "elapsed_days": result.elapsed_days,
        "elapsed_weeks": result.elapsed_weeks,
        "risk_flags": [f.value for f in risks.flags],
        "risk_messages": messages,
    }


def _wizard_inputs(data, pr_details: list[dict]) -> dict[str, Any]:
    """Wizard snapshot stored on the estimation (key order as in v3)."""
    simple, medium, complex_ = _pr_counts(data.pr_fixes)
    return {
        "feature_ids": data.feature_ids,
        "new_feature_ids": data.new_feature_ids,
        "reference_project_ids": data.reference_project_ids,
        "dut_ids": data.dut_ids,
        "profile_ids": data.profile_ids,
        "dut_profile_matrix": data.dut_profile_matrix,
        "pr_fixes": {"simple": simple, "medium": medium, "complex": complex_},
        "pr_details": pr_details,
        "team_size": data.team_size,
        "has_leader": data.has_leader,
        "working_days": data.working_days,
        "start_date": str(data.start_date) if data.start_date else None,
        "expected_releases": data.expected_releases,
        "document_type_ids": data.document_type_ids,
        "document_counts": data.document_counts,
        "project_reference": data.project_reference,
        "testing_start_date": data.testing_start_date,
        "product_type_filter": data.product_type_filter,
        "applied_presets": data.applied_presets or [],
    }


def _completion_date(testing_start_date: str | None, elapsed_days: float) -> date | None:
    if testing_start_date and elapsed_days > 0:
        try:
            return calc.compute_completion_date(date.fromisoformat(testing_start_date), elapsed_days)
        except (ValueError, TypeError):
            return None
    return None


def _apply_run(estimation: Estimation, data, run: _Run) -> None:
    """Copy computed totals and wizard-level fields onto the estimation (not saved)."""
    r = run.result
    estimation.project_name = data.project_name
    estimation.project_type = data.project_type
    estimation.reference_project_ids = data.reference_project_ids
    estimation.dut_count = run.dut_count
    estimation.profile_count = run.profile_count
    estimation.dut_profile_combinations = run.combinations
    estimation.pr_fix_count = run.pr_total
    estimation.start_date = data.start_date
    estimation.expected_delivery = data.expected_delivery
    estimation.total_tester_hours = r.total_tester_hours
    estimation.total_leader_hours = r.total_leader_hours
    estimation.pr_fix_hours = r.pr_fix_hours
    estimation.pr_no_test_hours = r.pr_no_test_total_hours
    estimation.study_hours = r.study_hours
    estimation.release_extra_hours = r.release_extra_hours
    estimation.documentation_hours = run.documentation_hours
    estimation.buffer_hours = r.buffer_hours
    estimation.grand_total_hours = run.grand_total
    estimation.grand_total_days = run.grand_total_days
    estimation.elapsed_hours = r.elapsed_hours
    estimation.elapsed_days = r.elapsed_days
    estimation.elapsed_weeks = r.elapsed_weeks
    estimation.estimated_completion_date = _completion_date(data.testing_start_date, r.elapsed_days)
    estimation.feasibility_status = r.feasibility_status
    estimation.expected_releases = data.expected_releases
    estimation.project_goals = data.project_goals
    estimation.target_customer = data.target_customer
    estimation.project_reference = data.project_reference
    estimation.team_id = data.team_id
    estimation.wizard_inputs = _wizard_inputs(data, run.pr_details)


def _write_children(estimation: Estimation, data, run: _Run) -> None:
    leader_ratio = run.settings["leader_ratio"]
    EstimationTask.objects.bulk_create([
        EstimationTask(
            estimation=estimation,
            task_template_id=task.template_id,
            task_name=task.name,
            task_type=task.task_type,
            base_hours=task.base_hours,
            calculated_hours=task.calculated_hours,
            assigned_testers=data.task_assigned_testers.get(task.name, 1),
            has_leader_support=data.has_leader,
            leader_hours=task.calculated_hours * leader_ratio if data.has_leader else 0,
            is_new_feature_study=task.is_new_feature_study,
            formula=task.formula,
            feature_id=task.feature_id,
            feature_name=task.feature_name,
        )
        for task in run.result.tasks
    ])
    EstimationTeamAllocation.objects.bulk_create([
        EstimationTeamAllocation(
            estimation=estimation, team_member_id=a.team_member_id,
            role=a.role, allocated_hours=a.allocated_hours,
        )
        for a in data.team_allocations
    ])
    EstimationRisk.objects.bulk_create([
        EstimationRisk(estimation=estimation, risk_item_id=rid) for rid in data.risk_item_ids
    ])


def _pr_details_raw(data) -> list[dict]:
    return [d.model_dump() for d in data.pr_details] if data.pr_details else []


@transaction.atomic
def create_estimation(data, user: User) -> Estimation:
    """POST /estimations: resolve inputs, run the calculation, save the result as DRAFT v1."""
    pr_details = _pr_details_raw(data)
    run = _Run(data, data.feature_ids, data.new_feature_ids, pr_details=pr_details)
    estimation = Estimation(
        request_id=data.request_id,
        estimation_number=generate_number("estimation_number_prefix", Estimation, "EST"),
        status="DRAFT",
        created_by=data.created_by,
        creator=user,
        assigned_to=user,
        version=1,
    )
    _apply_run(estimation, data, run)
    estimation.save()
    _write_children(estimation, data, run)
    if data.request_id:
        Request.objects.filter(pk=data.request_id).update(status="ESTIMATED")
    return estimation


def apply_estimation_inputs(estimation: Estimation, data) -> None:
    """Recalculate from wizard inputs and overwrite totals, wizard snapshot and child rows.

    Shared by revise and draft edit; does not touch status, version or approval.
    """
    pr_details = _pr_details_raw(data)
    run = _Run(data, data.feature_ids, data.new_feature_ids, pr_details=pr_details)
    _apply_run(estimation, data, run)
    estimation.save()
    estimation.tasks.all().delete()
    estimation.team_allocations.all().delete()
    estimation.risks.all().delete()
    _write_children(estimation, data, run)


def recalculate(estimation: Estimation) -> dict[str, Any]:
    """POST /estimations/{id}/calculate: re-run the engine on the saved task rows."""
    s = _calc_settings()
    tasks = list(estimation.tasks.order_by("id"))
    task_inputs = [
        TaskInput(
            name=t.task_name,
            task_type=t.task_type,
            base_effort_hours=t.base_hours,
            scales_with_dut=False,
            scales_with_profile=False,
            complexity_weight=1.0,
            is_new_feature_study=t.is_new_feature_study,
            template_id=t.task_template_id,
            feature_id=t.feature_id,
            feature_name=t.feature_name,
        )
        for t in tasks
    ]
    new_feature_count = sum(1 for t in tasks if t.is_new_feature_study)
    result = calculate_estimation(EstimationInput(
        project_type=estimation.project_type,
        tasks=task_inputs,
        dut_count=1,
        profile_count=1,
        new_feature_count=new_feature_count,
        team_size=max(1, sum(t.assigned_testers for t in tasks) // max(len(tasks), 1)),
        has_leader=any(t.has_leader_support for t in tasks),
        working_days=20,
        leader_effort_ratio=s["leader_ratio"],
        new_feature_study_hours=s["study_hours"],
        working_hours_per_day=s["hours_per_day"],
        buffer_percentage=s["buffer_pct"],
    ))
    ref_ids = estimation.reference_project_ids or []
    risks = assess_risks(
        total_features=0,
        new_feature_count=new_feature_count,
        reference_project_count=len(ref_ids),
        delivery_date=estimation.expected_delivery,
        dut_profile_combinations=estimation.dut_profile_combinations,
    )
    items = [er.risk_item for er in estimation.risks.select_related("risk_item").order_by("id")]
    return {
        "tasks": _task_rows(result),
        "total_tester_hours": result.total_tester_hours,
        "total_leader_hours": result.total_leader_hours,
        "pr_fix_hours": result.pr_fix_hours,
        "study_hours": result.study_hours,
        "release_extra_hours": result.release_extra_hours,
        "buffer_hours": result.buffer_hours,
        "grand_total_hours": result.grand_total_hours,
        "grand_total_days": result.grand_total_days,
        "feasibility_status": result.feasibility_status,
        "capacity_hours": result.capacity_hours,
        "utilization_pct": result.utilization_pct,
        "elapsed_hours": result.elapsed_hours,
        "elapsed_days": result.elapsed_days,
        "elapsed_weeks": result.elapsed_weeks,
        "risk_flags": [f.value for f in risks.flags],
        "risk_messages": list(risks.messages) + _risk_item_messages([ri for ri in items if ri]),
    }


# ── Detail ───────────────────────────────────────────────


def _synthetic_task(st: dict) -> dict[str, Any]:
    return {
        "id": 0,
        "task_template_id": None,
        "task_name": st["task_name"],
        "task_type": st["task_type"],
        "base_hours": st["base_hours"],
        "calculated_hours": st["calculated_hours"],
        "assigned_testers": 0,
        "has_leader_support": False,
        "leader_hours": 0,
        "is_new_feature_study": False,
        "formula": None,
        "notes": st["notes"],
        "feature_id": None,
        "feature_name": None,
    }


def estimation_detail(estimation: Estimation) -> dict[str, Any]:
    """GET /estimations/{id}: EstimationOut plus document deliverables and synthetic tasks."""
    from apps.api.schemas.estimations import serialize_estimation

    out = serialize_estimation(estimation)
    deliverables = calc.compute_doc_deliverables(estimation)
    out["document_deliverables"] = deliverables
    for st in calc.build_doc_synthetic_tasks(deliverables):
        out["tasks"].append(_synthetic_task(st))
    wizard = estimation.wizard_inputs if isinstance(estimation.wizard_inputs, dict) else {}
    no_test_hours = config.get_float("pr_no_test_hours", 8.0)
    for st in calc.build_pr_no_test_synthetic_tasks(wizard.get("pr_details", []), no_test_hours):
        out["tasks"].append(_synthetic_task(st))
    return out


# ── Simple edits ─────────────────────────────────────────

_UPDATABLE_FIELDS = {
    "project_name", "project_type", "expected_delivery", "project_goals", "target_customer",
    "project_reference",
}


def update_metadata(estimation: Estimation, changes: dict[str, Any]) -> Estimation:
    """PUT /estimations/{id}: set the given metadata fields (v3 ``notes`` has no column)."""
    for key, val in changes.items():
        if key in _UPDATABLE_FIELDS:
            setattr(estimation, key, val)
    estimation.save()
    return estimation


@transaction.atomic
def edit_draft(estimation: Estimation, data, user: User, ip: str | None = None) -> Estimation:
    """PUT /estimations/{id}/draft: recalculate a DRAFT in place (same version, no snapshot)."""
    if estimation.status != "DRAFT":
        raise ServiceError(
            400,
            f"Only DRAFT estimations can be edited in place (current: {estimation.status}). "
            "Move to REVISED to revise a finalized estimation.",
        )
    if not _is_author_or_admin(estimation, user):
        raise ServiceError(403, "Only the estimation's author or an ADMIN can edit this draft")
    apply_estimation_inputs(estimation, data)
    log_action(user, "EDIT_DRAFT_ESTIMATION", "estimation", estimation.id,
               {"version": estimation.version}, ip)
    return estimation


@transaction.atomic
def revise(estimation: Estimation, data, user: User, ip: str | None = None) -> Estimation:
    """PUT /estimations/{id}/revise: snapshot the current version, recalculate, bump version, DRAFT."""
    from .versions import snapshot_estimation

    if estimation.status != "REVISED":
        raise ServiceError(
            400, f"Estimation must be in REVISED status to revise (current: {estimation.status})",
        )
    EstimationVersionSnapshot.objects.create(
        estimation=estimation, version=estimation.version or 1,
        snapshot=snapshot_estimation(estimation),
    )
    apply_estimation_inputs(estimation, data)
    estimation.status = "DRAFT"
    estimation.version = (estimation.version or 1) + 1
    estimation.approved_by = None
    estimation.approved_at = None
    estimation.save()
    log_action(user, "REVISE_ESTIMATION", "estimation", estimation.id,
               {"new_version": estimation.version}, ip)
    return estimation


# ── Status workflow ──────────────────────────────────────


def change_status(estimation: Estimation, target: str, approved_by: str | None, user: User) -> Estimation:
    """POST /estimations/{id}/status: DRAFT -> FINAL -> APPROVED, any -> REVISED -> DRAFT.

    Side effects (notification, external export, Outline export, historical
    project) never block the transition.
    """
    current = estimation.status
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise ServiceError(400, f"Invalid status transition: {current} -> {target}. Allowed: {allowed}")

    estimation.status = target
    if target == "APPROVED":
        estimation.approved_by = approved_by
        estimation.approved_at = datetime.now()
    elif target == "REVISED":
        estimation.approved_by = None
        estimation.approved_at = None
    estimation.save()

    try:
        from apps.notifications.service import NotificationService

        NotificationService().notify_estimation_status_changed(
            estimation.estimation_number or f"EST-{estimation.id}",
            estimation.project_name,
            current,
            target,
            user.display_name,
            estimation.grand_total_hours,
            creator_user_id=estimation.creator_id,
            assigned_user_id=estimation.assigned_to_id,
        )
    except Exception:
        logger.debug("Status-change notification failed", exc_info=True)

    if target in ("FINAL", "APPROVED") and estimation.request_id:
        try_export_estimation(estimation)

    try_outline_auto_export(estimation, target)

    try:
        auto_rule = config.get_str("auto_create_historical_project", "manual")
        trigger_status = {"on_approve": "APPROVED", "on_complete": "FINAL"}.get(auto_rule)
        if trigger_status and target == trigger_status:
            if not HistoricalProject.objects.filter(estimation=estimation).exists():
                HistoricalProject.objects.create(
                    project_name=estimation.project_name,
                    project_type=estimation.project_type,
                    estimated_hours=estimation.grand_total_hours,
                    dut_count=estimation.dut_count,
                    profile_count=estimation.profile_count,
                    pr_count=estimation.pr_fix_count,
                    estimation=estimation,
                )
    except Exception:
        logger.debug("Historical project auto-create failed", exc_info=True)

    return estimation


def try_export_estimation(estimation: Estimation) -> None:
    """Export results back to the originating external system (v3 ``_try_export_estimation``)."""
    req = Request.objects.filter(pk=estimation.request_id).first()
    if not req or not req.external_id or req.request_source == "MANUAL":
        return
    try:
        from apps.integrations.service import sync_export

        sync_export(req.request_source, {
            "external_id": req.external_id,
            "grand_total_hours": estimation.grand_total_hours,
            "feasibility_status": estimation.feasibility_status,
            "estimation_number": estimation.estimation_number or f"EST-{estimation.id}",
            "project_name": estimation.project_name,
            "status": estimation.status,
        })
        logger.info(
            "Auto-exported estimation %s to %s (external_id=%s)",
            estimation.estimation_number or estimation.id, req.request_source, req.external_id,
        )
    except Exception:
        logger.warning(
            "Auto-export failed for estimation %s to %s (external_id=%s)",
            estimation.estimation_number or estimation.id, req.request_source, req.external_id,
            exc_info=True,
        )


def try_outline_auto_export(estimation: Estimation, new_status: str) -> None:
    """Export to Outline if ``new_status`` is listed in ``outline_auto_export_states``."""
    try:
        raw = config.get_str("outline_auto_export_states", "")
        if not raw.strip():
            return
        states = [s.strip().upper() for s in raw.split(",") if s.strip()]
        if new_status.upper() not in states:
            return
        from apps.integrations.service import estimation_to_export_dict, get_adapter

        adapter = get_adapter("OUTLINE")
        if not adapter:
            return
        adapter.export_estimation(estimation_to_export_dict(estimation))
    except Exception:
        logger.debug("Outline auto-export failed", exc_info=True)


# ── Assignment, calibration, archive, reinit, project features ──


def assign(estimation: Estimation, assigned_to_id: int, user: User, ip: str | None = None) -> dict[str, Any]:
    """POST /estimations/{id}/assign: set the assignee and notify them."""
    if not User.objects.filter(pk=assigned_to_id).exists():
        raise ServiceError(404, "Target user not found")
    estimation.assigned_to_id = assigned_to_id
    estimation.save(update_fields=["assigned_to"])
    try:
        from apps.notifications.service import NotificationService

        NotificationService().notify_user_assigned(
            estimation.estimation_number or f"EST-{estimation.id}",
            estimation.project_name,
            assigned_to_id,
            user.display_name,
        )
    except Exception:
        logger.debug("Assignment notification failed", exc_info=True)
    log_action(user, "ASSIGN", "estimation", estimation.id, {"assigned_to_id": assigned_to_id}, ip)
    return {"status": "ok", "assigned_to_id": assigned_to_id}


def calibrate_estimation(estimation: Estimation) -> dict[str, Any]:
    """POST /estimations/{id}/calibrate: compare against linked historical projects."""
    ref_ids = estimation.reference_project_ids or []
    if not ref_ids:
        raise ServiceError(400, "No reference projects linked to this estimation.")
    refs = list(HistoricalProject.objects.filter(id__in=ref_ids).order_by("id"))
    if not refs:
        raise ServiceError(400, "No reference projects found.")
    points = [
        HistoricalDataPoint(
            project_name=r.project_name,
            estimated_hours=r.estimated_hours or 0,
            actual_hours=r.actual_hours or 0,
            feature_ids=r.features if isinstance(r.features, list) else [],
        )
        for r in refs
        if r.estimated_hours and r.actual_hours
    ]
    if not points:
        raise ServiceError(400, "Reference projects lack estimated/actual hours data.")
    result = calibrate(points, current_feature_ids=[])
    return {
        "accuracy_ratio": round(result.accuracy_ratio, 3),
        "adjustment_factor": round(result.suggested_adjustment, 3),
        "suggestion": result.message,
        "reference_projects": [
            {
                "project_name": r.project_name,
                "estimated_hours": r.estimated_hours,
                "actual_hours": r.actual_hours,
                "accuracy_ratio": round(r.actual_hours / r.estimated_hours, 2) if r.estimated_hours else None,
            }
            for r in refs
        ],
        "adjusted_grand_total": round(estimation.grand_total_hours * result.suggested_adjustment, 1),
    }


def archive_to_history(estimation: Estimation) -> HistoricalProject:
    """POST /estimations/{id}/archive: turn an APPROVED estimation into a historical project."""
    if estimation.status != "APPROVED":
        raise ServiceError(400, f"Only APPROVED estimations can be archived (current: {estimation.status})")
    if HistoricalProject.objects.filter(estimation=estimation).exists():
        raise ServiceError(400, "This estimation has already been archived")
    wizard = estimation.wizard_inputs if isinstance(estimation.wizard_inputs, dict) else {}
    return HistoricalProject.objects.create(
        project_name=estimation.project_name,
        project_type=estimation.project_type,
        estimated_hours=estimation.grand_total_hours,
        dut_count=estimation.dut_count,
        profile_count=estimation.profile_count,
        pr_count=estimation.pr_fix_count,
        features=wizard.get("feature_ids", []),
        notes=f"Archived from estimation {estimation.estimation_number or estimation.id}",
        estimation=estimation,
    )


def _reset_id_counter() -> None:
    """Restart the estimations id sequence (SQLite, MySQL, PostgreSQL)."""
    statements = {
        "sqlite": "DELETE FROM sqlite_sequence WHERE name='estimations'",
        "mysql": "ALTER TABLE estimations AUTO_INCREMENT = 1",
        "postgresql": "ALTER SEQUENCE estimations_id_seq RESTART WITH 1",
    }
    sql = statements.get(connection.vendor)
    if not sql:
        return
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql)
    except Exception:
        logger.debug("Could not reset the estimations id counter", exc_info=True)


def reinit_estimations(confirm: str | None, user: User, ip: str | None = None) -> dict[str, Any]:
    """POST /estimations/reinit: delete every estimation and reset the id counter."""
    if (confirm or "").strip().upper() != REINIT_TOKEN:
        raise ServiceError(400, f"Confirmation token mismatch. Type '{REINIT_TOKEN}' to proceed.")
    with transaction.atomic():
        deleted = Estimation.objects.count()
        Estimation.objects.all().delete()
    _reset_id_counter()
    log_action(user, "ESTIMATION_REINIT", "estimation", None, {"deleted": deleted}, ip)
    return {
        "deleted": deleted,
        "message": f"All estimations cleared ({deleted} removed), ID counter reset to 1.",
    }


def create_project_feature(estimation: Estimation, fields: dict[str, Any], user: User) -> Feature:
    """POST /estimations/{id}/features: a feature owned by one DRAFT estimation."""
    if estimation.status != "DRAFT":
        raise ServiceError(
            400,
            f"Project features can only be added to a DRAFT estimation (current: {estimation.status})",
        )
    if user.role != "ADMIN" and estimation.creator_id is not None and estimation.creator_id != user.id:
        raise ServiceError(403, "Only the estimation's author or an ADMIN can add project features")
    name, category = fields["name"], fields.get("category")
    if Feature.objects.filter(name=name, category=category, owner_estimation=estimation).exists():
        cat_label = category or "(no category)"
        raise ServiceError(409, f"This project already has a feature '{name}' in category '{cat_label}'.")
    return Feature.objects.create(**fields, is_global=False, owner_estimation=estimation)
