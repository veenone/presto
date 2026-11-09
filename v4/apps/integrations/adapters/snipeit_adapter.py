"""Snipe-IT integration adapter — pull hardware assets for DUT management."""

import logging
from typing import Any

import requests as http_requests

from .base import BaseAdapter, ConnectionTestResult, SyncResult, SyncStatus

logger = logging.getLogger(__name__)


class SnipeItAdapter(BaseAdapter):
    """Adapter for Snipe-IT asset management system."""

    @property
    def system_name(self) -> str:
        return "SNIPE_IT"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/api/v1/{path.lstrip('/')}"

    def _request(self, method: str, url: str, **kwargs) -> http_requests.Response:
        timeout = self.additional_config.get("timeout", 30)
        kwargs.setdefault("verify", self.ssl_verify)
        return http_requests.request(
            method, url, headers=self._headers(), timeout=timeout, **kwargs
        )

    def test_connection(self) -> ConnectionTestResult:
        """Test connection by fetching one hardware item."""
        if not self.base_url or not self.api_key:
            return ConnectionTestResult(False, "Base URL and API key are required.")
        try:
            resp = self._request("GET", self._url("hardware?limit=1"))
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("total", 0)
                return ConnectionTestResult(
                    True,
                    f"Connected successfully. {total} total assets found.",
                    {"total_assets": total},
                )
            return ConnectionTestResult(
                False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        except Exception as e:
            return ConnectionTestResult(False, f"Connection failed: {e}")

    def import_requests(self) -> SyncResult:
        """Not used for Snipe-IT (asset-only integration)."""
        return SyncResult(
            system=self.system_name,
            direction="IMPORT",
            status=SyncStatus.SKIPPED,
            errors=["Snipe-IT integration is for asset data only, not request import."],
        )

    def export_estimation(self, estimation_data: dict) -> SyncResult:
        """Not used for Snipe-IT."""
        return SyncResult(
            system=self.system_name,
            direction="EXPORT",
            status=SyncStatus.SKIPPED,
            errors=["Snipe-IT integration does not support estimation export."],
        )

    def get_categories(self) -> list[dict]:
        """Fetch all categories from Snipe-IT."""
        try:
            resp = self._request("GET", self._url("categories?limit=500"))
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("rows", [])
                return [{"id": r["id"], "name": r["name"]} for r in rows]
        except Exception as e:
            logger.warning("Failed to fetch Snipe-IT categories: %s", e)
        return []

    def _get_category_ids(self, names: list[str]) -> list[int]:
        """Map category names to IDs."""
        categories = self.get_categories()
        name_lower_map = {c["name"].lower(): c["id"] for c in categories}
        ids = []
        for name in names:
            cid = name_lower_map.get(name.strip().lower())
            if cid:
                ids.append(cid)
        return ids

    def get_hardware_by_category(self, category_names: list[str]) -> list[dict]:
        """Fetch hardware assets filtered by category names."""
        category_ids = self._get_category_ids(category_names)
        if not category_ids:
            # If no category filter or names not found, return all
            try:
                resp = self._request("GET", self._url("hardware?limit=500"))
                if resp.status_code == 200:
                    return self._parse_hardware(resp.json().get("rows", []))
            except Exception as e:
                logger.warning("Failed to fetch Snipe-IT hardware: %s", e)
            return []

        assets: list[dict] = []
        for cid in category_ids:
            try:
                resp = self._request(
                    "GET", self._url(f"hardware?category_id={cid}&limit=500")
                )
                if resp.status_code == 200:
                    assets.extend(self._parse_hardware(resp.json().get("rows", [])))
            except Exception as e:
                logger.warning("Failed to fetch hardware for category %s: %s", cid, e)
        return assets

    @staticmethod
    def _parse_hardware(rows: list[dict]) -> list[dict]:
        """Parse Snipe-IT hardware rows into simplified dicts."""
        result = []
        for r in rows:
            model = r.get("model") or {}
            category = r.get("category") or {}
            status_label = r.get("status_label") or {}
            result.append({
                "id": r.get("id"),
                "name": r.get("name", ""),
                "serial": r.get("serial", ""),
                "model_name": model.get("name", ""),
                "category": category.get("name", ""),
                "status": status_label.get("name", ""),
                "asset_tag": r.get("asset_tag", ""),
            })
        return result
