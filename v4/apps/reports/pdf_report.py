"""PDF report generation with ReportLab.

Generates a formal estimation PDF matching the Word report structure per SPEC §5.2.
"""

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .excel_report import ExcelReportData

# ── Colors ───────────────────────────────────────────────

BLUE = colors.HexColor("#2F5496")
LIGHT_BLUE = colors.HexColor("#D9E2F3")
GREEN = colors.HexColor("#C6EFCE")
AMBER = colors.HexColor("#FFEB9C")
RED = colors.HexColor("#FFC7CE")
DARK_GRAY = colors.HexColor("#404040")
LIGHT_GRAY = colors.HexColor("#F2F2F2")


def _feasibility_color(status: str) -> colors.Color:
    if status == "FEASIBLE":
        return GREEN
    elif status == "AT_RISK":
        return AMBER
    return RED


def _make_styles() -> dict:
    styles = getSampleStyleSheet()
    custom = {}

    custom["CoverTitle"] = ParagraphStyle(
        "CoverTitle", parent=styles["Title"],
        fontSize=28, textColor=BLUE, alignment=1, spaceAfter=20,
    )
    custom["CoverSubtitle"] = ParagraphStyle(
        "CoverSubtitle", parent=styles["Normal"],
        fontSize=16, textColor=DARK_GRAY, alignment=1, spaceAfter=8,
    )
    custom["CoverMeta"] = ParagraphStyle(
        "CoverMeta", parent=styles["Normal"],
        fontSize=11, textColor=colors.HexColor("#606060"), alignment=1, spaceAfter=4,
    )
    custom["SectionTitle"] = ParagraphStyle(
        "SectionTitle", parent=styles["Heading1"],
        fontSize=16, textColor=BLUE, spaceBefore=16, spaceAfter=8,
    )
    custom["SubSection"] = ParagraphStyle(
        "SubSection", parent=styles["Heading2"],
        fontSize=12, textColor=BLUE, spaceBefore=10, spaceAfter=6,
    )
    custom["Body"] = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=10, spaceAfter=6, leading=14,
    )
    custom["BoldBody"] = ParagraphStyle(
        "BoldBody", parent=styles["Normal"],
        fontSize=10, spaceAfter=6, leading=14, fontName="Helvetica-Bold",
    )
    custom["RiskItem"] = ParagraphStyle(
        "RiskItem", parent=styles["Normal"],
        fontSize=10, spaceAfter=4, leftIndent=20, bulletIndent=10,
    )
    custom["Cell"] = ParagraphStyle(
        "Cell", parent=styles["Normal"],
        fontSize=8, leading=10,
    )
    return custom


def _make_table(headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None) -> Table:
    data = [headers] + rows
    table = Table(data, colWidths=col_widths, repeatRows=1)

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    table.setStyle(TableStyle(style_commands))
    return table


