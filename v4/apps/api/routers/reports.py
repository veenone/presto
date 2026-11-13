"""API routes: /estimations/{id}/report/*, /estimations/{id}/send-report, /reports/*."""

from __future__ import annotations

import logging

from django.http import HttpResponse
from ninja import Router
from ninja.errors import HttpError

from apps.api.auth import authenticated, role
from apps.api.common import get_or_404
from apps.api.schemas.reports import SendReportOut
from apps.estimations.models import Estimation, HistoricalProject
from apps.reports.builders import build_report_data, report_filename

logger = logging.getLogger(__name__)

router = Router(auth=authenticated, tags=["reports"])

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_TYPE = "application/pdf"


def _file_response(content: bytes, content_type: str, filename: str, expose: bool = False) -> HttpResponse:
    resp = HttpResponse(content, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    if expose:
        resp["Access-Control-Expose-Headers"] = "Content-Disposition"
    return resp


def _get_estimation(estimation_id: int) -> Estimation:
    return get_or_404(Estimation, estimation_id, "Estimation not found")


def _full_report(estimation_id: int, ext: str, label: str, generate, content_type: str) -> HttpResponse:
    estimation = _get_estimation(estimation_id)
    try:
        content = generate(build_report_data(estimation))
    except Exception as exc:
        logger.exception("%s report generation failed for estimation %s", label, estimation_id)
        raise HttpError(500, f"{label} report generation failed: {exc}") from exc
    return _file_response(content, content_type, report_filename(estimation, ext), expose=True)


@router.get("/estimations/{estimation_id}/report/xlsx")
def download_excel_report(request, estimation_id: int):
    from apps.reports.excel_report import generate_excel_report
    return _full_report(estimation_id, "xlsx", "Excel", generate_excel_report, XLSX_TYPE)


@router.get("/estimations/{estimation_id}/report/docx")
def download_word_report(request, estimation_id: int):
    from apps.reports.word_report import generate_word_report
    return _full_report(estimation_id, "docx", "Word", generate_word_report, DOCX_TYPE)


@router.get("/estimations/{estimation_id}/report/pdf")
def download_pdf_report(request, estimation_id: int):
    from apps.reports.pdf_report import generate_pdf_report
    return _full_report(estimation_id, "pdf", "PDF", generate_pdf_report, PDF_TYPE)


@router.post("/estimations/{estimation_id}/send-report", auth=role("ESTIMATOR"), response=SendReportOut)
def send_estimation_report(request, estimation_id: int, to_email: str):
    estimation = _get_estimation(estimation_id)

    from apps.integrations.service import get_adapter
    adapter = get_adapter("EMAIL")
    if not adapter:
        raise HttpError(400, "Email integration is not configured or not enabled.")

    from apps.reports.pdf_report import generate_pdf_report
    pdf_bytes = generate_pdf_report(build_report_data(estimation))

    from apps.integrations.adapters import EmailAdapter
    if not isinstance(adapter, EmailAdapter):
        raise HttpError(500, "Invalid email adapter")

    result = adapter.send_estimation_report(
        to_email=to_email,
        estimation_number=estimation.estimation_number or "",
        project_name=estimation.project_name,
        grand_total_hours=estimation.grand_total_hours,
        feasibility_status=estimation.feasibility_status,
        report_bytes=pdf_bytes,
        report_filename=f"{estimation.estimation_number or 'report'}.pdf",
    )
    if result.status.value == "SUCCESS":
        return {"status": "ok", "message": f"Report sent to {to_email}"}
    raise HttpError(500, f"Failed to send: {'; '.join(result.errors)}")


def _task_rows(estimation: Estimation) -> list[dict]:
    return [
        {"task_name": t.task_name, "task_type": t.task_type, "calculated_hours": t.calculated_hours}
        for t in estimation.tasks.all()
    ]


@router.get("/estimations/{estimation_id}/report/executive-summary")
def download_executive_summary(request, estimation_id: int):
    estimation = _get_estimation(estimation_id)

    from apps.reports.executive_summary import ExecutiveSummaryData, generate_executive_summary
    data = ExecutiveSummaryData(
        project_name=estimation.project_name,
        estimation_number=estimation.estimation_number or "",
        project_type=estimation.project_type,
        created_by=estimation.created_by,
        created_at=str(estimation.created_at.date()) if estimation.created_at else "",
        grand_total_hours=estimation.grand_total_hours,
        grand_total_days=estimation.grand_total_days,
        feasibility_status=estimation.feasibility_status,
        total_tester_hours=estimation.total_tester_hours,
        total_leader_hours=estimation.total_leader_hours,
        dut_count=estimation.dut_count,
        profile_count=estimation.profile_count,
        dut_profile_combinations=estimation.dut_profile_combinations,
        tasks=_task_rows(estimation),
    )
    content = generate_executive_summary(data)
    filename = f"{estimation.estimation_number or f'EST-{estimation_id}'}_summary.pdf"
    return _file_response(content, PDF_TYPE, filename)


def _comparison_dict(est: Estimation) -> dict:
    return {
        "estimation_number": est.estimation_number,
        "project_name": est.project_name,
        "project_type": est.project_type,
        "grand_total_hours": est.grand_total_hours,
        "grand_total_days": est.grand_total_days,
        "total_tester_hours": est.total_tester_hours,
        "total_leader_hours": est.total_leader_hours,
        "feasibility_status": est.feasibility_status,
        "dut_count": est.dut_count,
        "profile_count": est.profile_count,
        "dut_profile_combinations": est.dut_profile_combinations,
        "pr_fix_count": est.pr_fix_count,
        "status": est.status,
        "tasks": _task_rows(est),
    }


@router.post("/reports/comparison")
def generate_comparison(request, estimation_a_id: int, estimation_b_id: int):
    est_a = Estimation.objects.filter(pk=estimation_a_id).first()
    est_b = Estimation.objects.filter(pk=estimation_b_id).first()
    if not est_a or not est_b:
        raise HttpError(404, "One or both estimations not found")

    from apps.reports.comparison_report import ComparisonReportData, generate_comparison_excel
    data = ComparisonReportData(
        estimation_a=_comparison_dict(est_a),
        estimation_b=_comparison_dict(est_b),
    )
    content = generate_comparison_excel(data)
    filename = f"comparison_{est_a.estimation_number}_{est_b.estimation_number}.xlsx"
    return _file_response(content, XLSX_TYPE, filename)


@router.get("/reports/trend")
def generate_trend(request):
    from apps.reports.trend_report import TrendReportData, generate_trend_excel
    data = TrendReportData(
        projects=[
            {
                "project_name": p.project_name,
                "project_type": p.project_type,
                "estimated_hours": p.estimated_hours,
                "actual_hours": p.actual_hours,
                "dut_count": p.dut_count,
                "completion_date": str(p.completion_date) if p.completion_date else "",
            }
            for p in HistoricalProject.objects.order_by("id")
        ]
    )
    content = generate_trend_excel(data)
    return _file_response(content, XLSX_TYPE, "trend_analysis.xlsx")
