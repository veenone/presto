"""Core estimation calculation engine.

Implements the formulas from SPEC §4.1-4.2:
  Task_Effort = Base_Hours × Combination_Count × Complexity_Weight
  Grand Total = Tester + Leader + PR_Fix + Study + Buffer
"""

from dataclasses import dataclass, field
from enum import Enum


class ProjectType(str, Enum):
    NEW = "NEW"
    EVOLUTION = "EVOLUTION"
    SUPPORT = "SUPPORT"


class TaskType(str, Enum):
    SETUP = "SETUP"
    EXECUTION = "EXECUTION"
    ANALYSIS = "ANALYSIS"
    REPORTING = "REPORTING"
    STUDY = "STUDY"


class PRComplexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


PR_COMPLEXITY_HOURS: dict[str, float] = {
    "simple": 2.0,
    "medium": 4.0,
    "complex": 8.0,
}

# Default hours to create tests when a PR has no existing tests
PR_NO_TEST_DEFAULT_HOURS: float = 8.0


@dataclass
class TaskInput:
    """Input data for a single task calculation."""

    name: str
    task_type: str
    base_effort_hours: float
    scales_with_dut: bool = False
    scales_with_profile: bool = False
    complexity_weight: float = 1.0
    is_new_feature_study: bool = False
    is_parallelizable: bool = False
    template_id: int | None = None
    feature_id: int | None = None
    feature_name: str | None = None


@dataclass
class TaskResult:
    """Result of calculating a single task's effort."""

    name: str
    task_type: str
    base_hours: float
    dut_multiplier: int
    profile_multiplier: int
    complexity_weight: float
    calculated_hours: float
    formula: str = ""
    is_new_feature_study: bool = False
    is_parallelizable: bool = False
    template_id: int | None = None
    feature_id: int | None = None
    feature_name: str | None = None


@dataclass
class PRFixInput:
    """PR fix counts by complexity."""

    simple: int = 0
    medium: int = 0
    complex: int = 0


@dataclass
class EstimationInput:
    """All inputs needed to compute an estimation."""

    project_type: str
    tasks: list[TaskInput]
    dut_count: int
    profile_count: int
    combination_count: int = 0  # DUT×Profile combinations from matrix
    pr_fixes: PRFixInput = field(default_factory=PRFixInput)
    new_feature_count: int = 0
    feature_study_hours_list: list[float] = field(default_factory=list)
    team_size: int = 1
    has_leader: bool = False
    working_days: int = 20
    # Configurable parameters (defaults from SPEC)
    leader_effort_ratio: float = 0.5
    new_feature_study_hours: float = 16.0
    working_hours_per_day: float = 7.0
    buffer_percentage: float = 10.0
    pr_scales_with_profile: bool = False
    expected_releases: int = 1
    release_effort_factor: float = 0.5
    # Configurable PR complexity hours (overrides PR_COMPLEXITY_HOURS)
    pr_complexity_hours: dict[str, float] = field(default_factory=lambda: dict(PR_COMPLEXITY_HOURS))
    # Extra hours per PR that has no test available
    pr_no_test_hours: float = PR_NO_TEST_DEFAULT_HOURS
    pr_no_test_count: int = 0
    # Task types affected by release effort multiplier (empty = all)
    release_effort_task_types: list[str] = field(default_factory=lambda: ["EXECUTION"])


@dataclass
class EstimationResult:
    """Complete estimation calculation result."""

    tasks: list[TaskResult]
    total_tester_hours: float
    total_leader_hours: float
    pr_fix_hours: float
    pr_no_test_total_hours: float
    study_hours: float
    release_extra_hours: float
    subtotal_hours: float
    buffer_hours: float
    grand_total_hours: float
    grand_total_days: float
    capacity_hours: float
    utilization_pct: float
    feasibility_status: str
    elapsed_hours: float = 0.0
    elapsed_days: float = 0.0
    elapsed_weeks: float = 0.0


def _format_number(v: float) -> str:
    """Format a number for formula display — drop .0 for integers."""
    return f"{v:g}"


