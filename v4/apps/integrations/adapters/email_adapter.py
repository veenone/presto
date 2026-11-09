"""Email integration adapter.

Handles sending estimation reports via SMTP and optionally
receiving requests via IMAP.
"""

import smtplib
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .base import (
    BaseAdapter,
    ConnectionTestResult,
    SyncResult,
    SyncStatus,
)


class EmailAdapter(BaseAdapter):
    """Email adapter for sending reports and notifications."""

    @property
    def system_name(self) -> str:
        return "EMAIL"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.smtp_host = self.additional_config.get("smtp_host", "")
        self.smtp_port = int(self.additional_config.get("smtp_port", 587))
        self.smtp_use_tls = self.additional_config.get("smtp_use_tls", True)
        self.smtp_username = self.username or self.additional_config.get("smtp_username", "")
        self.smtp_password = self.api_key or self.additional_config.get("smtp_password", "")
        self.sender_email = self.additional_config.get("sender_email", self.smtp_username)
        self.sender_name = self.additional_config.get("sender_name", "Estimation Tool")

    def test_connection(self) -> ConnectionTestResult:
        """Test SMTP connection."""
        if not self.smtp_host:
            return ConnectionTestResult(False, "SMTP host not configured.")
        try:
            if self.smtp_use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                    server.ehlo()
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
            return ConnectionTestResult(True, "SMTP connection successful.")
        except Exception as e:
            return ConnectionTestResult(False, f"SMTP connection failed: {e}")

    def send_report(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> SyncResult:
        """Send an estimation report via email.

        Args:
            to_email: Recipient email address.
            subject: Email subject line.
            body_html: HTML body content.
            attachments: List of (filename, content_bytes, mime_type) tuples.
        """
        if not self.smtp_host:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=["SMTP not configured."],
            )

        try:
            msg = MIMEMultipart()
            msg["From"] = f"{self.sender_name} <{self.sender_email}>"
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body_html, "html"))

            if attachments:
                for filename, content, mime_type in attachments:
                    part = MIMEBase(*mime_type.split("/", 1))
                    part.set_payload(content)
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={filename}")
                    msg.attach(part)

            if self.smtp_use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                    server.ehlo()
                    if self.smtp_username and self.smtp_password:
                        server.login(self.smtp_username, self.smtp_password)
                    server.send_message(msg)

            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SUCCESS,
                items_processed=1,
                items_created=1,
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )

    def send_estimation_report(
        self,
        to_email: str,
        estimation_number: str,
        project_name: str,
        grand_total_hours: float,
        feasibility_status: str,
        report_bytes: bytes | None = None,
        report_filename: str = "estimation_report.pdf",
    ) -> SyncResult:
        """Send a formatted estimation report email."""
        status_color = {
            "FEASIBLE": "#28a745",
            "AT_RISK": "#ffc107",
            "NOT_FEASIBLE": "#dc3545",
        }.get(feasibility_status, "#6c757d")

        body_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #2F5496;">Test Effort Estimation Report</h2>
            <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Estimation:</td>
                    <td style="padding: 8px;">{estimation_number}</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 8px; font-weight: bold;">Project:</td>
                    <td style="padding: 8px;">{project_name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; font-weight: bold;">Total Effort:</td>
                    <td style="padding: 8px;">{grand_total_hours:.1f} hours</td>
                </tr>
                <tr style="background: #f8f9fa;">
                    <td style="padding: 8px; font-weight: bold;">Feasibility:</td>
                    <td style="padding: 8px;">
                        <span style="color: {status_color}; font-weight: bold;">
                            {feasibility_status}
                        </span>
                    </td>
                </tr>
            </table>
            <p style="margin-top: 20px; color: #666; font-size: 12px;">
                This report was generated by the Test Effort Estimation Tool.
            </p>
        </body>
        </html>
        """

        attachments = None
        if report_bytes:
            mime = "application/pdf" if report_filename.endswith(".pdf") else "application/octet-stream"
            attachments = [(report_filename, report_bytes, mime)]

        return self.send_report(
            to_email=to_email,
            subject=f"Estimation Report: {estimation_number} - {project_name}",
            body_html=body_html,
            attachments=attachments,
        )

    def import_requests(self) -> SyncResult:
        """Email import is not yet implemented (requires IMAP)."""
        return SyncResult(
            system=self.system_name,
            direction="IMPORT",
            status=SyncStatus.SKIPPED,
            errors=["Email import (IMAP) not yet implemented."],
        )

    def export_estimation(self, estimation_data: dict) -> SyncResult:
        """Export estimation via email."""
        to_email = estimation_data.get("requester_email")
        if not to_email:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SKIPPED,
                errors=["No requester email provided."],
            )
        return self.send_estimation_report(
            to_email=to_email,
            estimation_number=estimation_data.get("estimation_number", ""),
            project_name=estimation_data.get("project_name", ""),
            grand_total_hours=estimation_data.get("grand_total_hours", 0),
            feasibility_status=estimation_data.get("feasibility_status", ""),
            report_bytes=estimation_data.get("report_bytes"),
            report_filename=estimation_data.get("report_filename", "report.pdf"),
        )
