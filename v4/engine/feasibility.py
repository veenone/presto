"""Feasibility check and risk flag generation.

Implements SPEC §4.3 (feasibility) and §4.5 (risk flags).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class FeasibilityStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    AT_RISK = "AT_RISK"
    NOT_FEASIBLE = "NOT_FEASIBLE"


class RiskFlag(str, Enum):
    HIGH_NEW_FEATURE_RATIO = "high_new_feature_ratio"
    NO_REFERENCE_PROJECTS = "no_reference_projects"
    COMPRESSED_TIMELINE = "compressed_timeline"
    HIGH_MATRIX_COMPLEXITY = "high_matrix_complexity"
    HISTORICAL_UNDERESTIMATE = "historical_underestimate"


@dataclass
class FeasibilityResult:
    status: FeasibilityStatus
    capacity_hours: float
    utilization_pct: float
    recommendation: str | None = None


@dataclass
class RiskAssessment:
    flags: list[RiskFlag]
    messages: list[str]


def check_feasibility(
    grand_total_hours: float,
    team_size: int,
    working_days: int,
    hours_per_day: float = 7.0,
) -> FeasibilityResult:
    """Check whether the estimation is feasible given team capacity.

    Thresholds per SPEC §4.3:
      <= 80% capacity → FEASIBLE
      80-100% capacity → AT_RISK
      > 100% capacity  → NOT_FEASIBLE
    """
    capacity = working_days * team_size * hours_per_day
    if capacity <= 0:
        return FeasibilityResult(
            status=FeasibilityStatus.NOT_FEASIBLE,
            capacity_hours=0,
            utilization_pct=999.0,
            recommendation="No team capacity defined.",
        )

    utilization = grand_total_hours / capacity * 100.0

    if utilization <= 80.0:
        return FeasibilityResult(
            status=FeasibilityStatus.FEASIBLE,
            capacity_hours=capacity,
            utilization_pct=round(utilization, 1),
        )
    elif utilization <= 100.0:
        extra_days = _days_to_feasible(grand_total_hours, team_size, hours_per_day, working_days)
        return FeasibilityResult(
            status=FeasibilityStatus.AT_RISK,
            capacity_hours=capacity,
            utilization_pct=round(utilization, 1),
            recommendation=f"Tight schedule. Consider extending by ~{extra_days} day(s) or adding 1 tester.",
        )
    else:
        extra_people = _people_to_feasible(grand_total_hours, working_days, hours_per_day, team_size)
        return FeasibilityResult(
            status=FeasibilityStatus.NOT_FEASIBLE,
            capacity_hours=capacity,
            utilization_pct=round(utilization, 1),
            recommendation=f"Over capacity. Add ~{extra_people} tester(s) or extend the delivery date.",
        )


def _days_to_feasible(
    total_hours: float, team_size: int, hours_per_day: float, current_days: int
) -> int:
    """Calculate how many extra days needed to bring utilization to 80%."""
    target_capacity = total_hours / 0.8
    needed_days = target_capacity / (team_size * hours_per_day)
    return max(1, round(needed_days - current_days))


def _people_to_feasible(
    total_hours: float, working_days: int, hours_per_day: float, current_size: int
) -> int:
    """Calculate how many extra people needed to bring utilization to 80%."""
    target_capacity = total_hours / 0.8
    needed_people = target_capacity / (working_days * hours_per_day)
    return max(1, round(needed_people - current_size))


def assess_risks(
    total_features: int,
    new_feature_count: int,
    reference_project_count: int,
    delivery_date: date | None,
    dut_profile_combinations: int,
    historical_accuracy_ratio: float | None = None,
    today: date | None = None,
) -> RiskAssessment:
    """Generate risk flags based on project parameters.

    Risk conditions per SPEC §4.5:
      - >50% new features → high estimation uncertainty
      - No reference projects → no baseline
      - Delivery < 2 weeks → compressed timeline
      - DUT×Profile > 20 → high matrix complexity
      - Historical accuracy ratio > 1.3 → team tends to underestimate
    """
    flags: list[RiskFlag] = []
    messages: list[str] = []

    if today is None:
        today = date.today()

    # New feature ratio
    if total_features > 0 and new_feature_count / total_features > 0.5:
        flags.append(RiskFlag.HIGH_NEW_FEATURE_RATIO)
        pct = round(new_feature_count / total_features * 100)
        messages.append(
            f"{pct}% of features are new (no existing tests) — high estimation uncertainty."
        )

    # No reference projects
    if reference_project_count == 0:
        flags.append(RiskFlag.NO_REFERENCE_PROJECTS)
        messages.append("No reference projects linked — no baseline for comparison.")

    # Compressed timeline
    if delivery_date is not None:
        days_until = (delivery_date - today).days
        if days_until < 14:
            flags.append(RiskFlag.COMPRESSED_TIMELINE)
            messages.append(
                f"Delivery date is {days_until} day(s) away — compressed timeline risk."
            )

    # High matrix complexity
    if dut_profile_combinations > 20:
        flags.append(RiskFlag.HIGH_MATRIX_COMPLEXITY)
        messages.append(
            f"{dut_profile_combinations} DUT×Profile combinations — high matrix complexity."
        )

    # Historical underestimate
    if historical_accuracy_ratio is not None and historical_accuracy_ratio > 1.3:
        flags.append(RiskFlag.HISTORICAL_UNDERESTIMATE)
        messages.append(
            f"Historical accuracy ratio is {historical_accuracy_ratio:.2f} — team tends to underestimate."
        )

    return RiskAssessment(flags=flags, messages=messages)