def calculate_task_effort(
    task: TaskInput,
    dut_count: int,
    profile_count: int,
    combination_count: int = 0,
) -> TaskResult:
    """Calculate effort for a single task.

    Formula: base_hours × scaling_factor × complexity_weight

    When the task scales with both DUT and profile, the scaling factor uses
    the actual DUT×Profile combination count (from the matrix) rather than
    multiplying DUT count and profile count independently.
    """
    scales_both = task.scales_with_dut and task.scales_with_profile
    effective_combinations = combination_count if combination_count > 0 else dut_count * profile_count

    if scales_both:
        scaling = effective_combinations
        dut_mult_display = dut_count
        prof_mult_display = profile_count
    else:
        dut_mult_display = dut_count if task.scales_with_dut else 1
        prof_mult_display = profile_count if task.scales_with_profile else 1
        scaling = dut_mult_display * prof_mult_display

    calculated = task.base_effort_hours * scaling * task.complexity_weight

    # Build human-readable formula
    parts = [_format_number(task.base_effort_hours)]
    if scales_both and scaling > 1:
        if dut_count > 1 and profile_count > 1:
            parts.append(f"{scaling} combos ({dut_count}D×{profile_count}P)")
        elif dut_count > 1:
            parts.append(f"{scaling} DUTs")
        elif profile_count > 1:
            parts.append(f"{scaling} profiles")
    else:
        if task.scales_with_dut and dut_count > 1:
            parts.append(f"{dut_count} DUTs")
        if task.scales_with_profile and profile_count > 1:
            parts.append(f"{profile_count} profiles")
    if task.complexity_weight != 1.0:
        parts.append(f"w{_format_number(task.complexity_weight)}")

    formula = " × ".join(parts) + f" = {_format_number(calculated)}"

    return TaskResult(
        name=task.name,
        task_type=task.task_type,
        base_hours=task.base_effort_hours,
        dut_multiplier=dut_mult_display,
        profile_multiplier=prof_mult_display,
        complexity_weight=task.complexity_weight,
        calculated_hours=calculated,
        formula=formula,
        is_new_feature_study=task.is_new_feature_study,
        is_parallelizable=task.is_parallelizable,
        template_id=task.template_id,
        feature_id=task.feature_id,
        feature_name=task.feature_name,
    )


def calculate_pr_fix_effort(
    pr_fixes: PRFixInput,
    dut_count: int = 1,
    profile_count: int = 1,
    pr_scales_with_profile: bool = False,
    complexity_hours: dict[str, float] | None = None,
    combination_count: int = 0,
) -> float:
    """Calculate total PR fix validation effort.

    Each PR is validated per DUT (scales_with_dut = true per SPEC §9.2).
    Optionally scales with profile count if pr_scales_with_profile is enabled.
    When scaling with both DUT and profile, uses combination_count from matrix.
    """
    hours = complexity_hours or PR_COMPLEXITY_HOURS
    total = (
        pr_fixes.simple * hours.get("simple", 2.0)
        + pr_fixes.medium * hours.get("medium", 4.0)
        + pr_fixes.complex * hours.get("complex", 8.0)
    )
    if pr_scales_with_profile:
        effective = combination_count if combination_count > 0 else dut_count * profile_count
        return total * effective
    return total * dut_count


