"""Redmine integration adapter.

Syncs test requests from Redmine issues and pushes estimation results back.
Uses the Redmine REST API (JSON format).
"""

import json
from typing import Any
from urllib.parse import urljoin

import requests as http_requests

from .base import (
    BaseAdapter,
    ConnectionTestResult,
    ExternalRequest,
    SyncResult,
    SyncStatus,
)


class RedmineAdapter(BaseAdapter):
    """Redmine REST API adapter."""

    @property
    def system_name(self) -> str:
        return "REDMINE"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.project_id = self.additional_config.get("project_id", "")
        self.tracker_id = self.additional_config.get("tracker_id")
        # Support both nested custom_fields dict and flat *_field_id keys from UI
        custom_fields = self.additional_config.get("custom_fields", {})
        if not custom_fields:
            # Map flat UI keys to the expected format
            for ui_key, cf_key in [
                ("effort_hours_field_id", "effort_hours"),
                ("feasibility_field_id", "feasibility"),
                ("estimation_number_field_id", "estimation_number"),
            ]:
                val = self.additional_config.get(ui_key)
                if val:
                    # "estimated_hours" or "0" => use built-in field
                    if str(val).strip().lower() == "estimated_hours" or str(val).strip() == "0":
                        custom_fields[cf_key] = "estimated_hours"
                    else:
                        try:
                            custom_fields[cf_key] = int(val)
                        except (ValueError, TypeError):
                            pass
        self.custom_fields = custom_fields
        self.timeout = int(self.additional_config.get("timeout", 30))

    def _headers(self) -> dict[str, str]:
        return {
            "X-Redmine-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    # HTTP wrappers that inject the per-integration TLS verify setting. They
    # call the module-level http_requests.* (not a Session) so unit tests that
    # patch http_requests.get/post/put keep intercepting. Corp Redmine often
    # sits behind a self-signed / internal-CA chain (ssl_verify=False).
    def _get(self, url: str, **kwargs: Any) -> http_requests.Response:
        kwargs.setdefault("verify", self.ssl_verify)
        return http_requests.get(url, **kwargs)

    def _post(self, url: str, **kwargs: Any) -> http_requests.Response:
        kwargs.setdefault("verify", self.ssl_verify)
        return http_requests.post(url, **kwargs)

    def _put(self, url: str, **kwargs: Any) -> http_requests.Response:
        kwargs.setdefault("verify", self.ssl_verify)
        return http_requests.put(url, **kwargs)

    def test_connection(self) -> ConnectionTestResult:
        """Test connection by fetching current user info."""
        if not self.base_url or not self.api_key:
            return ConnectionTestResult(False, "Redmine URL or API key not configured.")
        try:
            resp = self._get(
                self._url("/users/current.json"),
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                user = resp.json().get("user", {})
                name = f"{user.get('firstname', '')} {user.get('lastname', '')}".strip()
                return ConnectionTestResult(
                    True,
                    f"Connected as: {name or 'Unknown'}",
                    details={"user_id": user.get("id"), "login": user.get("login")},
                )
            return ConnectionTestResult(False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            return ConnectionTestResult(False, f"Connection failed: {e}")

    def import_requests(self) -> SyncResult:
        """Import issues from the configured Redmine project as requests."""
        if not self.project_id:
            return SyncResult(
                system=self.system_name,
                direction="IMPORT",
                status=SyncStatus.FAILED,
                errors=["No Redmine project_id configured."],
            )

        try:
            params: dict[str, Any] = {
                "project_id": self.project_id,
                "status_id": "open",
                "limit": 100,
                "sort": "updated_on:desc",
            }
            if self.tracker_id:
                params["tracker_id"] = self.tracker_id

            resp = self._get(
                self._url("/issues.json"),
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            issues = data.get("issues", [])

            imported: list[ExternalRequest] = []
            errors: list[str] = []

            for issue in issues:
                try:
                    priority_map = {
                        "Low": "LOW",
                        "Normal": "MEDIUM",
                        "High": "HIGH",
                        "Urgent": "CRITICAL",
                        "Immediate": "CRITICAL",
                    }
                    priority_name = issue.get("priority", {}).get("name", "Normal")

                    ext_req = ExternalRequest(
                        external_id=str(issue["id"]),
                        title=issue.get("subject", ""),
                        description=issue.get("description", ""),
                        requester_name=issue.get("author", {}).get("name", ""),
                        priority=priority_map.get(priority_name, "MEDIUM"),
                        requested_delivery_date=issue.get("due_date"),
                        raw_data=issue,
                    )
                    imported.append(ext_req)
                except Exception as e:
                    errors.append(f"Issue #{issue.get('id', '?')}: {e}")

            return SyncResult(
                system=self.system_name,
                direction="IMPORT",
                status=SyncStatus.SUCCESS if not errors else SyncStatus.PARTIAL,
                items_processed=len(issues),
                items_created=len(imported),
                items_failed=len(errors),
                errors=errors,
                imported_items=imported,
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="IMPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )

    def export_estimation(self, estimation_data: dict) -> SyncResult:
        """Push estimation results back to a Redmine issue.

        Updates custom fields on the issue with effort and feasibility data.
        """
        external_id = estimation_data.get("external_id")
        if not external_id:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SKIPPED,
                errors=["No external_id (Redmine issue ID) provided."],
            )

        try:
            update_data: dict[str, Any] = {"issue": {"notes": ""}}
            custom_field_values = []

            # Map estimation fields to Redmine fields
            cf = self.custom_fields
            effort_hours_val = estimation_data.get("grand_total_hours", 0)
            if "effort_hours" in cf:
                effort_cf = cf["effort_hours"]
                if effort_cf == "estimated_hours" or effort_cf == 0:
                    # Use Redmine's built-in estimated_hours field
                    update_data["issue"]["estimated_hours"] = float(effort_hours_val or 0)
                else:
                    custom_field_values.append({
                        "id": effort_cf,
                        "value": str(effort_hours_val or 0),
                    })
            if "feasibility" in cf:
                custom_field_values.append({
                    "id": cf["feasibility"],
                    "value": estimation_data.get("feasibility_status", ""),
                })
            if "estimation_number" in cf:
                custom_field_values.append({
                    "id": cf["estimation_number"],
                    "value": estimation_data.get("estimation_number", ""),
                })

            if custom_field_values:
                update_data["issue"]["custom_fields"] = custom_field_values

            # Add a note with the summary and task breakdown
            est_num = estimation_data.get("estimation_number", "N/A")
            total = estimation_data.get("grand_total_hours", 0)
            status = estimation_data.get("feasibility_status", "N/A")
            note_lines = [
                f"Estimation {est_num} completed.",
                f"Total effort: {total:.1f} hours",
                f"Feasibility: {status}",
            ]

            # Include task breakdown if available
            tasks = estimation_data.get("tasks", [])
            if tasks:
                note_lines.append("")
                note_lines.append("Task Breakdown:")
                for task in tasks:
                    name = task.get("task_name", task.get("name", ""))
                    tester_h = task.get("calculated_hours", 0)
                    leader_h = task.get("leader_hours", 0)
                    note_lines.append(f"- {name}: {tester_h:.1f} tester hrs, {leader_h:.1f} leader hrs")

            update_data["issue"]["notes"] = "\n".join(note_lines)

            resp = self._put(
                self._url(f"/issues/{external_id}.json"),
                headers=self._headers(),
                json=update_data,
                timeout=self.timeout,
            )

            if resp.status_code in (200, 204):
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.SUCCESS,
                    items_processed=1,
                    items_updated=1,
                )
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[f"HTTP {resp.status_code}: {resp.text[:200]}"],
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )

    def create_task_breakdown(self, estimation_data: dict) -> SyncResult:
        """Create sub-tasks in Redmine for each estimation task.

        If a parent issue (external_id) is provided, tasks are created as
        child issues.  Otherwise, tasks are created as standalone issues
        in the configured project_id.
        """
        external_id = estimation_data.get("external_id")  # parent Redmine issue ID

        tasks = estimation_data.get("tasks", [])
        if not tasks:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SKIPPED,
                errors=["No tasks to export."],
            )

        try:
            project_id = None
            has_parent = False
            subtask_tracker_id = self.additional_config.get("subtask_tracker_id")

            # Try to resolve project from parent issue
            if external_id:
                try:
                    parent_resp = self._get(
                        self._url(f"/issues/{external_id}.json"),
                        headers=self._headers(),
                        timeout=self.timeout,
                    )
                    if parent_resp.status_code == 200:
                        parent_data = parent_resp.json().get("issue", {})
                        project_id = parent_data.get("project", {}).get("id")
                        has_parent = True
                        if not subtask_tracker_id:
                            subtask_tracker_id = parent_data.get("tracker", {}).get("id")
                except Exception:
                    pass

            # Fallback to configured project_id
            if not project_id:
                project_id = self.project_id

            if not project_id:
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.FAILED,
                    errors=[
                        "Cannot determine Redmine project. "
                        "Either link the estimation to a Redmine issue or "
                        "configure a Project ID in Redmine integration settings."
                    ],
                )

            if not subtask_tracker_id:
                subtask_tracker_id = self.tracker_id

            est_num = estimation_data.get("estimation_number", "")
            created = 0
            errors: list[str] = []

            for task in tasks:
                task_name = task.get("task_name", task.get("name", "Task"))
                calc_hours = task.get("calculated_hours", 0)
                leader_hours = task.get("leader_hours", 0)
                task_type = task.get("task_type", "")
                total_hours = calc_hours + leader_hours

                subject = f"[{est_num}] {task_name}"
                desc_lines = [
                    f"Task Type: {task_type}",
                    f"Tester Hours: {calc_hours:.1f}h",
                    f"Leader Hours: {leader_hours:.1f}h",
                    f"Total: {total_hours:.1f}h",
                ]
                if task.get("is_new_feature_study"):
                    desc_lines.append("(New Feature Study)")
                if task.get("notes"):
                    desc_lines.append(f"Notes: {task['notes']}")

                issue_payload: dict[str, Any] = {
                    "issue": {
                        "project_id": project_id,
                        "subject": subject,
                        "description": "\n".join(desc_lines),
                        "estimated_hours": total_hours,
                    }
                }
                if has_parent and external_id:
                    issue_payload["issue"]["parent_issue_id"] = int(external_id)
                if subtask_tracker_id:
                    issue_payload["issue"]["tracker_id"] = int(subtask_tracker_id)

                resp = self._post(
                    self._url("/issues.json"),
                    headers=self._headers(),
                    json=issue_payload,
                    timeout=self.timeout,
                )
                if resp.status_code == 201:
                    created += 1
                else:
                    errors.append(f"Failed to create '{task_name}': HTTP {resp.status_code}: {resp.text[:150]}")

            if errors and created == 0:
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.FAILED,
                    items_processed=len(tasks),
                    items_created=created,
                    errors=errors,
                )
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SUCCESS if not errors else SyncStatus.PARTIAL,
                items_processed=len(tasks),
                items_created=created,
                errors=errors,
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )

    def upload_attachment(self, issue_id: str, filename: str, content: bytes) -> SyncResult:
        """Upload an attachment (report) to a Redmine issue."""
        try:
            # Step 1: Upload the file
            upload_resp = self._post(
                self._url("/uploads.json"),
                headers={
                    "X-Redmine-API-Key": self.api_key,
                    "Content-Type": "application/octet-stream",
                },
                data=content,
                timeout=self.timeout,
            )
            upload_resp.raise_for_status()
            token = upload_resp.json()["upload"]["token"]

            # Step 2: Attach to issue
            attach_resp = self._put(
                self._url(f"/issues/{issue_id}.json"),
                headers=self._headers(),
                json={
                    "issue": {
                        "uploads": [
                            {"token": token, "filename": filename, "content_type": "application/pdf"}
                        ]
                    }
                },
                timeout=self.timeout,
            )

            if attach_resp.status_code in (200, 204):
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.SUCCESS,
                    items_processed=1,
                    items_created=1,
                )
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[f"Attach failed: HTTP {attach_resp.status_code}"],
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )

    def update_assignee(self, external_id: str, assignee_login: str) -> SyncResult:
        """Update the assigned_to on a Redmine issue by resolving a username to a Redmine user ID."""
        try:
            # Resolve login to Redmine user ID
            resp = self._get(
                self._url("/users.json"),
                headers=self._headers(),
                params={"name": assignee_login, "limit": 5},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            users = resp.json().get("users", [])

            redmine_user_id = None
            for u in users:
                if u.get("login", "").lower() == assignee_login.lower():
                    redmine_user_id = u["id"]
                    break
            if not redmine_user_id and users:
                redmine_user_id = users[0]["id"]

            if not redmine_user_id:
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.FAILED,
                    errors=[f"Redmine user not found for login '{assignee_login}'"],
                )

            # Update issue assignee
            update_resp = self._put(
                self._url(f"/issues/{external_id}.json"),
                headers=self._headers(),
                json={"issue": {"assigned_to_id": redmine_user_id}},
                timeout=self.timeout,
            )
            if update_resp.status_code in (200, 204):
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.SUCCESS,
                    items_processed=1,
                    items_updated=1,
                )
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[f"HTTP {update_resp.status_code}: {update_resp.text[:200]}"],
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )
