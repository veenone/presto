"""Wizard state: one ``EstimationCreate``-shaped dict in the Django session.

Session layout (one key per wizard: ``wizard:new`` or ``wizard:edit:<id>``)::

    {"data": {...EstimationCreate fields...},
     "meta": {"description": str, "draft_id": int | None, "saved_sig": str | None,
              "calc_total": float | None}}

``data`` is exactly what is sent to ``create_estimation`` / ``edit_draft`` /
``revise`` (validated with the API schemas); ``meta`` holds what is not
persisted (the v3 description field, the draft backing a new wizard, the
signature of the last saved payload and the last calculated total).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import ValidationError

from apps.api.schemas.estimations import CalculateInput, EstimationCreate, EstimationRevise
from apps.core import services as core_services
from apps.core.errors import ServiceError
from apps.estimations import calc, services
from apps.estimations.models import Estimation

SESSION_PREFIX = "wizard:"

NEW, DRAFT, REVISION = "new", "draft", "revision"


def default_data(request_id: int | None = None, username: str | None = None) -> dict[str, Any]:
    types = core_services.project_types()
    return {
        "project_name": "",
        "project_type": "EVOLUTION" if "EVOLUTION" in types or not types else types[0],
        "feature_ids": [],
        "new_feature_ids": [],
        "reference_project_ids": [],
        "dut_ids": [],
        "profile_ids": [],
        "dut_profile_matrix": [],
        "pr_fixes": {"simple": 0, "medium": 0, "complex": 0},
        "pr_details": [],
        "team_size": 1,
        "has_leader": False,
        "start_date": None,
        "expected_delivery": None,
        "working_days": 20,
        "team_allocations": [],
        "expected_releases": 1,
        "project_goals": None,
        "target_customer": None,
        "project_reference": None,
        "team_id": None,
        "risk_item_ids": [],
        "document_type_ids": [],
        "document_counts": {},
        "task_assigned_testers": {},
        "testing_start_date": None,
        "product_type_filter": "All",
        "applied_presets": [],
        "request_id": request_id,
        "created_by": username,
    }


def default_meta() -> dict[str, Any]:
    return {"description": "", "draft_id": None, "saved_sig": None, "calc_total": None}


def signature(data: dict[str, Any]) -> str:
    """Stable text of the payload, to skip autosaves when nothing changed."""
    return json.dumps(data, sort_keys=True, default=str)


def to_model(data: dict[str, Any], cls=EstimationRevise):
    """Validate the state with the API schema; schema errors become a 400 ``ServiceError``."""
    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise ServiceError(400, f"Invalid wizard input ({where}): {first.get('msg')}") from exc


class Wizard:
    """The wizard of one request: mode, backing estimation and session state."""

    def __init__(self, request, estimation: Estimation | None = None) -> None:
        self.request = request
        self.estimation = estimation
        if estimation is None:
            self.mode = NEW
            self.key = f"{SESSION_PREFIX}new"
            self.base_url = "/estimation/new"
        else:
            self.mode = DRAFT if estimation.status == "DRAFT" else REVISION
            self.key = f"{SESSION_PREFIX}edit:{estimation.id}"
            self.base_url = f"/estimation/{estimation.id}/edit"
        stored = request.session.get(self.key)
        self.loaded = isinstance(stored, dict) and isinstance(stored.get("data"), dict)
        if self.loaded:
            self.data = {**default_data(), **stored["data"]}
            self.meta = {**default_meta(), **(stored.get("meta") or {})}
        else:
            self.reset()

    # ── lifecycle ────────────────────────────────────────
    def reset(self, request_id: int | None = None, description: str = "") -> None:
        """Fresh state: defaults for a new estimation, the saved inputs for an edit."""
        self.meta = default_meta()
        if self.estimation is None:
            self.data = default_data(request_id, self.request.user.username)
            self.meta["description"] = description
        else:
            self.data = {**default_data(), **services.wizard_payload(self.estimation)}
            self.meta["saved_sig"] = signature(self.data)
            self.meta["calc_total"] = self.estimation.grand_total_hours
        self.loaded = True

    def save(self) -> None:
        self.request.session[self.key] = {"data": self.data, "meta": self.meta}
        self.request.session.modified = True

    def clear(self) -> None:
        self.request.session.pop(self.key, None)

    # ── derived ──────────────────────────────────────────
    @property
    def autosave(self) -> bool:
        """Autosave runs for new estimations and drafts edited in place, never for revisions."""
        return self.mode in (NEW, DRAFT)

    @property
    def draft_id(self) -> int | None:
        """The estimation that owns project features (the autosaved draft for a new wizard)."""
        return self.estimation.id if self.estimation is not None else self.meta.get("draft_id")

    def url(self, action: str = "") -> str:
        return f"{self.base_url}/{action}" if action else self.base_url

    def step_url(self, step: int) -> str:
        return f"{self.base_url}?step={step}"

    def merge(self, updates: dict[str, Any], meta: dict[str, Any] | None = None) -> None:
        self.data.update(copy.deepcopy(updates))
        if meta:
            self.meta.update(meta)

    @property
    def dirty(self) -> bool:
        return signature(self.data) != self.meta.get("saved_sig")

    # ── persistence ──────────────────────────────────────
    def persist(self) -> Estimation:
        """Save the state as a DRAFT: create it once (new wizard), then edit it in place."""
        user, ip = self.request.user, _client_ip(self.request)
        if self.mode == REVISION:
            raise ServiceError(400, "Revisions are saved with Save Revision only.")
        if self.mode == DRAFT:
            est = services.edit_draft(self.estimation, to_model(self.data), user, ip)
        else:
            draft = Estimation.objects.filter(pk=self.meta.get("draft_id")).first() \
                if self.meta.get("draft_id") else None
            if draft is None:
                est = services.create_estimation(to_model(self.data, EstimationCreate), user)
                self.meta["draft_id"] = est.id
            else:
                est = services.edit_draft(draft, to_model(self.data), user, ip)
        self.meta["saved_sig"] = signature(self.data)
        self.save()
        return est

    def finish(self) -> Estimation:
        """Final save from the Review step: reuse the draft, or revise (snapshot + version bump)."""
        if self.mode == REVISION:
            est = services.revise(self.estimation, to_model(self.data), self.request.user,
                                  _client_ip(self.request))
        else:
            est = self.persist()
        self.clear()
        return est


def _client_ip(request) -> str | None:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")


# ── Preview ──────────────────────────────────────────────


def calculate_input(data: dict[str, Any]) -> CalculateInput:
    """The ``POST /estimations/calculate`` body the v3 wizard sends for this state."""
    return CalculateInput.model_validate({
        "project_type": data.get("project_type") or "EVOLUTION",
        "feature_ids": data.get("feature_ids", []),
        "new_feature_ids": data.get("new_feature_ids", []),
        "reference_project_ids": data.get("reference_project_ids", []),
        "dut_ids": data.get("dut_ids", []),
        "profile_ids": data.get("profile_ids", []),
        "dut_profile_matrix": data.get("dut_profile_matrix", []),
        "pr_fixes": data.get("pr_fixes") or {},
        "team_size": data.get("team_size", 1),
        "has_leader": data.get("has_leader", False),
        "working_days": data.get("working_days", 20),
        "delivery_date": data.get("expected_delivery"),
        "expected_releases": data.get("expected_releases", 1),
        "risk_item_ids": data.get("risk_item_ids", []),
        "document_type_ids": data.get("document_type_ids", []),
        "document_counts": data.get("document_counts", {}),
    })


def preview(data: dict[str, Any]) -> dict[str, Any]:
    """``services.calculate_preview`` plus what the detail page will show after saving.

    ``result`` is exactly the API preview. ``synthetic_tasks`` are the
    documentation tasks (hours already in ``documentation_hours``) and the
    PR test-creation task (its hours are added when the estimation is saved,
    as in v3). ``completion_date`` uses the testing start date.
    """
    result = services.calculate_preview(calculate_input(data))
    _, templates = services.resolve_features_and_templates(data.get("feature_ids", []))
    _, deliverables = calc.calc_doc_hours_and_deliverables(
        data.get("document_type_ids", []), data.get("document_counts", {}), {t.id for t in templates},
    )
    synthetic = [dict(t, pending=False) for t in calc.build_doc_synthetic_tasks(deliverables)]
    no_test_hours = calc.get_pr_config()["no_test_hours"]
    synthetic += [dict(t, pending=True)
                  for t in calc.build_pr_no_test_synthetic_tasks(data.get("pr_details", []), no_test_hours)]
    return {
        "result": result,
        "synthetic_tasks": synthetic,
        "deliverables": deliverables,
        "completion_date": services.estimated_completion_date(
            data.get("testing_start_date"), result["elapsed_days"]),
    }
