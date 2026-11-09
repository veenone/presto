"""API routes: /public-holidays/*, /working-weeks."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["holidays"])
