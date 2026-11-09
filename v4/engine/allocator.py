"""Tester allocation engine.

Assigns testers to estimation tasks based on parallelizability,
team member skills, and availability.
"""

from dataclasses import dataclass, field


@dataclass
class TeamMemberInfo:
    id: int
    name: str
    role: str  # TESTER or TEST_LEADER
    available_hours_per_day: float = 7.0
    skill_feature_ids: list[int] = field(default_factory=list)


@dataclass
class TaskAllocation:
    task_name: str
    task_type: str
    calculated_hours: float
    is_parallelizable: bool
    assigned_tester_ids: list[int] = field(default_factory=list)
    assigned_tester_names: list[str] = field(default_factory=list)
    hours_per_tester: float = 0.0


@dataclass
class AllocationResult:
    allocations: list[TaskAllocation]
    tester_workloads: dict[int, float]  # tester_id -> total hours
    tester_names: dict[int, str]
    unallocated_hours: float
    warnings: list[str]


def allocate_testers(
    tasks: list[dict],
    team_members: list[TeamMemberInfo],
    working_days: int = 20,
) -> AllocationResult:
    """Allocate testers to tasks using a greedy load-balancing approach.

    For parallelizable tasks, split across multiple testers.
    For non-parallelizable tasks, assign to the least-loaded tester.
    """
    testers = [m for m in team_members if m.role == "TESTER"]
    if not testers:
        return AllocationResult(
            allocations=[],
            tester_workloads={},
            tester_names={},
            unallocated_hours=sum(t.get("calculated_hours", 0) for t in tasks),
            warnings=["No testers available for allocation."],
        )

    workloads: dict[int, float] = {t.id: 0.0 for t in testers}
    tester_names: dict[int, str] = {t.id: t.name for t in testers}
    allocations: list[TaskAllocation] = []
    warnings: list[str] = []

    # Sort tasks: non-parallelizable first (harder to schedule), then by hours desc
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (t.get("is_parallelizable", False), -t.get("calculated_hours", 0)),
    )

    for task in sorted_tasks:
        hours = task.get("calculated_hours", 0)
        is_parallel = task.get("is_parallelizable", False)
        task_name = task.get("task_name", task.get("name", ""))
        task_type = task.get("task_type", "")

        if hours <= 0:
            continue

        if is_parallel and len(testers) > 1:
            # Split across testers, preferring least-loaded
            sorted_testers = sorted(testers, key=lambda t: workloads[t.id])
            # Use up to all testers, but at least 2
            num_testers = min(len(testers), max(2, len(testers)))
            selected = sorted_testers[:num_testers]
            per_tester = hours / num_testers

            for t in selected:
                workloads[t.id] += per_tester

            allocations.append(TaskAllocation(
                task_name=task_name,
                task_type=task_type,
                calculated_hours=hours,
                is_parallelizable=True,
                assigned_tester_ids=[t.id for t in selected],
                assigned_tester_names=[t.name for t in selected],
                hours_per_tester=round(per_tester, 1),
            ))
        else:
            # Assign to least-loaded tester
            least_loaded = min(testers, key=lambda t: workloads[t.id])
            workloads[least_loaded.id] += hours

            allocations.append(TaskAllocation(
                task_name=task_name,
                task_type=task_type,
                calculated_hours=hours,
                is_parallelizable=False,
                assigned_tester_ids=[least_loaded.id],
                assigned_tester_names=[least_loaded.name],
                hours_per_tester=round(hours, 1),
            ))

    # Check for overloaded testers
    for tester in testers:
        capacity = tester.available_hours_per_day * working_days
        load = workloads[tester.id]
        if load > capacity:
            pct = round(load / capacity * 100, 1)
            warnings.append(
                f"{tester.name} is overloaded: {load:.0f}h assigned vs {capacity:.0f}h capacity ({pct}%)."
            )

    return AllocationResult(
        allocations=allocations,
        tester_workloads={tid: round(h, 1) for tid, h in workloads.items()},
        tester_names=tester_names,
        unallocated_hours=0,
        warnings=warnings,
    )
