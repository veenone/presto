"""API routes: /notifications/*."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["notifications"])
