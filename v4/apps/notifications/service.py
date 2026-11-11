"""Notification service for sending email alerts on key events."""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from apps.accounts.models import User
from apps.core.models import Configuration

from . import templates


class NotificationService:
    """Sends email notifications for estimation events.

    Reads SMTP settings from the ``configuration`` table at call time so
    that changes made through the UI take effect without restarting the
    application.

    Configuration keys consumed:
        - ``smtp_host``      – SMTP server hostname (required to enable sending)
        - ``smtp_port``      – SMTP port, defaults to ``587``
        - ``smtp_user``      – Login username
        - ``smtp_password``  – Login password
        - ``smtp_from``      – Sender address; falls back to ``smtp_user``
        - ``smtp_tls``       – ``"true"`` / ``"false"``; defaults to ``"true"``
    """

    def __init__(self, session: object | None = None) -> None:
        # ``session`` is accepted (and ignored) for call-site compatibility with v3.
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_config(self, key: str, default: str = "") -> str:
        value = Configuration.objects.filter(key=key).values_list("value", flat=True).first()
        return value if value is not None else default

    @property
    def is_configured(self) -> bool:
        """Return True when an SMTP host has been set in configuration."""
        return bool(self._get_config("smtp_host"))

    def test_connection(self) -> tuple[bool, str]:
        """Open an SMTP connection (and login if credentials are set) to verify
        the stored configuration. Returns ``(success, message)`` with the real
        error so the Settings UI can surface it, unlike ``_send_email`` which
        swallows failures so notifications never break business operations.
        """
        if not self.is_configured:
            return False, "SMTP is not configured. Set smtp_host in Settings and save."

        smtp_host = self._get_config("smtp_host")
        try:
            smtp_port = int(self._get_config("smtp_port", "587") or "587")
        except ValueError:
            smtp_port = 587
        smtp_user = self._get_config("smtp_user")
        smtp_password = self._get_config("smtp_password")
        smtp_tls = self._get_config("smtp_tls", "true").lower() == "true"

        try:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            if smtp_tls:
                server.starttls()
            authed = bool(smtp_user and smtp_password)
            if authed:
                server.login(smtp_user, smtp_password)
            server.quit()
            suffix = " and authenticated" if authed else " (no auth credentials set)"
            return True, f"Connected to {smtp_host}:{smtp_port}{suffix}."
        except Exception as exc:
            return False, f"SMTP connection failed: {exc}"

    def _send_email(
        self, to_emails: list[str], subject: str, html_body: str
    ) -> bool:
        """Construct and deliver an HTML email via SMTP.

        Returns True on success, False on any failure (misconfiguration,
        network error, auth error, etc.).  Errors are silently swallowed
        so that a notification failure never breaks a business operation.
        """
        if not self.is_configured:
            return False

        smtp_host = self._get_config("smtp_host")
        smtp_port = int(self._get_config("smtp_port", "587"))
        smtp_user = self._get_config("smtp_user")
        smtp_password = self._get_config("smtp_password")
        smtp_from = self._get_config("smtp_from", smtp_user)
        smtp_tls = self._get_config("smtp_tls", "true").lower() == "true"

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_from
            msg["To"] = ", ".join(to_emails)
            msg.attach(MIMEText(html_body, "html"))

            server = smtplib.SMTP(smtp_host, smtp_port)
            if smtp_tls:
                server.starttls()

            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)

            server.sendmail(smtp_from, to_emails, msg.as_string())
            server.quit()
            return True
        except Exception:
            return False

    def _get_admin_emails(self) -> list[str]:
        """Return email addresses of all active ADMIN users."""
        admins = User.objects.filter(role="ADMIN", is_active=True, email__isnull=False)
        return [a.email for a in admins if a.email]

    def _get_user_email(self, user_id: int | None) -> list[str]:
        """Return a single-element list with the user's email, or empty."""
        if not user_id:
            return []
        user = User.objects.filter(pk=user_id).first()
        return [user.email] if user and user.email else []

    # ------------------------------------------------------------------
    # Public notification methods
    # ------------------------------------------------------------------

    def notify_estimation_status_changed(
        self,
        estimation_number: str,
        project_name: str,
        old_status: str,
        new_status: str,
        changed_by: str,
        grand_total_hours: float = 0,
        creator_user_id: int | None = None,
        assigned_user_id: int | None = None,
    ) -> bool:
        """Notify creator, assignee, and (on FINAL) all admins of a status change.

        Args:
            estimation_number: Human-readable estimation identifier.
            project_name:      Display name of the project.
            old_status:        Previous status string.
            new_status:        Incoming status string.
            changed_by:        Display name of the actor making the change.
            grand_total_hours: Calculated total hours for the estimation.
            creator_user_id:   PK of the user who created the estimation.
            assigned_user_id:  PK of the currently assigned user.

        Returns:
            True if the email was dispatched successfully, False otherwise.
        """
        recipients: set[str] = set()
        recipients.update(self._get_user_email(creator_user_id))
        recipients.update(self._get_user_email(assigned_user_id))
        if new_status == "FINAL":
            recipients.update(self._get_admin_emails())

        if not recipients:
            return False

        subject, body = templates.estimation_status_changed(
            estimation_number,
            project_name,
            old_status,
            new_status,
            changed_by,
            grand_total_hours,
        )
        return self._send_email(list(recipients), subject, body)

    def notify_feature_promotion_requested(
        self,
        feature_name: str,
        category: str | None,
        requested_by: str,
    ) -> bool:
        """Notify approvers/admins that a project feature awaits promotion."""
        recipients = [
            u.email
            for u in User.objects.filter(
                role__in=["APPROVER", "ADMIN"], is_active=True, email__isnull=False,
            )
            if u.email
        ]
        if not recipients:
            return False
        cat = category or "(no category)"
        subject = f"Feature promotion requested: {feature_name}"
        body = (
            f"<p><b>{requested_by}</b> requested adding the project feature "
            f"<b>{feature_name}</b> (category: {cat}) to the global catalog.</p>"
            f"<p>Open the estimation's Features step and use "
            f"<b>Promote to global</b> to approve the request.</p>"
        )
        return self._send_email(recipients, subject, body)

    def notify_user_assigned(
        self,
        estimation_number: str,
        project_name: str,
        assignee_user_id: int,
        assigned_by: str,
    ) -> bool:
        """Notify a user that they have been assigned to an estimation.

        Args:
            estimation_number: Human-readable estimation identifier.
            project_name:      Display name of the project.
            assignee_user_id:  PK of the user being assigned.
            assigned_by:       Display name of the actor making the assignment.

        Returns:
            True if the email was dispatched successfully, False otherwise.
        """
        emails = self._get_user_email(assignee_user_id)
        if not emails:
            return False

        assignee = User.objects.filter(pk=assignee_user_id).first()
        assignee_name = assignee.display_name if assignee else "User"

        subject, body = templates.user_assigned(
            estimation_number, project_name, assignee_name, assigned_by
        )
        return self._send_email(emails, subject, body)

    def notify_request_imported(
        self,
        request_number: str,
        title: str,
        source: str,
        requester_name: str,
    ) -> bool:
        """Notify all admin users that a new request has been imported.

        Args:
            request_number: Identifier from the external system.
            title:          Short description of the request.
            source:         Integration source (e.g. "Redmine", "JIRA").
            requester_name: Name of the person who raised the request.

        Returns:
            True if the email was dispatched successfully, False otherwise.
        """
        recipients = self._get_admin_emails()
        if not recipients:
            return False

        subject, body = templates.request_imported(
            request_number, title, source, requester_name
        )
        return self._send_email(recipients, subject, body)

    def notify_deadline_approaching(
        self,
        estimation_number: str,
        project_name: str,
        delivery_date: str,
        days_remaining: int,
        creator_user_id: int | None = None,
        assigned_user_id: int | None = None,
    ) -> bool:
        """Notify creator and assignee that a delivery deadline is near.

        Args:
            estimation_number: Human-readable estimation identifier.
            project_name:      Display name of the project.
            delivery_date:     ISO-8601 date string of the deadline.
            days_remaining:    Calendar days until the deadline.
            creator_user_id:   PK of the user who created the estimation.
            assigned_user_id:  PK of the currently assigned user.

        Returns:
            True if the email was dispatched successfully, False otherwise.
        """
        recipients: set[str] = set()
        recipients.update(self._get_user_email(creator_user_id))
        recipients.update(self._get_user_email(assigned_user_id))

        if not recipients:
            return False

        subject, body = templates.deadline_approaching(
            estimation_number, project_name, delivery_date, days_remaining
        )
        return self._send_email(list(recipients), subject, body)


# ── In-app notifications (bell, notifications page) ──────────────────────────
# Same rules as the bell views and /api/notifications: a user only ever sees
# or changes their own rows.

def user_notifications(user, unread_only: bool = False):
    """The user's in-app notifications, newest first."""
    from .models import WebhookNotification

    qs = WebhookNotification.objects.filter(user=user)
    return qs.filter(is_read=False) if unread_only else qs


def mark_notification_read(user, notification_id: int) -> bool:
    """Mark one of the user's notifications read; False when it is not theirs or does not exist."""
    return bool(user_notifications(user).filter(pk=notification_id).update(is_read=True))


def mark_all_notifications_read(user) -> int:
    return user_notifications(user, unread_only=True).update(is_read=True)
