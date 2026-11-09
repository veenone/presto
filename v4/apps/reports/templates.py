"""Report template registry and base class."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ReportType(str, Enum):
    STANDARD = "standard"
    COMPARISON = "comparison"
    TREND = "trend"
    EXECUTIVE_SUMMARY = "executive_summary"


@dataclass
class ReportMetadata:
    """Metadata for report branding and customization."""
    company_name: str = "Test Organization"
    company_logo_path: Optional[str] = None
    confidentiality_notice: str = "CONFIDENTIAL — Internal Use Only"
    generated_by: str = "Test Effort Estimation Tool v2.0"


@dataclass
class ComparisonReportData:
    """Data for side-by-side estimation comparison."""
    estimation_a: dict = field(default_factory=dict)
    estimation_b: dict = field(default_factory=dict)
    metadata: ReportMetadata = field(default_factory=ReportMetadata)


@dataclass
class TrendReportData:
    """Data for historical trend analysis."""
    projects: list[dict] = field(default_factory=list)
    metadata: ReportMetadata = field(default_factory=ReportMetadata)


@dataclass
class ExecutiveSummaryData:
    """Data for one-page executive summary."""
    project_name: str = ""
    estimation_number: str = ""
    project_type: str = ""
    created_by: Optional[str] = None
    created_at: str = ""
    grand_total_hours: float = 0
    grand_total_days: float = 0
    feasibility_status: str = ""
    total_tester_hours: float = 0
    total_leader_hours: float = 0
    pr_fix_hours: float = 0
    study_hours: float = 0
    buffer_hours: float = 0
    dut_count: int = 0
    profile_count: int = 0
    dut_profile_combinations: int = 0
    team_size: int = 0
    working_days: int = 0
    capacity_hours: float = 0
    utilization_pct: float = 0
    risk_flags: list[str] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    metadata: ReportMetadata = field(default_factory=ReportMetadata)
