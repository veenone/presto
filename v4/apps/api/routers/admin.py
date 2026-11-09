"""API routes: /admin/backup, /admin/restore."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["admin"])