def calculate_estimation(inputs: EstimationInput) -> EstimationResult:
    """Run the full estimation calculation.

    Aggregation per SPEC §4.2:
      total_tester  = Σ(task efforts)
      leader_effort = total_tester × leader_effort_ratio
      pr_fix_effort = Σ(pr_count × hours_per_complexity) × scaling
      study_effort  = new_feature_count × new_feature_study_hours
      buffer        = subtotal × buffer_percentage / 100
      grand_total   = tester + leader + pr_fix + study + buffer
    """
    # Calculate each task
    task_results = [
        calculate_task_effort(task, inputs.dut_count, inputs.profile_count, inputs.combination_count)
        for task in inputs.tasks
    ]

    # Aggregate tester effort
    total_tester = sum(t.calculated_hours for t in task_results)

    # Test leader effort
    total_leader = total_tester * inputs.leader_effort_ratio if inputs.has_leader else 0.0

    # PR fix effort
    pr_fix_hours = calculate_pr_fix_effort(
        inputs.pr_fixes,
        inputs.dut_count,
        inputs.profile_count,
        inputs.pr_scales_with_profile,
        complexity_hours=inputs.pr_complexity_hours,
        combination_count=inputs.combination_count,
    )

    # PR no-test effort: extra hours for PRs without existing tests
    pr_no_test_total = inputs.pr_no_test_count * inputs.pr_no_test_hours

    # New feature study effort — use per-feature hours if available, else flat rate
    if inputs.feature_study_hours_list:
        study_hours = sum(inputs.feature_study_hours_list)
    else:
        study_hours = inputs.new_feature_count * inputs.new_feature_study_hours

    # Release extra hours: each additional release adds a fraction of filtered task effort
    extra_releases = max(0, inputs.expected_releases - 1)
    if inputs.release_effort_task_types:
        release_base_hours = sum(
            t.calculated_hours for t in task_results
            if t.task_type in inputs.release_effort_task_types
        )
    else:
        release_base_hours = total_tester
    if inputs.has_leader:
        release_base_hours += release_base_hours * inputs.leader_effort_ratio
    release_extra_hours = release_base_hours * extra_releases * inputs.release_effort_factor

    # Subtotal before buffer
    subtotal = total_tester + total_leader + pr_fix_hours + pr_no_test_total + study_hours + release_extra_hours

    # Buffer
    buffer = subtotal * inputs.buffer_percentage / 100.0

    # Grand total
    grand_total = subtotal + buffer
    grand_total_days = grand_total / inputs.working_hours_per_day

    # Capacity and feasibility
    team_size = inputs.team_size + (1 if inputs.has_leader else 0)
    capacity = inputs.working_days * team_size * inputs.working_hours_per_day
    utilization = (grand_total / capacity * 100.0) if capacity > 0 else 999.0

    if grand_total <= capacity * 0.8:
        feasibility = "FEASIBLE"
    elif grand_total <= capacity:
        feasibility = "AT_RISK"
    else:
        feasibility = "NOT_FEASIBLE"

    # Elapsed time estimate — wall-clock hours considering parallelism.
    # Parallelizable tasks can be split across testers; sequential tasks cannot.
    # Leader works in parallel with testers, so leader hours don't add elapsed time.
    tester_count = max(1, inputs.team_size)
    parallel_hours = sum(t.calculated_hours for t in task_results if t.is_parallelizable)
    sequential_hours = sum(t.calculated_hours for t in task_results if not t.is_parallelizable)
    elapsed_tester = (parallel_hours / tester_count) + sequential_hours
    # Add non-task items (PR fix, study, release extra) as sequential
    elapsed_other = pr_fix_hours + pr_no_test_total + study_hours + release_extra_hours
    elapsed_subtotal = elapsed_tester + elapsed_other
    elapsed_buffer = elapsed_subtotal * inputs.buffer_percentage / 100.0
    elapsed_hours = elapsed_subtotal + elapsed_buffer
    elapsed_days = round(elapsed_hours / inputs.working_hours_per_day, 1) if inputs.working_hours_per_day else 0
    elapsed_weeks = round(elapsed_days / 5.0, 1)

    return EstimationResult(
        tasks=task_results,
        total_tester_hours=total_tester,
        total_leader_hours=total_leader,
        pr_fix_hours=pr_fix_hours,
        pr_no_test_total_hours=pr_no_test_total,
        study_hours=study_hours,
        release_extra_hours=release_extra_hours,
        subtotal_hours=subtotal,
        buffer_hours=buffer,
        grand_total_hours=grand_total,
        grand_total_days=round(grand_total_days, 1),
        capacity_hours=capacity,
        utilization_pct=round(utilization, 1),
        feasibility_status=feasibility,
        elapsed_hours=round(elapsed_hours, 1),
        elapsed_days=elapsed_days,
        elapsed_weeks=elapsed_weeks,
    )
