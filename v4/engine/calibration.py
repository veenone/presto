"""Historical calibration logic.

Implements SPEC §4.4:
  Accuracy_Ratio = Actual_Hours / Estimated_Hours
  If ratio > 1.0, suggest applying as adjustment factor.
"""

from dataclasses import dataclass


@dataclass
class CalibrationResult:
    accuracy_ratio: float
    should_warn: bool
    suggested_adjustment: float
    message: str


@dataclass
class HistoricalDataPoint:
    project_name: str
    estimated_hours: float
    actual_hours: float
    feature_ids: list[int]


def calculate_accuracy_ratio(projects: list[HistoricalDataPoint]) -> float | None:
    """Calculate the average accuracy ratio across reference projects.

    Returns None if no valid projects are provided.
    """
    valid = [p for p in projects if p.estimated_hours > 0]
    if not valid:
        return None
    ratios = [p.actual_hours / p.estimated_hours for p in valid]
    return sum(ratios) / len(ratios)


def calculate_feature_overlap(
    current_feature_ids: list[int],
    reference_feature_ids: list[int],
) -> float:
    """Calculate the percentage of feature overlap between current and reference project."""
    if not current_feature_ids:
        return 0.0
    current = set(current_feature_ids)
    reference = set(reference_feature_ids)
    overlap = current & reference
    return len(overlap) / len(current) * 100.0


def calibrate(
    reference_projects: list[HistoricalDataPoint],
    current_feature_ids: list[int] | None = None,
) -> CalibrationResult:
    """Produce a calibration result from reference project data.

    Per SPEC §4.4:
    - If average accuracy ratio > 1.0, we consistently underestimate.
    - If > 1.3, it's a risk flag.
    - The suggested adjustment is the ratio itself (to multiply estimates by).
    """
    ratio = calculate_accuracy_ratio(reference_projects)

    if ratio is None:
        return CalibrationResult(
            accuracy_ratio=1.0,
            should_warn=False,
            suggested_adjustment=1.0,
            message="No valid reference projects for calibration.",
        )

    ratio = round(ratio, 3)
    should_warn = ratio > 1.0

    if ratio <= 1.0:
        message = f"Historical accuracy ratio is {ratio:.2f} — estimates are accurate or conservative."
    elif ratio <= 1.3:
        message = (
            f"Historical accuracy ratio is {ratio:.2f} — team tends to slightly underestimate. "
            f"Consider applying a {ratio:.2f}x adjustment factor."
        )
    else:
        message = (
            f"Historical accuracy ratio is {ratio:.2f} — team significantly underestimates. "
            f"Strongly recommend applying a {ratio:.2f}x adjustment factor."
        )

    return CalibrationResult(
        accuracy_ratio=ratio,
        should_warn=should_warn,
        suggested_adjustment=ratio if ratio > 1.0 else 1.0,
        message=message,
    )
