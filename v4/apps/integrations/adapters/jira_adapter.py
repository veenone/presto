"""Jira / Xray integration adapter.

Links estimations to Jira issues and syncs test plans with Xray.
Supports Jira Cloud, Jira Server, and Jira Data Center (DC).

Authentication modes:
  - Cloud: email + API token (Basic Auth)
  - Server/DC: username + password (Basic Auth) OR Personal Access Token (Bearer)
"""

import base64
from typing import Any, Optional

import requests as http_requests

from .base import (
    BaseAdapter,
    ConnectionTestResult,
    ExternalRequest,
    SyncResult,
    SyncStatus,
)


class JiraAdapter(BaseAdapter):
    """Jira REST API adapter supporting Cloud, Server, and Data Center."""

    @property
    def system_name(self) -> str:
        return "JIRA"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.jql_filter = self.additional_config.get("jql_filter", "")
        self.issue_type = self.additional_config.get("issue_type", "")
        self.project_key = self.additional_config.get("project_key", "")
        self.subtask_type = self.additional_config.get("subtask_type", "Sub-task")
        self.task_export_type = self.additional_config.get("task_export_type", "")
        self.xray_enabled = self.additional_config.get("xray_enabled", False)
        self.xray_project_key = self.additional_config.get("xray_project_key", "")
        self.is_cloud = self.additional_config.get("is_cloud", False)

        # SSL verification — DC instances often use self-signed certificates
        ssl_val = self.additional_config.get("ssl_verify", True)
        if isinstance(ssl_val, str):
            self.ssl_verify: bool | str = ssl_val.lower() not in ("false", "0", "no")
        else:
            self.ssl_verify = bool(ssl_val)

        self.timeout = int(self.additional_config.get("timeout", 30))

        # Proxy bypass — internal Jira hosts sitting behind a corporate HTTP
        # proxy fail with a 502 because the proxy won't tunnel (CONNECT) to an
        # internal host. When enabled, force a direct connection for this
        # integration regardless of the server's HTTP(S)_PROXY env vars.
        self.bypass_proxy: bool = self._as_bool(
            self.additional_config.get("bypass_proxy", False)
        )

        # PR / Problem-Report items can use a dedicated token and proxy setting
        # (the PR query may run with a different Jira account than the main
        # integration). Both fall back to the main credentials/proxy when unset.
        self.pr_api_key = self.additional_config.get("pr_api_key", "")
        self.pr_bypass_proxy: bool = self._as_bool(
            self.additional_config.get("pr_bypass_proxy", False)
        )

        # Auth mode: "pat" (Personal Access Token), "basic", or auto-detect
        self.auth_mode = self.additional_config.get("auth_mode", "").lower()

        # Field mappings — support both nested dict and flat UI keys
        field_mappings = self.additional_config.get("field_mappings", {})
        if not field_mappings:
            # Map flat UI keys to internal field_mappings dict
            for ui_key, fm_key in [
                ("effort_hours_field", "effort_hours"),
                ("feasibility_field", "feasibility"),
                ("estimation_number_field", "estimation_number"),
            ]:
                val = self.additional_config.get(ui_key)
                if val:
                    val_str = str(val).strip()
                    if val_str.lower() in ("originalestimate", "time_tracking", "0"):
                        field_mappings[fm_key] = "originalEstimate"
                    else:
                        field_mappings[fm_key] = val_str
        self.field_mappings = field_mappings

    @staticmethod
    def _as_bool(value: Any) -> bool:
        """Coerce a stored config value (bool or string) into a bool."""
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)

    def _auth_header_value(self, api_key: str) -> Optional[str]:
        """Build the ``Authorization`` header value for *api_key* using the
        configured auth mode. Shared by the main and PR-items requests so a
        dedicated PR token is authenticated exactly like the main token.
        """
        if not api_key:
            return None
        if self.is_cloud:
            # Cloud: email + API token as Basic Auth
            creds = base64.b64encode(f"{self.username}:{api_key}".encode()).decode()
            return f"Basic {creds}"
        if self.auth_mode == "basic":
            # DC/Server: explicit Basic Auth (username + password)
            creds = base64.b64encode(f"{self.username}:{api_key}".encode()).decode()
            return f"Basic {creds}"
        if self.auth_mode == "pat":
            # DC/Server: explicit Personal Access Token (Bearer)
            return f"Bearer {api_key}"
        if self.username:
            # Auto-detect: username present → Basic Auth
            creds = base64.b64encode(f"{self.username}:{api_key}".encode()).decode()
            return f"Basic {creds}"
        # Auto-detect: no username → assume PAT (Bearer)
        return f"Bearer {api_key}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = self._auth_header_value(self.api_key)
        if auth:
            headers["Authorization"] = auth
        return headers

    def _pr_headers(self) -> dict[str, str]:
        """Headers for PR-item requests. Uses the dedicated PR token when set,
        otherwise falls back to the main API key.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = self._auth_header_value(self.pr_api_key or self.api_key)
        if auth:
            headers["Authorization"] = auth
        return headers

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/rest/api/2/{path.lstrip('/')}"

    def _request(self, method: str, url: str, **kwargs) -> http_requests.Response:
        """Centralised HTTP call with SSL and timeout defaults."""
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.ssl_verify)
        if self.bypass_proxy:
            # Override any HTTP(S)_PROXY env vars so the request goes direct.
            # trust_env stays on (default), so REQUESTS_CA_BUNDLE / the corp CA
            # chain is still honoured for TLS verification.
            kwargs.setdefault("proxies", {"http": None, "https": None})
        return http_requests.request(method, url, **kwargs)

    def _pr_request(self, method: str, url: str, **kwargs) -> http_requests.Response:
        """HTTP call for PR-item queries — PR token + PR proxy setting.

        Bypasses the proxy when the PR toggle is on, or when the main toggle is
        on (same host, so the main bypass implies PR needs it too).
        """
        kwargs.setdefault("headers", self._pr_headers())
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.ssl_verify)
        if self.pr_bypass_proxy or self.bypass_proxy:
            kwargs.setdefault("proxies", {"http": None, "https": None})
        return http_requests.request(method, url, **kwargs)

    def test_connection(self) -> ConnectionTestResult:
        """Test connection by fetching current user and server info."""
        if not self.base_url:
            return ConnectionTestResult(False, "Jira URL is not configured.")
        if not self.api_key:
            return ConnectionTestResult(False, "Jira API key / token is not configured.")

        auth_desc = "Bearer/PAT" if "Bearer" in self._headers().get("Authorization", "") else "Basic"

        try:
            resp = self._request("GET", self._url("myself"))
        except http_requests.exceptions.SSLError as e:
            hint = (
                "SSL certificate verification failed. "
                "If this Jira instance uses a self-signed certificate, "
                "disable 'Verify SSL' in Deployment Settings."
            )
            return ConnectionTestResult(False, f"{hint}\n\nDetail: {e}")
        except http_requests.exceptions.ConnectTimeout:
            return ConnectionTestResult(
                False,
                f"Connection timed out after {self.timeout}s reaching {self.base_url}. "
                "Check that the URL is reachable from this server.",
            )
        except http_requests.exceptions.ProxyError as e:
            return ConnectionTestResult(
                False,
                f"The HTTP proxy could not reach {self.base_url} "
                "(it failed to tunnel to this host). Internal Jira servers "
                "usually must bypass the corporate proxy: enable 'Bypass proxy' "
                "in this integration's settings, or add the host to the "
                "server's NO_PROXY.\n\n"
                f"Detail: {e}",
            )
        except http_requests.exceptions.ConnectionError as e:
            return ConnectionTestResult(
                False,
                f"Cannot connect to {self.base_url}. "
                "Check the URL, network access, and firewall rules.\n\n"
                f"Detail: {e}",
            )
        except Exception as e:
            return ConnectionTestResult(False, f"Connection failed: {e}")

        if resp.status_code == 401:
            return ConnectionTestResult(
                False,
                f"Authentication failed (HTTP 401) using {auth_desc} auth. "
                "Check your credentials and auth mode setting. "
                "For Jira DC with a Personal Access Token, set Auth Mode to 'pat'.",
            )
        if resp.status_code == 403:
            return ConnectionTestResult(
                False,
                f"Access forbidden (HTTP 403) using {auth_desc} auth. "
                "The credentials may be valid but lack permission for this resource. "
                "Ensure the account/token has appropriate Jira permissions.",
            )
        if resp.status_code == 404:
            return ConnectionTestResult(
                False,
                f"API endpoint not found (HTTP 404) at {self._url('myself')}. "
                "If Jira runs under a context path (e.g. /jira), "
                "include it in the Base URL: https://host/jira",
            )
        if resp.status_code != 200:
            return ConnectionTestResult(
                False, f"HTTP {resp.status_code}: {resp.text[:300]}"
            )

        user = resp.json()
        display = user.get("displayName", user.get("name", "Unknown"))

        # Detect deployment type via serverInfo (DC/Server expose this)
        deploy_type = "Cloud" if self.is_cloud else "Server/DC"
        try:
            info_resp = self._request("GET", self._url("serverInfo"))
            if info_resp.status_code == 200:
                info = info_resp.json()
                version = info.get("version", "")
                dt = info.get("deploymentType", "")
                if dt:
                    deploy_type = dt.replace("_", " ").title()
                if version:
                    deploy_type += f" v{version}"
        except Exception:
            pass

        return ConnectionTestResult(
            True,
            f"Connected as: {display} ({deploy_type}) [auth: {auth_desc}]",
            details={"account_id": user.get("accountId", user.get("key"))},
        )

    def test_pr_connection(self) -> ConnectionTestResult:
        """Verify the PR-items connection using the dedicated PR token.

        Mirrors ``test_connection`` but authenticates with the PR token (or the
        main token when none is set) and honours the PR proxy bypass, so admins
        can validate the Problem-Report credentials independently.
        """
        if not self.base_url:
            return ConnectionTestResult(False, "Jira URL is not configured.")
        if not (self.pr_api_key or self.api_key):
            return ConnectionTestResult(
                False,
                "No PR API key (and no main API key) is configured.",
            )

        using = "PR token" if self.pr_api_key else "main token (no separate PR token set)"
        auth_desc = (
            "Bearer/PAT"
            if "Bearer" in self._pr_headers().get("Authorization", "")
            else "Basic"
        )

        try:
            resp = self._pr_request("GET", self._url("myself"))
        except http_requests.exceptions.SSLError as e:
            return ConnectionTestResult(
                False,
                "SSL certificate verification failed for the PR connection. "
                "If this Jira uses a self-signed certificate, disable 'Verify "
                f"SSL'.\n\nDetail: {e}",
            )
        except http_requests.exceptions.ProxyError as e:
            return ConnectionTestResult(
                False,
                "The HTTP proxy could not reach Jira for the PR connection. "
                "Enable 'PR bypass proxy' for internal hosts, or fix the "
                f"server's NO_PROXY.\n\nDetail: {e}",
            )
        except http_requests.exceptions.ConnectTimeout:
            return ConnectionTestResult(
                False,
                f"PR connection timed out after {self.timeout}s reaching {self.base_url}.",
            )
        except http_requests.exceptions.ConnectionError as e:
            return ConnectionTestResult(
                False,
                f"Cannot connect to {self.base_url} for the PR connection.\n\nDetail: {e}",
            )
        except Exception as e:
            return ConnectionTestResult(False, f"PR connection failed: {e}")

        if resp.status_code == 401:
            return ConnectionTestResult(
                False,
                f"PR authentication failed (HTTP 401) using {auth_desc} auth with the {using}. "
                "Check the PR API key / token and auth mode.",
            )
        if resp.status_code == 403:
            return ConnectionTestResult(
                False,
                f"PR access forbidden (HTTP 403) using the {using}. "
                "The token may lack permission for the PR query.",
            )
        if resp.status_code != 200:
            return ConnectionTestResult(
                False, f"PR connection HTTP {resp.status_code}: {resp.text[:300]}"
            )

        user = resp.json()
        display = user.get("displayName", user.get("name", "Unknown"))
        return ConnectionTestResult(
            True,
            f"PR connection OK as: {display} [auth: {auth_desc}, using {using}]",
            details={"account_id": user.get("accountId", user.get("key"))},
        )

    def import_requests(self) -> SyncResult:
        """Import issues from Jira using JQL filter."""
        if not self.jql_filter:
            return SyncResult(
                system=self.system_name,
                direction="IMPORT",
                status=SyncStatus.FAILED,
                errors=["No JQL filter configured."],
            )

        try:
            resp = self._request(
                "GET",
                self._url("search"),
                params={
                    "jql": self.jql_filter,
                    "maxResults": 100,
                    "fields": "summary,description,reporter,priority,duedate,status,issuetype",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            issues = data.get("issues", [])

            imported: list[ExternalRequest] = []
            errors: list[str] = []

            for issue in issues:
                try:
                    fields = issue.get("fields", {})
                    priority_map = {
                        "Lowest": "LOW",
                        "Low": "LOW",
                        "Medium": "MEDIUM",
                        "High": "HIGH",
                        "Highest": "CRITICAL",
                    }
                    priority_name = (fields.get("priority") or {}).get("name", "Medium")
                    reporter = fields.get("reporter") or {}

                    ext_req = ExternalRequest(
                        external_id=issue["key"],
                        title=fields.get("summary", ""),
                        description=fields.get("description", "") or "",
                        requester_name=reporter.get("displayName", reporter.get("name", "")),
                        requester_email=reporter.get("emailAddress", ""),
                        priority=priority_map.get(priority_name, "MEDIUM"),
                        requested_delivery_date=fields.get("duedate"),
                        raw_data=issue,
                    )
                    imported.append(ext_req)
                except Exception as e:
                    errors.append(f"Issue {issue.get('key', '?')}: {e}")

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
        """Push estimation results to a Jira issue via custom fields."""
        external_id = estimation_data.get("external_id")  # Jira issue key
        if not external_id:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SKIPPED,
                errors=["No external_id (Jira issue key) provided."],
            )

        try:
            update_fields: dict[str, Any] = {}

            fm = self.field_mappings
            effort_hours_val = estimation_data.get("grand_total_hours", 0)

            if "effort_hours" in fm:
                target = fm["effort_hours"]
                if target == "originalEstimate":
                    # Use Jira's built-in time tracking field
                    # originalEstimate expects a Jira duration string, e.g. "511h"
                    hours = float(effort_hours_val or 0)
                    update_fields["timetracking"] = {
                        "originalEstimate": f"{hours:.0f}h",
                    }
                else:
                    update_fields[target] = float(effort_hours_val or 0)
            if "feasibility" in fm:
                # Map feasibility status to a numeric value for Jira number fields
                feasibility_str = estimation_data.get("feasibility_status", "")
                _feasibility_num = {"FEASIBLE": 1, "AT_RISK": 2, "NOT_FEASIBLE": 3}
                if fm["feasibility"].startswith("customfield_"):
                    update_fields[fm["feasibility"]] = _feasibility_num.get(feasibility_str, 0)
                else:
                    update_fields[fm["feasibility"]] = feasibility_str
            if "estimation_number" in fm:
                update_fields[fm["estimation_number"]] = estimation_data.get("estimation_number", "")

            # Add comment with summary
            est_num = estimation_data.get("estimation_number", "N/A")
            total = float(effort_hours_val or 0)
            status = estimation_data.get("feasibility_status", "N/A")

            # Update fields — send each field individually to avoid one bad field blocking all
            errors: list[str] = []
            if update_fields:
                resp = self._request(
                    "PUT",
                    self._url(f"issue/{external_id}"),
                    json={"fields": update_fields},
                )
                if resp.status_code not in (200, 204):
                    # Retry fields individually to identify which one fails
                    for field_key, field_val in update_fields.items():
                        payload = {"fields": {field_key: field_val}}
                        r2 = self._request(
                            "PUT",
                            self._url(f"issue/{external_id}"),
                            json=payload,
                        )
                        if r2.status_code not in (200, 204):
                            errors.append(
                                f"Field {field_key} failed: HTTP {r2.status_code}: {r2.text[:150]}"
                            )

            # Add comment
            comment_body = (
                f"Estimation {est_num} completed.\n"
                f"Total effort: {total:.1f} hours\n"
                f"Feasibility: {status}"
            )
            self._request(
                "POST",
                self._url(f"issue/{external_id}/comment"),
                json={"body": comment_body},
            )

            if errors:
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.PARTIAL,
                    items_processed=1,
                    items_updated=1,
                    errors=errors,
                )
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SUCCESS,
                items_processed=1,
                items_updated=1,
            )
        except Exception as e:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )

    def create_task_breakdown(self, estimation_data: dict) -> SyncResult:
        """Create sub-tasks in Jira for each estimation task.

        If a parent issue (external_id) is provided, tasks are created as
        sub-tasks under it.  Otherwise, tasks are created as standalone issues
        in the configured project_key.
        """
        external_id = estimation_data.get("external_id")  # parent Jira issue key

        tasks = estimation_data.get("tasks", [])
        if not tasks:
            return SyncResult(
                system=self.system_name,
                direction="EXPORT",
                status=SyncStatus.SKIPPED,
                errors=["No tasks to export."],
            )

        try:
            project_key = None
            has_parent = False

            # Try to resolve project key from parent issue
            if external_id:
                try:
                    parent_resp = self._request(
                        "GET", self._url(f"issue/{external_id}?fields=project")
                    )
                    if parent_resp.status_code == 200:
                        project_key = parent_resp.json()["fields"]["project"]["key"]
                        has_parent = True
                except Exception:
                    pass

            # Fallback to configured project_key
            if not project_key:
                project_key = self.project_key

            if not project_key:
                return SyncResult(
                    system=self.system_name,
                    direction="EXPORT",
                    status=SyncStatus.FAILED,
                    errors=[
                        "Cannot determine Jira project. "
                        "Either link the estimation to a Jira issue or "
                        "configure a default Project Key in Jira integration settings."
                    ],
                )

            # Resolve issue type for standalone tasks
            task_type_name = self.task_export_type or self.issue_type
            if not task_type_name:
                # Auto-detect: query project's issue types
                try:
                    meta_resp = self._request(
                        "GET", self._url(f"issue/createmeta?projectKeys={project_key}")
                    )
                    if meta_resp.status_code == 200:
                        projects = meta_resp.json().get("projects", [])
                        if projects:
                            issue_types = projects[0].get("issuetypes", [])
                            # Prefer "Task" or "Todo", skip sub-task types
                            for preferred in ("Task", "Todo", "Story"):
                                for it in issue_types:
                                    if it.get("name") == preferred and not it.get("subtask"):
                                        task_type_name = it["name"]
                                        break
                                if task_type_name:
                                    break
                            # If none matched, use first non-subtask type
                            if not task_type_name:
                                for it in issue_types:
                                    if not it.get("subtask"):
                                        task_type_name = it["name"]
                                        break
                except Exception:
                    pass
                if not task_type_name:
                    task_type_name = "Task"
            est_num = estimation_data.get("estimation_number", "")
            created = 0
            errors: list[str] = []

            for task in tasks:
                task_name = task.get("task_name", task.get("name", "Task"))
                calc_hours = task.get("calculated_hours", 0)
                leader_hours = task.get("leader_hours", 0)
                task_type = task.get("task_type", "")
                total_hours = calc_hours + leader_hours

                summary = f"[{est_num}] {task_name}"
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

                issue_data: dict[str, Any] = {
                    "fields": {
                        "project": {"key": project_key},
                        "summary": summary,
                        "description": "\n".join(desc_lines),
                    }
                }

                if has_parent and external_id:
                    # Create as sub-task under parent
                    issue_data["fields"]["parent"] = {"key": external_id}
                    issue_data["fields"]["issuetype"] = {"name": self.subtask_type}
                else:
                    # Create as standalone task in project
                    issue_data["fields"]["issuetype"] = {"name": task_type_name}

                # Set time tracking if available
                if total_hours > 0:
                    issue_data["fields"]["timetracking"] = {
                        "originalEstimate": f"{total_hours:.0f}h",
                    }

                resp = self._request("POST", self._url("issue"), json=issue_data)
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

    def create_xray_test_plan(self, estimation_data: dict) -> SyncResult:
        """Create an Xray test plan from estimation task breakdown."""
        if not self.xray_enabled:
            return SyncResult(
                system="XRAY",
                direction="EXPORT",
                status=SyncStatus.SKIPPED,
                errors=["Xray integration not enabled."],
            )

        project_key = self.xray_project_key or self.project_key
        if not project_key:
            return SyncResult(
                system="XRAY",
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=["No Xray project key configured."],
            )

        try:
            est_num = estimation_data.get("estimation_number", "")
            project_name = estimation_data.get("project_name", "")
            tasks = estimation_data.get("tasks", [])

            # Create a Test Plan issue
            plan_data = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": f"Test Plan - {est_num}: {project_name}",
                    "description": (
                        f"Auto-generated test plan from estimation {est_num}.\n"
                        f"Total effort: {estimation_data.get('grand_total_hours', 0):.1f} hours\n"
                        f"Tasks: {len(tasks)}"
                    ),
                    "issuetype": {"name": "Test Plan"},
                }
            }

            resp = self._request(
                "POST",
                self._url("issue"),
                json=plan_data,
            )

            if resp.status_code == 201:
                plan_key = resp.json().get("key", "")
                return SyncResult(
                    system="XRAY",
                    direction="EXPORT",
                    status=SyncStatus.SUCCESS,
                    items_processed=1,
                    items_created=1,
                )
            return SyncResult(
                system="XRAY",
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[f"HTTP {resp.status_code}: {resp.text[:200]}"],
            )
        except Exception as e:
            return SyncResult(
                system="XRAY",
                direction="EXPORT",
                status=SyncStatus.FAILED,
                errors=[str(e)],
            )
