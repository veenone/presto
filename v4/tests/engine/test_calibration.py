"""Unit tests for historical calibration logic."""

import pytest

from engine.calibration import (
    CalibrationResult,
    HistoricalDataPoint,
    calculate_accuracy_ratio,
    calculate_feature_overlap,
    calibrate,
)


class TestAccuracyRatio:
    def test_exact_estimate(self):
        projects = [HistoricalDataPoint("P1", 100, 100, [])]
        assert calculate_accuracy_ratio(projects) == 1.0

    def test_underestimate(self):
        projects = [HistoricalDataPoint("P1", 100, 130, [])]
        assert calculate_accuracy_ratio(projects) == 1.3

    def test_overestimate(self):
        projects = [HistoricalDataPoint("P1", 100, 80, [])]
        assert calculate_accuracy_ratio(projects) == 0.8

    def test_average_of_multiple(self):
        projects = [
            HistoricalDataPoint("P1", 100, 120, []),
            HistoricalDataPoint("P2", 200, 220, []),
        ]
        # (1.2 + 1.1) / 2 = 1.15
        assert calculate_accuracy_ratio(projects) == pytest.approx(1.15)

    def test_empty_list(self):
        assert calculate_accuracy_ratio([]) is None

    def test_zero_estimated(self):
        projects = [HistoricalDataPoint("P1", 0, 100, [])]
        assert calculate_accuracy_ratio(projects) is None


class TestFeatureOverlap:
    def test_full_overlap(self):
        assert calculate_feature_overlap([1, 2, 3], [1, 2, 3]) == 100.0

    def test_no_overlap(self):
        assert calculate_feature_overlap([1, 2], [3, 4]) == 0.0

    def test_partial_overlap(self):
        assert calculate_feature_overlap([1, 2, 3, 4], [2, 3]) == 50.0

    def test_empty_current(self):
        assert calculate_feature_overlap([], [1, 2]) == 0.0


class TestCalibrate:
    def test_no_projects(self):
        result = calibrate([])
        assert result.accuracy_ratio == 1.0
        assert result.should_warn is False

    def test_accurate_team(self):
        projects = [HistoricalDataPoint("P1", 100, 95, [])]
        result = calibrate(projects)
        assert result.accuracy_ratio == 0.95
        assert result.should_warn is False
        assert result.suggested_adjustment == 1.0

    def test_slight_underestimate(self):
        projects = [HistoricalDataPoint("P1", 100, 115, [])]
        result = calibrate(projects)
        assert result.accuracy_ratio == 1.15
        assert result.should_warn is True
        assert result.suggested_adjustment == 1.15

    def test_significant_underestimate(self):
        projects = [HistoricalDataPoint("P1", 100, 150, [])]
        result = calibrate(projects)
        assert result.accuracy_ratio == 1.5
        assert result.should_warn is True
        assert "strongly recommend" in result.message.lower()
