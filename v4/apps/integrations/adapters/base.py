"""Base integration adapter and shared types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class IntegrationSystem(str, Enum):
    REDMINE = "REDMINE"
    JIRA = "JIRA"
    XRAY = "XRAY"
    EMAIL = "EMAIL"


class SyncDirection(str, Enum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    BIDIRECTIONAL = "BIDIRECTIONAL"


class SyncStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"


@dataclass
class SyncResult:
    system: str
    direction: str
    status: SyncStatus
    items_processed: int = 0
    items_created: int = 0
    items_updated: int = 0
    items_failed: int = 0
    errors: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    imported_items: list["ExternalRequest"] = field(default_factory=list)


@dataclass
class ExternalRequest:
    """A request imported from an external system."""
    external_id: str
    title: str
    description: str = ""
    requester_name: str = ""
    requester_email: str = ""
    priority: str = "MEDIUM"
    requested_delivery_date: str | None = None
    attachments: list[dict] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


@dataclass
class ConnectionTestResult:
    success: bool
    message: str
    details: dict = field(default_factory=dict)


class BaseAdapter(ABC):
    """Base class for all integration adapters."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.base_url = config.get("base_url", "")
        self.api_key = config.get("api_key", "")
        self.username = config.get("username", "")
        self.additional_config = config.get("additional_config", {})
        # TLS verification — corp services often use a self-signed CA chain.
        # Stored per-integration in additional_config["ssl_verify"]; accepts a
        # bool, a "true"/"false" string, or a CA-bundle path string. Defaults
        # to True (verify). Set False to skip verification on self-signed certs.
        self.ssl_verify: bool | str = self._normalize_ssl_verify(
            self.additional_config.get("ssl_verify", True)
        )

    @staticmethod
    def _normalize_ssl_verify(value: Any) -> bool | str:
        """Coerce a stored ssl_verify value into requests' ``verify`` arg."""
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("false", "0", "no", "off"):
                return False
            if lowered in ("true", "1", "yes", "on", ""):
                return True
            # Any other string is treated as a CA-bundle path.
            return value
        return bool(value)

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test the connection to the external system."""
        ...

    @abstractmethod
    def import_requests(self) -> SyncResult:
        """Import requests from the external system."""
        ...

    @abstractmethod
    def export_estimation(self, estimation_data: dict) -> SyncResult:
        """Export estimation results to the external system."""
        ...

    @property
    @abstractmethod
    def system_name(self) -> str:
        ...
