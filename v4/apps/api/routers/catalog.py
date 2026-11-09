"""API routes: /features/*, /feature-presets/*, /task-templates/*, /task-presets/*, /dut-types/*, /profiles/*, /risk-items/*, /document-types/*, /historical-projects/*."""

from ninja import Router

from apps.api.auth import authenticated

router = Router(auth=authenticated, tags=["catalog"])