def generate_pdf_report(data: ExcelReportData, output_path: str | Path | None = None) -> bytes | None:
    """Generate a PDF report.

    Args:
        data: ExcelReportData with all estimation information.
        output_path: If provided, saves to file. If None, returns bytes.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer if output_path is None else str(output_path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = _make_styles()
    story: list = []
    page_width = A4[0] - 40 * mm

    # ── 1. Cover page ──────────────────────────────────
    story.append(Spacer(1, 80))
    story.append(Paragraph("PRESTO — Estimation Report", styles["CoverTitle"]))
    story.append(Spacer(1, 20))
    story.append(Paragraph(data.project_name, styles["CoverSubtitle"]))
    story.append(Spacer(1, 30))

    meta_lines = [
        f"Estimation: {data.estimation_number} (v{data.version})",
        f"Project Type: {data.project_type}",
        f"Status: {data.status}",
    ]
    if data.request_number:
        meta_lines.append(f"Request: {data.request_number}")
    meta_lines.extend([
        f"Author: {data.created_by}",
        f"Date: {data.created_at}",
    ])
    for line in meta_lines:
        story.append(Paragraph(line, styles["CoverMeta"]))

    story.append(PageBreak())

    # ── 2. Request details ─────────────────────────────
    if data.request_number:
        story.append(Paragraph("Request Details", styles["SectionTitle"]))
        story.append(_make_table(
            ["Field", "Value"],
            [
                ["Request Number", data.request_number],
                ["Requester", data.requester_name],
                ["Business Unit", data.business_unit],
                ["Priority", data.priority],
                ["Requested Delivery", data.expected_delivery],
            ],
            col_widths=[page_width * 0.35, page_width * 0.65],
        ))
        story.append(Spacer(1, 12))

    # ── 2b. Project context ─────────────────────────────
    if data.project_goals or data.target_customer:
        story.append(Paragraph("Project Context", styles["SectionTitle"]))
        if data.project_goals:
            story.append(Paragraph(f"<b>Project Goals:</b> {data.project_goals}", styles["Body"]))
        if data.target_customer:
            story.append(Paragraph(f"<b>Target Customer:</b> {data.target_customer}", styles["Body"]))
        story.append(Spacer(1, 12))

    # ── 3. Executive summary ───────────────────────────
    story.append(Paragraph("Executive Summary", styles["SectionTitle"]))
    summary_text = (
        f"This estimation covers the <b>{data.project_type}</b> project "
        f'"{data.project_name}" with {data.dut_count} DUT(s), '
        f"{data.profile_count} test profile(s), and {data.pr_fix_count} PR fix(es)."
    )
    story.append(Paragraph(summary_text, styles["Body"]))
    story.append(Spacer(1, 6))

    story.append(_make_table(
        ["Metric", "Value"],
        [
            ["Grand Total (Hours)", f"{data.grand_total_hours:.1f}"],
            ["Grand Total (Person-Days)", f"{data.grand_total_days:.1f}"],
            ["Capacity (Hours)", f"{data.capacity_hours:.1f}"],
            ["Utilization", f"{data.utilization_pct:.1f}%"],
            ["Feasibility", data.feasibility_status],
        ],
        col_widths=[page_width * 0.45, page_width * 0.55],
    ))
    story.append(Spacer(1, 12))

    # ── 4. Project parameters ──────────────────────────
    story.append(Paragraph("Project Parameters", styles["SectionTitle"]))
    story.append(_make_table(
        ["Parameter", "Value"],
        [
            ["Project Type", data.project_type],
            ["DUT Count", str(data.dut_count)],
            ["Profile Count", str(data.profile_count)],
            ["Combinations", str(data.dut_profile_combinations)],
            ["PR Fixes", str(data.pr_fix_count)],
            ["Team Size", str(data.team_size)],
            ["Test Leader", "Yes" if data.has_leader else "No"],
            ["Project Start Date (T0)", data.start_date],
            ["Testing Start Date", data.testing_start_date],
            ["Target Date", data.expected_delivery],
        ],
        col_widths=[page_width * 0.40, page_width * 0.60],
    ))
    story.append(Spacer(1, 12))

    # ── 4b. DUT x Profile Matrix ─────────────────────
    if data.dut_types and data.profiles:
        story.append(Paragraph("DUT x Profile Matrix", styles["SectionTitle"]))
        matrix_headers = ["DUT \\ Profile"] + [p.get("name", "") for p in data.profiles]
        active_combos = set()
        for combo in data.dut_profile_matrix:
            if len(combo) >= 2:
                active_combos.add((combo[0], combo[1]))
        matrix_rows = []
        for dut in data.dut_types:
            row = [dut.get("name", "")]
            for prof in data.profiles:
                is_active = (dut.get("id"), prof.get("id")) in active_combos or not data.dut_profile_matrix
                row.append("Yes" if is_active else "-")
            matrix_rows.append(row)
        num_cols = len(data.profiles) + 1
        matrix_col_w = [page_width / num_cols] * num_cols
        story.append(_make_table(matrix_headers, matrix_rows, col_widths=matrix_col_w))
        story.append(Spacer(1, 12))

    # ── 5. Task breakdown ──────────────────────────────
    story.append(Paragraph("Task Breakdown", styles["SectionTitle"]))
    hpd = data.working_hours_per_day
    task_rows = []
    for task in data.tasks:
        tester_h = task.get("calculated_hours", 0)
        leader_h = task.get("leader_hours", 0)
        total_h = tester_h + leader_h
        task_rows.append([
            task.get("task_name", task.get("name", "")),
            task.get("task_type", ""),
            f"{task.get('base_hours', 0):.1f}",
            f"{tester_h:.1f}",
            f"{leader_h:.1f}",
            f"{total_h:.1f}",
            f"{total_h / hpd:.1f}",
        ])
    col_w = [page_width * 0.26, page_width * 0.12, page_width * 0.12, page_width * 0.12, page_width * 0.12, page_width * 0.13, page_width * 0.13]
    story.append(_make_table(["Task", "Type", "Base Hrs", "Tester Hrs", "Leader Hrs", "Total Hrs", "Total MD"], task_rows, col_widths=col_w))
    story.append(Spacer(1, 12))

    # ── 6. Effort summary ──────────────────────────────
    story.append(Paragraph("Effort Summary", styles["SectionTitle"]))
    effort_rows = [
        ["Total Tester Effort", f"{data.total_tester_hours:.1f}", f"{data.total_tester_hours / hpd:.1f}"],
        ["Test Leader Effort", f"{data.total_leader_hours:.1f}", f"{data.total_leader_hours / hpd:.1f}"],
        ["PR Fix Validation", f"{data.pr_fix_hours:.1f}", f"{data.pr_fix_hours / hpd:.1f}"],
        ["New Feature Study", f"{data.study_hours:.1f}", f"{data.study_hours / hpd:.1f}"],
    ]
    if getattr(data, "pr_no_test_hours", 0) > 0:
        effort_rows.append(["PR Test Creation", f"{data.pr_no_test_hours:.1f}", f"{data.pr_no_test_hours / hpd:.1f}"])
    if data.release_extra_hours > 0:
        effort_rows.append(["Release Extra Effort", f"{data.release_extra_hours:.1f}", f"{data.release_extra_hours / hpd:.1f}"])
    if data.documentation_hours > 0:
        effort_rows.append(["Documentation Effort", f"{data.documentation_hours:.1f}", f"{data.documentation_hours / hpd:.1f}"])
    effort_rows.append(["Buffer (10%)", f"{data.buffer_hours:.1f}", f"{data.buffer_hours / hpd:.1f}"])
    effort_rows.append(["GRAND TOTAL", f"{data.grand_total_hours:.1f}", f"{data.grand_total_days:.1f}"])
    if getattr(data, "working_weeks", 0) > 0:
        effort_rows.append(["Working Weeks", f"{data.working_weeks:.1f}", ""])
    story.append(_make_table(
        ["Component", "Hours", "Person-Days"],
        effort_rows,
        col_widths=[page_width * 0.45, page_width * 0.275, page_width * 0.275],
    ))
    story.append(Spacer(1, 12))

    # ── 6b. Team Allocation ──────────────────────────
    if data.team_members:
        story.append(Paragraph("Team Allocation", styles["SectionTitle"]))
        alloc_rows = []
        for tm in data.team_members:
            alloc_rows.append([
                tm.get("name", ""),
                tm.get("role", ""),
                f"{tm.get('allocated_hours', 0):.1f}",
            ])
        story.append(_make_table(
            ["Name", "Role", "Allocated Hours"],
            alloc_rows,
            col_widths=[page_width * 0.40, page_width * 0.30, page_width * 0.30],
        ))
        story.append(Spacer(1, 12))

    # ── 6c. PR Fixes breakdown ──────────────────────
    if data.pr_fix_count > 0:
        story.append(Paragraph("PR Fixes Breakdown", styles["SectionTitle"]))
        pr_breakdown_rows = [
            ["Simple", str(data.pr_simple), "2", str(data.pr_simple * 2), f"{data.pr_simple * 2 / hpd:.1f}"],
            ["Medium", str(data.pr_medium), "4", str(data.pr_medium * 4), f"{data.pr_medium * 4 / hpd:.1f}"],
            ["Complex", str(data.pr_complex), "8", str(data.pr_complex * 8), f"{data.pr_complex * 8 / hpd:.1f}"],
            ["TOTAL", str(data.pr_fix_count), "", f"{data.pr_fix_hours:.1f}", f"{data.pr_fix_hours / hpd:.1f}"],
        ]
        story.append(_make_table(
            ["Complexity", "Count", "Hours Each", "Subtotal (h)", "Subtotal (MD)"],
            pr_breakdown_rows,
            col_widths=[page_width * 0.24, page_width * 0.16, page_width * 0.20, page_width * 0.20, page_width * 0.20],
        ))
        story.append(Spacer(1, 8))

        if data.pr_details:
            story.append(Paragraph("PR Details", styles["SubSection"]))
            pr_detail_rows = []
            for pr in data.pr_details:
                pr_detail_rows.append([
                    pr.get("pr_number", ""),
                    pr.get("link", "") or "—",
                    pr.get("description", ""),
                    pr.get("complexity", ""),
                    pr.get("status", ""),
                ])
            story.append(_make_table(
                ["PR #", "Link", "Description", "Complexity", "Status"],
                pr_detail_rows,
                col_widths=[page_width * 0.10, page_width * 0.30, page_width * 0.30, page_width * 0.15, page_width * 0.15],
            ))
            story.append(Spacer(1, 12))

    # ── 6d. Document Deliverables ────────────────────────
    if data.document_deliverables:
        story.append(Paragraph("Document Deliverables", styles["SectionTitle"]))
        cell = styles["Cell"]
        doc_rows = []
        for dd in data.document_deliverables:
            doc_rows.append([
                Paragraph(str(dd.get("name", "")), cell),
                Paragraph(str(dd.get("category", "")), cell),
                Paragraph(str(dd.get("linked_task") or "—"), cell),
                str(dd.get("count", 1)),
                f"{dd.get('base_effort_hours', 0):.1f}",
                f"{dd.get('total_hours', 0):.1f}",
                f"{dd.get('effective_hours', dd.get('total_hours', 0)):.1f}",
                Paragraph(str(dd.get("overlap_note") or ""), cell),
            ])
        story.append(_make_table(
            ["Document Type", "Category", "Linked Task", "Count", "Base Hrs", "Total Hrs", "Effective Hrs", "Note"],
            doc_rows,
            col_widths=[
                page_width * 0.18, page_width * 0.12, page_width * 0.14,
                page_width * 0.07, page_width * 0.08, page_width * 0.09,
                page_width * 0.12, page_width * 0.20,
            ],
        ))
        story.append(Spacer(1, 12))

    # ── 6e. Features Under Test ──────────────────────────
    if data.features_breakdown:
        story.append(Paragraph("Features Under Test", styles["SectionTitle"]))
        cell = styles["Cell"]
        if data.presets_used:
            story.append(Paragraph(
                "Preset(s) applied: <b>" + ", ".join(data.presets_used) + "</b>",
                styles["Body"],
            ))
            story.append(Spacer(1, 6))
        feat_rows = []
        for f in data.features_breakdown:
            feat_rows.append([
                Paragraph(str(f.get("name", "")), cell),
                Paragraph(str(f.get("category", "")), cell),
                f"x{f.get('complexity_weight', 1.0):.1f}",
                "Yes" if f.get("is_new") else "",
                "Yes" if f.get("has_existing_tests") else "No",
                Paragraph(str(f.get("description", "") or ""), cell),
            ])
        story.append(_make_table(
            ["Feature", "Category", "Complexity", "New?", "Existing Tests?", "Description"],
            feat_rows,
            col_widths=[
                page_width * 0.20, page_width * 0.13, page_width * 0.11,
                page_width * 0.08, page_width * 0.13, page_width * 0.35,
            ],
        ))
        story.append(Spacer(1, 12))

    # ── 7. Feasibility analysis ────────────────────────
    story.append(Paragraph("Timeline Feasibility", styles["SectionTitle"]))
    leader_text = " and 1 test leader" if data.has_leader else ""
    feasibility_text = (
        f"With {data.team_size} tester(s){leader_text}, capacity is "
        f"{data.capacity_hours:.0f} hours. The estimated {data.grand_total_hours:.1f} hours "
        f"represents <b>{data.utilization_pct:.1f}%</b> utilization — "
        f"<b>{data.feasibility_status}</b>."
    )
    story.append(Paragraph(feasibility_text, styles["Body"]))
    story.append(Spacer(1, 12))

    # ── 8. Reference projects ──────────────────────────
    if data.reference_projects:
        story.append(Paragraph("Reference Projects", styles["SectionTitle"]))
        ref_rows = []
        for proj in data.reference_projects:
            est = proj.get("estimated_hours", 0) or 0
            act = proj.get("actual_hours", 0) or 0
            ratio = f"{act/est:.2f}" if est > 0 else "N/A"
            ref_rows.append([
                proj.get("project_name", ""),
                proj.get("project_type", ""),
                f"{est:.0f}", f"{act:.0f}", ratio,
            ])
        story.append(_make_table(
            ["Project", "Type", "Est.", "Actual", "Ratio"],
            ref_rows,
        ))
        story.append(Spacer(1, 12))

    # ── 9. Risks ────────────────────────────────────────
    if data.risks_detailed or data.risk_messages:
        story.append(Paragraph("Risks", styles["SectionTitle"]))
        if data.risks_detailed:
            cell = styles["Cell"]
            risk_rows = [
                [
                    Paragraph(str(r.get("name", "")), cell),
                    Paragraph(str(r.get("category", "")), cell),
                    r.get("likelihood", ""),
                    r.get("impact", ""),
                ]
                for r in data.risks_detailed
            ]
            story.append(_make_table(
                ["Risk", "Category", "Likelihood", "Impact"],
                risk_rows,
                col_widths=[
                    page_width * 0.40, page_width * 0.24,
                    page_width * 0.18, page_width * 0.18,
                ],
            ))
            story.append(Spacer(1, 8))
        for msg in data.risk_messages:
            story.append(Paragraph(f"• {msg}", styles["RiskItem"]))

    # Build PDF
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.build(story)
        return None
    else:
        doc.build(story)
        return buffer.getvalue()
