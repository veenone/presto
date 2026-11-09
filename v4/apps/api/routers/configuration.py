"""API routes: /configuration/*, /dut-categories, /feature-categories, /project-types."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["configuration"])
