"""Tests for the tester allocation engine."""

import pytest

from engine.allocator import (
    AllocationResult,
    TaskAllocation,
    TeamMemberInfo,
    allocate_testers,
)


class TestAllocateNoTesters:
    def test_returns_unallocated_hours(self):
        tasks = [
            {"task_name": "T1", "calculated_hours": 10, "is_parallelizable": False},
            {"task_name": "T2", "calculated_hours": 20, "is_parallelizable": True},
        ]
        result = allocate_testers(tasks, [])
        assert result.unallocated_hours == 30
        assert result.allocations == []
        assert "No testers available" in result.warnings[0]

    def test_leaders_excluded(self):
        """Only TESTER role members should be allocated, not TEST_LEADER."""
        tasks = [{"task_name": "T1", "calculated_hours": 10, "is_parallelizable": False}]
        team = [TeamMemberInfo(id=1, name="Lead", role="TEST_LEADER")]
        result = allocate_testers(tasks, team)
        assert result.unallocated_hours == 10
        assert len(result.allocations) == 0


class TestAllocateSingleTester:
    def test_all_tasks_assigned_to_single_tester(self):
        tasks = [
            {"task_name": "T1", "calculated_hours": 10, "is_parallelizable": False},
            {"task_name": "T2", "calculated_hours": 20, "is_parallelizable": True},
        ]
        team = [TeamMemberInfo(id=1, name="Alice", role="TESTER")]
        result = allocate_testers(tasks, team)
        assert len(result.allocations) == 2
        assert result.tester_workloads[1] == 30
        assert result.unallocated_hours == 0

    def test_parallel_task_not_split_with_one_tester(self):
        tasks = [{"task_name": "T1", "calculated_hours": 30, "is_parallelizable": True}]
        team = [TeamMemberInfo(id=1, name="Alice", role="TESTER")]
        result = allocate_testers(tasks, team)
        assert len(result.allocations) == 1
        assert result.allocations[0].assigned_tester_ids == [1]
        assert result.allocations[0].hours_per_tester == 30


class TestAllocateMultipleTesters:
    @pytest.fixture
    def two_testers(self):
        return [
            TeamMemberInfo(id=1, name="Alice", role="TESTER"),
            TeamMemberInfo(id=2, name="Bob", role="TESTER"),
        ]

    def test_non_parallel_goes_to_least_loaded(self, two_testers):
        tasks = [
            {"task_name": "T1", "calculated_hours": 30, "is_parallelizable": False},
            {"task_name": "T2", "calculated_hours": 10, "is_parallelizable": False},
        ]
        result = allocate_testers(tasks, two_testers)
        # Sorted by non-parallel first, largest first.
        # T1 (30h) -> least loaded (both 0) -> one of them
        # T2 (10h) -> least loaded -> the other one
        total_load = sum(result.tester_workloads.values())
        assert total_load == 40
        # Each tester should have some work (load balanced)
        assert result.tester_workloads[1] > 0
        assert result.tester_workloads[2] > 0

    def test_parallel_task_split_across_testers(self, two_testers):
        tasks = [{"task_name": "T1", "calculated_hours": 40, "is_parallelizable": True}]
        result = allocate_testers(tasks, two_testers)
        alloc = result.allocations[0]
        assert alloc.is_parallelizable is True
        assert len(alloc.assigned_tester_ids) == 2
        assert alloc.hours_per_tester == 20.0
        assert result.tester_workloads[1] == 20.0
        assert result.tester_workloads[2] == 20.0

    def test_mixed_tasks_balanced(self, two_testers):
        tasks = [
            {"task_name": "NonPar", "calculated_hours": 20, "is_parallelizable": False},
            {"task_name": "Par", "calculated_hours": 40, "is_parallelizable": True},
        ]
        result = allocate_testers(tasks, two_testers)
        assert len(result.allocations) == 2
        total = sum(result.tester_workloads.values())
        assert total == 60


class TestOverloadWarnings:
    def test_overloaded_tester_warning(self):
        team = [TeamMemberInfo(id=1, name="Alice", role="TESTER", available_hours_per_day=7.0)]
        # 7 * 20 = 140h capacity, assign 200h
        tasks = [{"task_name": "Big", "calculated_hours": 200, "is_parallelizable": False}]
        result = allocate_testers(tasks, team, working_days=20)
        assert len(result.warnings) == 1
        assert "overloaded" in result.warnings[0].lower()
        assert "Alice" in result.warnings[0]

    def test_no_warning_within_capacity(self):
        team = [TeamMemberInfo(id=1, name="Alice", role="TESTER", available_hours_per_day=7.0)]
        tasks = [{"task_name": "Small", "calculated_hours": 50, "is_parallelizable": False}]
        result = allocate_testers(tasks, team, working_days=20)
        assert len(result.warnings) == 0


class TestEdgeCases:
    def test_zero_hours_task_skipped(self):
        team = [TeamMemberInfo(id=1, name="Alice", role="TESTER")]
        tasks = [{"task_name": "Zero", "calculated_hours": 0, "is_parallelizable": False}]
        result = allocate_testers(tasks, team)
        assert len(result.allocations) == 0
        assert result.tester_workloads[1] == 0

    def test_empty_tasks(self):
        team = [TeamMemberInfo(id=1, name="Alice", role="TESTER")]
        result = allocate_testers([], team)
        assert len(result.allocations) == 0
        assert result.tester_workloads[1] == 0

    def test_tester_names_populated(self):
        team = [
            TeamMemberInfo(id=1, name="Alice", role="TESTER"),
            TeamMemberInfo(id=2, name="Bob", role="TESTER"),
        ]
        tasks = [{"task_name": "T1", "calculated_hours": 10, "is_parallelizable": False}]
        result = allocate_testers(tasks, team)
        assert result.tester_names[1] == "Alice"
        assert result.tester_names[2] == "Bob"
