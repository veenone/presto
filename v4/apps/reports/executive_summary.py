"""One-page executive summary PDF report."""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .templates import ExecutiveSummaryData


def _feasibility_color(status: str) -> colors.Color:
    return {
        "FEASIBLE": colors.HexColor("#198754"),
        "AT_RISK": colors.HexColor("#ffc107"),
        "NOT_FEASIBLE": colors.HexColor("#dc3545"),
    }.get(status, colors.grey)


def _risk_flag_label(flag: str) -> str:
    labels = {
        "high_new_feature_ratio": "High proportion of new features (>50%)",
        "no_reference_projects": "No reference projects for baseline",
        "compressed_timeline": "Compressed timeline (<2 weeks)",
        "high_matrix_complexity": "High DUT×Profile complexity (>20 combos)",
        "historical_underestimate": "Historical tendency to underestimate (>1.3x)",
    }
    return labels.get(flag, flag)


def generate_executive_summary(data: ExecutiveSummaryData) -> bytes:
    """Generate a one-page executive summary PDF."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=2 * cm, rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"],
        fontSize=18, textColor=colors.HexColor("#2F5496"),
        spaceAfter=6 * mm,
    )
    heading_style = ParagraphStyle(
        "SectionHead", parent=styles["Heading2"],
        fontSize=12, textColor=colors.HexColor("#2F5496"),
        spaceBefore=4 * mm, spaceAfter=2 * mm,
    )
    body_style = styles["Normal"]
    small_style = ParagraphStyle(
        "Small", parent=styles["Normal"], fontSize=8, textColor=colors.grey,
    )

    elements = []

    # Confidentiality header
    elements.append(Paragraph(data.metadata.confidentiality_notice, small_style))
    elements.append(Spacer(1, 4 * mm))

    # Title
    elements.append(Paragraph("Executive Summary", title_style))
    elements.append(Paragraph(
        f"<b>{data.estimation_number}</b> — {data.project_name}", body_style
    ))
    elements.append(Spacer(1, 3 * mm))

    # Project overview table
    elements.append(Paragraph("Project Overview", heading_style))
    overview_data = [
        ["Project Name", data.project_name, "Project Type", data.project_type],
        ["Estimation #", data.estimation_number, "Created By", data.created_by or "—"],
        ["Created At", data.created_at, "DUT Count", str(data.dut_count)],
        ["Profile Count", str(data.profile_count), "Combinations", str(data.dut_profile_combinations)],
    ]
    overview_table = Table(overview_data, colWidths=[3.5 * cm, 5 * cm, 3.5 * cm, 5 * cm])
    overview_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F0F4F8")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F0F4F8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(overview_table)
    elements.append(Spacer(1, 4 * mm))

    # Effort breakdown
    elements.append(Paragraph("Effort Breakdown", heading_style))
    feas_color = _feasibility_color(data.feasibility_status)

    effort_data = [
        ["Category", "Hours", "% of Total"],
        ["Tester Effort", f"{data.total_tester_hours:.1f}", f"{data.total_tester_hours / data.grand_total_hours * 100:.0f}%" if data.grand_total_hours else "0%"],
        ["Leader Effort", f"{data.total_leader_hours:.1f}", f"{data.total_leader_hours / data.grand_total_hours * 100:.0f}%" if data.grand_total_hours else "0%"],
        ["PR Fix Effort", f"{data.pr_fix_hours:.1f}", f"{data.pr_fix_hours / data.grand_total_hours * 100:.0f}%" if data.grand_total_hours else "0%"],
        ["Study Effort", f"{data.study_hours:.1f}", f"{data.study_hours / data.grand_total_hours * 100:.0f}%" if data.grand_total_hours else "0%"],
        ["Buffer", f"{data.buffer_hours:.1f}", f"{data.buffer_hours / data.grand_total_hours * 100:.0f}%" if data.grand_total_hours else "0%"],
        ["Grand Total", f"{data.grand_total_hours:.1f}", "100%"],
    ]
    effort_table = Table(effort_data, colWidths=[6 * cm, 4 * cm, 4 * cm])
    effort_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8ECF0")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(effort_table)
    elements.append(Spacer(1, 4 * mm))

    # Feasibility
    elements.append(Paragraph("Feasibility Assessment", heading_style))
    feas_data = [
        ["Status", data.feasibility_status],
        ["Grand Total", f"{data.grand_total_hours:.1f} hours ({data.grand_total_days:.1f} days)"],
        ["Capacity", f"{data.capacity_hours:.1f} hours"],
        ["Utilization", f"{data.utilization_pct:.1f}%"],
    ]
    feas_table = Table(feas_data, colWidths=[5 * cm, 12 * cm])
    feas_style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("BACKGROUND", (1, 0), (1, 0), feas_color),
        ("TEXTCOLOR", (1, 0), (1, 0), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    feas_table.setStyle(TableStyle(feas_style))
    elements.append(feas_table)

    # Risk flags
    if data.risk_flags:
        elements.append(Spacer(1, 3 * mm))
        elements.append(Paragraph("Risk Flags", heading_style))
        for flag in data.risk_flags:
            elements.append(Paragraph(
                f'<font color="#dc3545">&#9888;</font> {_risk_flag_label(flag)}',
                body_style,
            ))

    # Footer
    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph(
        f"Generated by {data.metadata.generated_by} on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        small_style,
    ))

    doc.build(elements)
    return buf.getvalue()
