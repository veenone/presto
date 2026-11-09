"""API routes: /auth/*, /users/*, /audit-log."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["accounts"])
