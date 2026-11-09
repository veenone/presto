"""First-run data: seed catalog, default config and default admin.

Behaviour matches v3 ``init_database()`` so a fresh v4 install holds the same
rows as a fresh v3 install.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.accounts.models import Role, User
from apps.catalog.models import DutType, Feature, TaskTemplate, TestProfile
from apps.core import config
from apps.core.models import Configuration

DEFAULT_ADMIN = {
    "username": "admin",
    "password": "admin",
    "display_name": "Administrator",
    "email": "admin@localhost",
}


@transaction.atomic
def load_seed_data(path: Path | None = None) -> bool:
    """Load the seed catalog if the feature table is empty. Returns True if loaded."""
    path = Path(path or settings.SEED_DATA_PATH)
    if Feature.objects.exists() or not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))

    features: dict[str, Feature] = {}
    for f in data.get("features", []):
        features[f["name"]] = Feature.objects.create(
            name=f["name"],
            category=f.get("category"),
            complexity_weight=f.get("complexity_weight", 1.0),
            has_existing_tests=f.get("has_existing_tests", False),
            description=f.get("description"),
        )
    for t in data.get("task_templates", []):
        # v3 sets only the legacy feature_id here; its many-to-many link was
        # view-only, so no task_template_features rows are written. Keep parity.
        linked = features.get(t.get("feature_name") or "")
        TaskTemplate.objects.create(
            feature=linked,
            name=t["name"],
            task_type=t["task_type"],
            base_effort_hours=t["base_effort_hours"],
            scales_with_dut=t.get("scales_with_dut", False),
            scales_with_profile=t.get("scales_with_profile", False),
            is_parallelizable=t.get("is_parallelizable", False),
            description=t.get("description"),
        )
    # v3 schema 17 flags PR-fix templates by name; a fresh v3 install ends up with this.
    for template in TaskTemplate.objects.all():
        name = template.name.lower()
        if "pr fix" in name or "pr validation" in name:
            template.is_pr_fix = True
            template.save(update_fields=["is_pr_fix"])
    for d in data.get("dut_types", []):
        DutType.objects.create(
            name=d["name"], category=d.get("category"),
            complexity_multiplier=d.get("complexity_multiplier", 1.0),
        )
    for p in data.get("test_profiles", []):
        TestProfile.objects.create(
            name=p["name"], description=p.get("description"),
            effort_multiplier=p.get("effort_multiplier", 1.0),
        )
    for key, cfg in data.get("configuration", {}).items():
        Configuration.objects.get_or_create(
            key=key, defaults={"value": cfg["value"], "description": cfg.get("description")},
        )
    return True


def ensure_default_admin() -> User | None:
    """Create admin/admin when there are no users at all (as v3 does)."""
    if User.objects.exists():
        return None
    return User.objects.create_superuser(
        DEFAULT_ADMIN["username"], DEFAULT_ADMIN["password"],
        display_name=DEFAULT_ADMIN["display_name"], email=DEFAULT_ADMIN["email"],
        auth_provider="local",
    )


@transaction.atomic
def ensure_defaults() -> dict[str, int | bool]:
    created_keys = config.ensure_defaults()
    admin = ensure_default_admin()
    return {"config_keys_created": created_keys, "admin_created": admin is not None}


def reset_admin(username: str, password: str) -> tuple[User, bool]:
    """Create or reset an admin account (port of v3 scripts/create_admin.py)."""
    user = User.objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_superuser(
            username, password, display_name="Administrator",
            email=f"{username}@localhost", auth_provider="local",
        )
        return user, True
    user.set_password(password)
    user.role = Role.ADMIN
    user.is_active = True
    user.save()
    return user, False
