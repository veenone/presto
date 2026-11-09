"""API routes: /estimations/{id}/report/*, /estimations/{id}/send-report, /reports/*."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["reports"])
