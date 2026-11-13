"""Database configuration from environment variables.

Mirrors the v3 lookup order so existing deployments keep working:

1. ``DB_URL``  - full URL, e.g. ``sqlite:////app/data/estimation.db`` or
   ``mysql+pymysql://user:pass@host/presto`` (the SQLAlchemy style used in v3).
2. ``DB_PATH`` - path to a SQLite file.
3. ``<BASE_DIR>/data/estimation.db`` - default SQLite file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import dj_database_url

SQLITE_OPTIONS = {
    # v3 ran SQLite in WAL mode; keep it so readers never block the writer.
    "init_command": "PRAGMA journal_mode=WAL;",
}


def _enable_pymysql() -> None:
    """Let Django's MySQL backend run on PyMySQL (pure Python, as in v3)."""
    import pymysql

    # Django checks the mysqlclient version; PyMySQL reports an old one.
    pymysql.version_info = (2, 2, 1, "final", 0)
    pymysql.install_as_MySQLdb()


def database_config(base_dir: Path) -> dict[str, Any]:
    url = os.environ.get("DB_URL", "").strip()
    if url:
        # Accept SQLAlchemy dialect+driver URLs from v3 configs.
        url = url.replace("mysql+pymysql://", "mysql://").replace("sqlite+pysqlite://", "sqlite://")
        cfg = dj_database_url.parse(url, conn_max_age=60, conn_health_checks=True)
        if cfg["ENGINE"].endswith("mysql"):
            _enable_pymysql()
            cfg.setdefault("OPTIONS", {}).setdefault("charset", "utf8mb4")
        elif cfg["ENGINE"].endswith("sqlite3"):
            cfg["OPTIONS"] = {**SQLITE_OPTIONS, **cfg.get("OPTIONS", {})}
        return cfg

    path = Path(os.environ.get("DB_PATH") or base_dir / "data" / "estimation.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(path),
        "OPTIONS": dict(SQLITE_OPTIONS),
    }
