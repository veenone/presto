"""API routes: /integrations/*, /webhooks/redmine, /estimations/{id}/export, /estimations/{id}/export-tasks."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["integrations"])
