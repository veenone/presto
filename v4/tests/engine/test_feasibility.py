"""Unit tests for feasibility check and risk flag generation."""

from datetime import date, timedelta

import pytest

from engine.feasibility import (
    FeasibilityStatus,
    RiskFlag,
    assess_risks,
    check_feasibility,
)


class TestCheckFeasibility:
    def test_feasible(self):
        # 300h / (20 * 3 * 7 = 420h) = 71.4% → FEASIBLE
        result = check_feasibility(300, team_size=3, working_days=20)
        assert result.status == FeasibilityStatus.FEASIBLE
        assert result.utilization_pct == pytest.approx(71.4, abs=0.1)

    def test_at_risk(self):
        # 400h / (20 * 3 * 7 = 420h) = 95.2% → AT_RISK
        result = check_feasibility(400, team_size=3, working_days=20)
        assert result.status == FeasibilityStatus.AT_RISK
        assert result.recommendation is not None

    def test_not_feasible(self):
        # 500h / (20 * 3 * 7 = 420h) = 119% → NOT_FEASIBLE
        result = check_feasibility(500, team_size=3, working_days=20)
        assert result.status == FeasibilityStatus.NOT_FEASIBLE
        assert result.recommendation is not None

    def test_zero_capacity(self):
        result = check_feasibility(100, team_size=0, working_days=20)
        assert result.status == FeasibilityStatus.NOT_FEASIBLE

    def test_boundary_80_percent(self):
        # Exactly 80% → FEASIBLE (<=80%)
        capacity = 20 * 3 * 7.0  # 420
        result = check_feasibility(336.0, team_size=3, working_days=20)  # 336/420 = 80%
        assert result.status == FeasibilityStatus.FEASIBLE

    def test_boundary_100_percent(self):
        # Exactly 100% → AT_RISK (80-100%)
        result = check_feasibility(420.0, team_size=3, working_days=20)  # 420/420 = 100%
        assert result.status == FeasibilityStatus.AT_RISK


class TestAssessRisks:
    def test_high_new_feature_ratio(self):
        result = assess_risks(
            total_features=10,
            new_feature_count=6,
            reference_project_count=1,
            delivery_date=date.today() + timedelta(days=30),
            dut_profile_combinations=5,
        )
        assert RiskFlag.HIGH_NEW_FEATURE_RATIO in result.flags
        assert len(result.messages) == 1

    def test_no_reference_projects(self):
        result = assess_risks(
            total_features=5,
            new_feature_count=1,
            reference_project_count=0,
            delivery_date=date.today() + timedelta(days=30),
            dut_profile_combinations=5,
        )
        assert RiskFlag.NO_REFERENCE_PROJECTS in result.flags

    def test_compressed_timeline(self):
        result = assess_risks(
            total_features=5,
            new_feature_count=1,
            reference_project_count=1,
            delivery_date=date.today() + timedelta(days=7),
            dut_profile_combinations=5,
        )
        assert RiskFlag.COMPRESSED_TIMELINE in result.flags

    def test_high_matrix_complexity(self):
        result = assess_risks(
            total_features=5,
            new_feature_count=1,
            reference_project_count=1,
            delivery_date=date.today() + timedelta(days=30),
            dut_profile_combinations=25,
        )
        assert RiskFlag.HIGH_MATRIX_COMPLEXITY in result.flags

    def test_historical_underestimate(self):
        result = assess_risks(
            total_features=5,
            new_feature_count=1,
            reference_project_count=1,
            delivery_date=date.today() + timedelta(days=30),
            dut_profile_combinations=5,
            historical_accuracy_ratio=1.5,
        )
        assert RiskFlag.HISTORICAL_UNDERESTIMATE in result.flags

    def test_no_risks(self):
        result = assess_risks(
            total_features=10,
            new_feature_count=2,
            reference_project_count=2,
            delivery_date=date.today() + timedelta(days=60),
            dut_profile_combinations=10,
            historical_accuracy_ratio=0.95,
        )
        assert len(result.flags) == 0

    def test_multiple_risks(self):
        result = assess_risks(
            total_features=4,
            new_feature_count=3,
            reference_project_count=0,
            delivery_date=date.today() + timedelta(days=5),
            dut_profile_combinations=30,
            historical_accuracy_ratio=1.5,
        )
        assert len(result.flags) == 5
        assert len(result.messages) == 5
