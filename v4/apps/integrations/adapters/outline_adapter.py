"""Outline wiki integration adapter.

Exports estimation summaries as wiki documents and imports context from Outline.
"""

import re
from datetime import datetime

import requests

from .base import (
    BaseAdapter,
    ConnectionTestResult,
    SyncDirection,
    SyncResult,
    SyncStatus,
)


class OutlineAdapter(BaseAdapter):
    """Adapter for Outline wiki (self-hosted, REST API with Bearer token).

    Outline does not expose requests to import, so ``import_requests`` always
    returns a SKIPPED result.  ``export_estimation`` creates or updates a wiki
    document in the configured collection.

    Configuration keys (passed via ``config`` dict):
        base_url (str): Outline instance root URL, e.g. ``https://wiki.example.com``.
        api_key (str): Personal access token with read/write scope.
        collection_id (str, optional): UUID of the target collection.  When
            omitted the document is created without a collection (Outline
            places it in the workspace root).

    Example::

        adapter = OutlineAdapter({
            "base_url": "https://wiki.example.com",
            "api_key": "my-secret-token",
            "additional_config": {"collection_id": "abc-123"},
        })
        result = adapter.test_connection()
    """

    # Regex for a standard UUID (v4 or any variant).
    _UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.base_url = self.base_url.rstrip("/")
        self.timeout: int = int(self.additional_config.get("timeout", 30))
        # One session so ssl_verify applies to every request — corp Outline
        # commonly sits behind a self-signed CA chain. Must be set before
        # _resolve_collection_id below, which issues a request.
        self._session = requests.Session()
        self._session.verify = self.ssl_verify
        raw_collection = self.additional_config.get("collection_id", "")
        self.parent_document_id: str = ""
        self.collection_id: str = self._resolve_collection_id(raw_collection)

    # ------------------------------------------------------------------
    # BaseAdapter interface
    # ------------------------------------------------------------------

    @property
    def system_name(self) -> str:
        """Human-readable name used in SyncResult records."""
        return "OUTLINE"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def test_connection(self) -> ConnectionTestResult:
        """Verify connectivity by listing collections.

        Returns:
            ConnectionTestResult with ``success=True`` when the API responds
            with HTTP 200, or ``success=False`` with an error message otherwise.
        """
        try:
            resp = self._session.post(
                f"{self.base_url}/api/collections.list",
                headers=self._headers,
                json={},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                collections = resp.json().get("data", [])
                details: dict = {
                    "collections": [
                        {"name": c.get("name"), "id": c.get("id")}
                        for c in collections[:10]
                    ],
                }
                msg = f"Connected. Found {len(collections)} collection(s)."
                if self.collection_id:
                    matched = next(
                        (c for c in collections if c.get("id") == self.collection_id),
                        None,
                    )
                    if matched:
                        perm = matched.get("permission")
                        msg += f" Target collection: {matched.get('name')!r}."
                        details["resolved_collection"] = {
                            "id": self.collection_id,
                            "name": matched.get("name"),
                            "permission": perm,
                        }
                        if perm not in ("read_write",):
                            msg += (
                                f" WARNING: Your API key has '{perm or 'no'}'"
                                f" permission on this collection — sync will"
                                f" fail. Grant read_write access in Outline or"
                                f" choose a different collection."
                            )
                            return ConnectionTestResult(
                                success=False, message=msg, details=details,
                            )
                    else:
                        msg += f" Warning: collection_id {self.collection_id!r} not found."
                if self.parent_document_id:
                    msg += f" Documents will be created as children of parent document {self.parent_document_id[:8]}…"
                    details["parent_document_id"] = self.parent_document_id
                return ConnectionTestResult(
                    success=True,
                    message=msg,
                    details=details,
                )
            return ConnectionTestResult(
                success=False,
                message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                details={},
            )
        except requests.RequestException as exc:
            return ConnectionTestResult(
                success=False, message=str(exc), details={}
            )

    def import_requests(self) -> SyncResult:
        """Not applicable for Outline — always returns SKIPPED.

        Outline is a write-only target for this tool.  Importing requests from
        a generic wiki is not supported.

        Returns:
            SyncResult with ``status=SyncStatus.SKIPPED``.
        """
        return SyncResult(
            system=self.system_name,
            direction=SyncDirection.IMPORT.value,
            status=SyncStatus.SKIPPED,
            items_processed=0,
            items_created=0,
            items_updated=0,
            items_failed=0,
            errors=["Import is not supported for the Outline wiki adapter."],
        )

    def export_estimation(self, estimation_data: dict) -> SyncResult:
        """Create or update an Outline wiki page with the estimation summary.

        Searches for an existing document whose title contains the estimation
        number.  If found, the document is updated in-place; otherwise a new
        document is created in the configured collection.

        Args:
            estimation_data: Dictionary produced by the estimation engine,
                expected keys: ``estimation_number``, ``project_name``,
                ``project_type``, ``status``, ``feasibility_status``,
                ``total_tester_hours``, ``total_leader_hours``,
                ``grand_total_hours``, ``grand_total_days``, ``dut_count``,
                ``profile_count``, ``dut_profile_combinations``,
                ``pr_fix_count``, ``created_at``, and optionally ``tasks``.

        Returns:
            SyncResult indicating SUCCESS or FAILED with error details.
        """
        try:
            est_number: str = estimation_data.get("estimation_number", "Unknown")
            title = (
                f"Estimation: {est_number} — "
                f"{estimation_data.get('project_name', 'Unnamed')}"
            )
            markdown = self._format_estimation_markdown(estimation_data)
            existing_doc_id = self._find_document(est_number)

            if existing_doc_id:
                resp = self._session.post(
                    f"{self.base_url}/api/documents.update",
                    headers=self._headers,
                    json={"id": existing_doc_id, "title": title, "text": markdown},
                    timeout=self.timeout,
                )
                created, updated = 0, 1
            else:
                payload: dict = {"title": title, "text": markdown, "publish": True}
                if self.collection_id:
                    payload["collectionId"] = self.collection_id
                if self.parent_document_id:
                    payload["parentDocumentId"] = self.parent_document_id
                resp = self._session.post(
                    f"{self.base_url}/api/documents.create",
                    headers=self._headers,
                    json=payload,
                    timeout=self.timeout,
                )
                created, updated = 1, 0

            if resp.status_code == 200:
                return SyncResult(
                    system=self.system_name,
                    direction=SyncDirection.EXPORT.value,
                    status=SyncStatus.SUCCESS,
                    items_processed=1,
                    items_created=created,
                    items_updated=updated,
                    items_failed=0,
                    errors=[],
                )

            return SyncResult(
                system=self.system_name,
                direction=SyncDirection.EXPORT.value,
                status=SyncStatus.FAILED,
                items_processed=1,
                items_created=0,
                items_updated=0,
                items_failed=1,
                errors=[f"HTTP {resp.status_code}: {resp.text[:200]}"],
            )

        except requests.RequestException as exc:
            return SyncResult(
                system=self.system_name,
                direction=SyncDirection.EXPORT.value,
                status=SyncStatus.FAILED,
                items_processed=1,
                items_created=0,
                items_updated=0,
                items_failed=1,
                errors=[str(exc)],
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def search_documents(self, query: str, limit: int = 10) -> list[dict]:
        """Search Outline wiki for documents matching *query*.

        Args:
            query: Free-text search string.
            limit: Maximum number of results to return (default 10).

        Returns:
            List of dicts with keys ``id``, ``title``, ``url``, ``snippet``.
            Returns an empty list on any error.
        """
        try:
            resp = self._session.post(
                f"{self.base_url}/api/documents.search",
                headers=self._headers,
                json={"query": query, "limit": limit},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return [
                    {
                        "id": item["document"]["id"],
                        "title": item["document"]["title"],
                        "url": item["document"].get("url", ""),
                        "snippet": item.get("context", ""),
                    }
                    for item in resp.json().get("data", [])
                ]
        except requests.RequestException:
            pass
        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_collection_id(self, raw: str) -> str:
        """Translate a collection identifier to a UUID.

        Accepts any of the following forms:
        - A UUID string (returned as-is).
        - A full Outline URL, e.g.
          ``http://outline.example.com/collection/my-col-spa7ozNOXi``.
        - A document URL, e.g.
          ``http://outline.example.com/doc/estimations-g0FaMvrXBz``
          (the document's parent collection ID is resolved via the API).
        - A bare slug, e.g. ``my-col-spa7ozNOXi``.

        For URL / slug forms the method calls the Outline API to resolve
        the identifier to a collection UUID.

        Returns:
            The UUID string if resolved, otherwise the original value
            unchanged (so that downstream calls surface a clear Outline API
            error rather than silently discarding the value).
        """
        raw = raw.strip()
        if not raw:
            return ""

        # Already a UUID — nothing to do.
        if self._UUID_RE.match(raw):
            return raw

        # Handle document URLs (/doc/<slug>) — look up the document and
        # use it as the parent document for new estimations.
        doc_match = re.search(r"/doc/([^/?#]+)", raw)
        if doc_match:
            doc_slug = doc_match.group(1)
            try:
                resp = self._session.post(
                    f"{self.base_url}/api/documents.search",
                    headers=self._headers,
                    json={"query": doc_slug.rsplit("-", 1)[0].replace("-", " "), "limit": 10},
                    timeout=int(self.additional_config.get("timeout", 30)),
                )
                if resp.status_code == 200:
                    for item in resp.json().get("data", []):
                        doc = item.get("document", {})
                        doc_url = doc.get("url", "")
                        if doc_url.rstrip("/").endswith(doc_slug):
                            self.parent_document_id = doc.get("id", "")
                            collection_id = doc.get("collectionId", "")
                            if collection_id:
                                return collection_id
            except requests.RequestException:
                pass
            return raw

        # Extract the slug part.  Outline collection URLs look like
        # ``/collection/<slug>`` where the slug ends with a short
        # alphanumeric id (e.g. ``incoming-request-spa7ozNOXi``).
        slug = raw
        url_match = re.search(r"/collection/([^/?#]+)", raw)
        if url_match:
            slug = url_match.group(1)

        # Normalise to ``/collection/<slug>`` for comparison.
        target_path = f"/collection/{slug}"

        try:
            resp = self._session.post(
                f"{self.base_url}/api/collections.list",
                headers=self._headers,
                json={},
                timeout=int(self.additional_config.get("timeout", 30)),
            )
            if resp.status_code == 200:
                for col in resp.json().get("data", []):
                    col_url: str = col.get("url", "")
                    if col_url == target_path:
                        return col["id"]
                    # Also match on the trailing id portion of the slug
                    # (e.g. ``spa7ozNOXi``) in case the human-readable
                    # prefix was changed after creation.
                    if col_url.rstrip("/").endswith(slug.rsplit("-", 1)[-1]):
                        return col["id"]
        except requests.RequestException:
            pass

        # Couldn't resolve — return as-is so the caller gets a meaningful
        # error from the Outline API.
        return raw

    def _find_document(self, estimation_number: str) -> str | None:
        """Return the document ID for an existing estimation page, or None.

        Args:
            estimation_number: The unique estimation identifier to search for.

        Returns:
            Document ID string if found, otherwise ``None``.
        """
        results = self.search_documents(f"Estimation: {estimation_number}")
        for doc in results:
            if estimation_number in doc.get("title", ""):
                return doc["id"]
        return None

    def _format_estimation_markdown(self, data: dict) -> str:
        """Render *data* as a Markdown wiki page.

        Args:
            data: Estimation data dictionary (see ``export_estimation`` for
                expected keys).

        Returns:
            Multi-line Markdown string suitable for an Outline document body.
        """
        feasibility: str = data.get("feasibility_status", "N/A")
        status_display = {
            "FEASIBLE": "**FEASIBLE** \u2705",
            "AT_RISK": "**AT RISK** \u26a0\ufe0f",
            "NOT_FEASIBLE": "**NOT FEASIBLE** \u274c",
        }.get(feasibility, feasibility)

        lines: list[str] = [
            f"# {data.get('estimation_number', 'N/A')} — {data.get('project_name', 'Unnamed')}",
            "",
            f"**Status:** {data.get('status', 'DRAFT')}  ",
            f"**Version:** {data.get('version', 1)}  ",
            f"**Feasibility:** {status_display}  ",
            f"**Project Type:** {data.get('project_type', 'N/A')}  ",
            f"**Assigned To:** {data.get('assigned_to_name') or 'Unassigned'}  ",
            f"**Created:** {data.get('created_at', 'N/A')}  ",
            "",
        ]

        # Timeline information
        _start_date = data.get("start_date", "")
        _testing_start = data.get("testing_start_date", "")
        _target_date = data.get("expected_delivery", "")
        if _start_date or _testing_start or _target_date:
            lines.append("## Timeline")
            lines.append("")
            if _start_date:
                lines.append(f"- **Project Start Date (T0):** {_start_date}")
            if _testing_start:
                lines.append(f"- **Testing Start Date:** {_testing_start}")
            if _target_date:
                lines.append(f"- **Target Date:** {_target_date}")
            _working_weeks = data.get("working_weeks", 0)
            if _working_weeks:
                lines.append(f"- **Working Weeks:** {_working_weeks:.1f}")
            _total_pd = data.get("grand_total_days", 0)
            if _total_pd:
                lines.append(f"- **Total Person-Days:** {_total_pd:.1f}")
            lines.append("")

        # Project context
        project_goals = data.get("project_goals")
        target_customer = data.get("target_customer")
        if project_goals or target_customer:
            lines.append("## Project Context")
            lines.append("")
            if project_goals:
                lines.append(f"**Project Goals:** {project_goals}  ")
            if target_customer:
                lines.append(f"**Target Customer:** {target_customer}  ")
            lines.append("")

        # Effort summary
        hpd = data.get("working_hours_per_day", 7.0)
        effort_lines = [
            "## Effort Summary",
            "",
            "| Category | Hours | Person-Days |",
            "|----------|------:|------------:|",
            f"| Tester Effort | {data.get('total_tester_hours', 0):.1f} | {data.get('total_tester_hours', 0) / hpd:.1f} |",
            f"| Leader Effort | {data.get('total_leader_hours', 0):.1f} | {data.get('total_leader_hours', 0) / hpd:.1f} |",
            f"| PR Fix Validation | {data.get('pr_fix_hours', 0):.1f} | {data.get('pr_fix_hours', 0) / hpd:.1f} |",
        ]
        pr_no_test = data.get("pr_no_test_hours", 0)
        if pr_no_test:
            effort_lines.append(f"| PR Test Creation | {pr_no_test:.1f} | {pr_no_test / hpd:.1f} |")
        effort_lines.append(f"| New Feature Study | {data.get('study_hours', 0):.1f} | {data.get('study_hours', 0) / hpd:.1f} |")
        release_extra = data.get("release_extra_hours", 0)
        doc_hours = data.get("documentation_hours", 0)
        if release_extra:
            effort_lines.append(f"| Release Extra | {release_extra:.1f} | {release_extra / hpd:.1f} |")
        if doc_hours:
            effort_lines.append(f"| Documentation | {doc_hours:.1f} | {doc_hours / hpd:.1f} |")
        effort_lines.extend([
            f"| Buffer | {data.get('buffer_hours', 0):.1f} | {data.get('buffer_hours', 0) / hpd:.1f} |",
            f"| **Grand Total** | **{data.get('grand_total_hours', 0):.1f}** | **{data.get('grand_total_days', 0):.1f}** |",
        ])
        working_weeks = data.get("working_weeks", 0)
        if working_weeks:
            effort_lines.append(f"| Working Weeks | {working_weeks:.1f} | |")
        effort_lines.append("")
        lines.extend(effort_lines)

        # Parameters
        lines.extend([
            "## Parameters",
            "",
            f"- DUT Count: {data.get('dut_count', 0)}",
            f"- Profile Count: {data.get('profile_count', 0)}",
            f"- DUT x Profile Combinations: {data.get('dut_profile_combinations', 0)}",
            f"- PR Fix Count: {data.get('pr_fix_count', 0)}",
        ])
        dut_names = data.get("dut_names", [])
        if dut_names:
            lines.append(f"- DUT Types: {', '.join(dut_names)}")
        profile_names = data.get("profile_names", [])
        if profile_names:
            lines.append(f"- Test Profiles: {', '.join(profile_names)}")
        lines.append("")

        # PR Fixes breakdown
        pr_simple = data.get("pr_simple", 0)
        pr_medium = data.get("pr_medium", 0)
        pr_complex = data.get("pr_complex", 0)
        if pr_simple or pr_medium or pr_complex:
            pr_fix_total = data.get("pr_fix_hours", 0)
            lines.extend([
                "## PR Fixes Breakdown",
                "",
                "| Complexity | Count | Hours Each | Subtotal (h) | Subtotal (MD) |",
                "|------------|------:|-----------:|-------------:|--------------:|",
                f"| Simple | {pr_simple} | 2 | {pr_simple * 2} | {pr_simple * 2 / hpd:.1f} |",
                f"| Medium | {pr_medium} | 4 | {pr_medium * 4} | {pr_medium * 4 / hpd:.1f} |",
                f"| Complex | {pr_complex} | 8 | {pr_complex * 8} | {pr_complex * 8 / hpd:.1f} |",
                f"| **Total** | **{pr_simple + pr_medium + pr_complex}** | | **{pr_fix_total:.1f}** | **{pr_fix_total / hpd:.1f}** |",
                "",
            ])

        pr_details: list[dict] = data.get("pr_details", [])
        if pr_details:
            lines.extend([
                "### PR Details",
                "",
                "| PR # | Link | Priority | Complexity | Status | Test Available |",
                "|------|------|----------|------------|--------|---------------|",
            ])
            for pr in pr_details:
                pr_num = pr.get("pr_number", "")
                link = pr.get("link", "")
                link_md = f"[{link}]({link})" if link else "\u2014"
                priority = pr.get("priority", "")
                cx = pr.get("complexity", "")
                st = pr.get("status", "")
                test_avail = "\u2705" if pr.get("test_available", True) else "\u274c"
                lines.append(f"| {pr_num} | {link_md} | {priority} | {cx} | {st} | {test_avail} |")
            lines.append("")

        tasks: list[dict] = data.get("tasks", [])
        if tasks:
            lines.extend([
                "## Task Breakdown",
                "",
                "| Task | Type | Tester Hours | Leader Hours | Total Hrs | Total MD | Notes |",
                "|------|------|-------------:|-------------:|----------:|---------:|-------|",
            ])
            for task in tasks:
                name = task.get("task_name", task.get("name", ""))
                task_type = task.get("task_type", "")
                tester_hours = task.get("calculated_hours", 0)
                leader_hours = task.get("leader_hours", 0)
                total_h = tester_hours + leader_hours
                notes = task.get("notes", "") or ""
                lines.append(f"| {name} | {task_type} | {tester_hours:.1f} | {leader_hours:.1f} | {total_h:.1f} | {total_h / hpd:.1f} | {notes} |")
            lines.append("")

        # Document Deliverables
        doc_deliverables: list[dict] = data.get("document_deliverables", [])
        if doc_deliverables:
            lines.extend([
                "## Document Deliverables",
                "",
                "| Document Type | Category | Linked Task | Count | Total Hours | Effective Hours | Note |",
                "|---------------|----------|-------------|------:|------------:|----------------:|------|",
            ])
            for dd in doc_deliverables:
                lines.append(
                    f"| {dd.get('name', '')} "
                    f"| {dd.get('category', '')} "
                    f"| {dd.get('linked_task', '') or '\u2014'} "
                    f"| {dd.get('count', 0)} "
                    f"| {dd.get('total_hours', 0):.1f} "
                    f"| {dd.get('effective_hours', 0):.1f} "
                    f"| {dd.get('overlap_note', '') or ''} |"
                )
            lines.append("")

        # Team Allocation
        team_members: list[dict] = data.get("team_members", [])
        if team_members:
            lines.extend([
                "## Team Allocation",
                "",
                "| Member | Role | Allocated Hours |",
                "|--------|------|----------------:|",
            ])
            for tm in team_members:
                lines.append(f"| {tm.get('name', '')} | {tm.get('role', '')} | {tm.get('allocated_hours', 0):.1f} |")
            lines.append("")

        # Risk Assessment
        risk_messages: list[str] = data.get("risk_messages", [])
        if risk_messages:
            lines.extend([
                "## Risk Assessment",
                "",
            ])
            for msg in risk_messages:
                lines.append(f"- \u26a0\ufe0f {msg}")
            lines.append("")

        lines.extend([
            "---",
            (
                f"*Generated by PRESTO on "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M')}*"
            ),
        ])

        return "\n".join(lines)
