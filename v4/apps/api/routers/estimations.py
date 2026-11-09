"""API routes: /estimations/* except report, send-report and export paths."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["estimations"])
