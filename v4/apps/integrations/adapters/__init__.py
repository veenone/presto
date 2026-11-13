"""External system adapters (copied unchanged from v3)."""

from . import base
from .base import (
    BaseAdapter,
    ConnectionTestResult,
    ExternalRequest,
    IntegrationSystem,
    SyncDirection,
    SyncResult,
    SyncStatus,
)
from .email_adapter import EmailAdapter
from .jira_adapter import JiraAdapter
from .outline_adapter import OutlineAdapter
from .redmine_adapter import RedmineAdapter
from .snipeit_adapter import SnipeItAdapter

__all__ = [
    "BaseAdapter",
    "ConnectionTestResult",
    "EmailAdapter",
    "ExternalRequest",
    "IntegrationSystem",
    "JiraAdapter",
    "OutlineAdapter",
    "RedmineAdapter",
    "SnipeItAdapter",
    "SyncDirection",
    "SyncResult",
    "SyncStatus",
    "base",
]
