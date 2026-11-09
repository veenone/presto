"""HTML email templates for notification events."""


def _base_template(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
    <div style="background: #2F5496; color: white; padding: 15px 20px; border-radius: 4px 4px 0 0;">
        <h2 style="margin: 0;">{title}</h2>
    </div>
    <div style="border: 1px solid #ddd; border-top: none; padding: 20px; border-radius: 0 0 4px 4px;">
        {body}
    </div>
    <div style="text-align: center; padding: 10px; color: #999; font-size: 12px;">
        Test Effort Estimation Tool — Automated Notification
    </div>
</body>
</html>"""


def _status_color(status: str) -> str:
    colors = {
        "DRAFT": "#6c757d",
        "FINAL": "#0d6efd",
        "APPROVED": "#198754",
        "REVISED": "#fd7e14",
        "FEASIBLE": "#198754",
        "AT_RISK": "#ffc107",
        "NOT_FEASIBLE": "#dc3545",
    }
    return colors.get(status, "#6c757d")


def estimation_status_changed(
    estimation_number: str,
    project_name: str,
    old_status: str,
    new_status: str,
    changed_by: str,
    grand_total_hours: float = 0,
) -> tuple[str, str]:
    """Returns (subject, html_body) for estimation status change."""
    subject = f"Estimation {estimation_number} — Status: {new_status}"
    body = f"""
    <p>The estimation <strong>{estimation_number}</strong> for project <strong>{project_name}</strong> has been updated.</p>
    <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Previous Status</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">
                <span style="color: {_status_color(old_status)}; font-weight: bold;">{old_status}</span>
            </td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>New Status</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">
                <span style="color: {_status_color(new_status)}; font-weight: bold;">{new_status}</span>
            </td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Grand Total</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{grand_total_hours:.1f} hours</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Changed By</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{changed_by}</td>
        </tr>
    </table>
    """
    return subject, _base_template("Estimation Status Update", body)


def user_assigned(
    estimation_number: str,
    project_name: str,
    assignee_name: str,
    assigned_by: str,
) -> tuple[str, str]:
    """Returns (subject, html_body) for user assignment notification."""
    subject = f"You have been assigned to estimation {estimation_number}"
    body = f"""
    <p>Hello <strong>{assignee_name}</strong>,</p>
    <p>You have been assigned to work on estimation <strong>{estimation_number}</strong>
       for project <strong>{project_name}</strong> by <strong>{assigned_by}</strong>.</p>
    <p>Please review the estimation details in the application.</p>
    """
    return subject, _base_template("New Assignment", body)


def request_imported(
    request_number: str,
    title: str,
    source: str,
    requester_name: str,
) -> tuple[str, str]:
    """Returns (subject, html_body) for request import notification."""
    subject = f"New request imported: {request_number}"
    body = f"""
    <p>A new test request has been imported from <strong>{source}</strong>.</p>
    <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Request #</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{request_number}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Title</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{title}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Requester</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{requester_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Source</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{source}</td>
        </tr>
    </table>
    """
    return subject, _base_template("New Request Imported", body)


def deadline_approaching(
    estimation_number: str,
    project_name: str,
    delivery_date: str,
    days_remaining: int,
) -> tuple[str, str]:
    """Returns (subject, html_body) for deadline approaching notification."""
    urgency = "URGENT: " if days_remaining <= 3 else ""
    subject = f"{urgency}Estimation {estimation_number} — Delivery in {days_remaining} days"
    body = f"""
    <p>The estimation <strong>{estimation_number}</strong> for project <strong>{project_name}</strong>
       has an approaching deadline.</p>
    <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Delivery Date</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{delivery_date}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Days Remaining</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">
                <span style="color: {'#dc3545' if days_remaining <= 3 else '#ffc107'}; font-weight: bold;">
                    {days_remaining} days
                </span>
            </td>
        </tr>
    </table>
    <p>Please ensure the estimation is finalized before the delivery date.</p>
    """
    return subject, _base_template("Deadline Approaching", body)
