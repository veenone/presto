"""Session-authenticated report downloads for the estimation detail page.

Same generators, filenames and content types as the REST endpoints in
``apps/api/routers/reports.py`` (which need a bearer token, so the browser
cannot link to them directly).
"""

from __future__ import annotations

import logging

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

from apps.estimations.models import Estimation
from apps.reports.builders import build_report_data, report_filename
from apps.web.views import render_error

from .common import get_estimation_or_404

logger = logging.getLogger(__name__)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_TYPE = "application/pdf"


def _file_response(content: bytes, content_type: str, filename: str) -> HttpResponse:
    resp = HttpResponse(content, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _generator(fmt: str):
    if fmt == "xlsx":
        from apps.reports.excel_report import generate_excel_report
        return "Excel", generate_excel_report, XLSX_TYPE
    if fmt == "docx":
        from apps.reports.word_report import generate_word_report
        return "Word", generate_word_report, DOCX_TYPE
    if fmt == "pdf":
        from apps.reports.pdf_report import generate_pdf_report
        return "PDF", generate_pdf_report, PDF_TYPE
    raise Http404


@require_GET
def report_download(request, estimation_id: int, fmt: str):
    """GET /estimation/<id>/report/<xlsx|docx|pdf> (API: /api/estimations/{id}/report/{fmt})."""
    label, generate, content_type = _generator(fmt)
    estimation = get_estimation_or_404(estimation_id)
    try:
        content = generate(build_report_data(estimation))
    except Exception as exc:
        logger.exception("%s report generation failed for estimation %s", label, estimation_id)
        return render_error(request, 500, f"{label} report generation failed: {exc}")
    return _file_response(content, content_type, report_filename(estimation, fmt))


def _task_rows(estimation: Estimation) -> list[dict]:
    return [
        {"task_name": t.task_name, "task_type": t.task_type, "calculated_hours": t.calculated_hours}
        for t in estimation.tasks.all()
    ]


@require_GET
def executive_summary(request, estimation_id: int):
    """GET /estimation/<id>/report/executive-summary (API: same path under /api/estimations)."""
    from apps.reports.executive_summary import ExecutiveSummaryData, generate_executive_summary

    estimation = get_estimation_or_404(estimation_id)
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


@require_GET
def comparison_report(request, estimation_id: int):
    """GET /estimation/<id>/compare?other=<id> (API: POST /api/reports/comparison)."""
    from apps.reports.comparison_report import ComparisonReportData, generate_comparison_excel

    try:
        other_id = int(request.GET.get("other", ""))
    except ValueError:
        return render_error(request, 400, "Choose an estimation to compare with.")
    est_a = Estimation.objects.filter(pk=estimation_id).first()
    est_b = Estimation.objects.filter(pk=other_id).first()
    if not est_a or not est_b:
        raise Http404("One or both estimations not found")
    data = ComparisonReportData(estimation_a=_comparison_dict(est_a), estimation_b=_comparison_dict(est_b))
    content = generate_comparison_excel(data)
    filename = f"comparison_{est_a.estimation_number}_{est_b.estimation_number}.xlsx"
    return _file_response(content, XLSX_TYPE, filename)
