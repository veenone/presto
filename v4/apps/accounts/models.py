"""User, session and audit models (v3 ``users``, ``user_sessions``, ``audit_log``)."""

from __future__ import annotations

import bcrypt
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

from apps.core.models import created_at_field, updated_at_field


class Role(models.TextChoices):
    VIEWER = "VIEWER"
    ESTIMATOR = "ESTIMATOR"
    APPROVER = "APPROVER"
    ADMIN = "ADMIN"


class ThemePreference(models.TextChoices):
    LIGHT = "light"
    DARK = "dark"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username: str, password: str | None = None, **extra) -> User:
        if not username:
            raise ValueError("username is required")
        extra.setdefault("display_name", username)
        user = self.model(username=username, **extra)
        if password:
            user.set_password(password)
        else:
            user.password = None
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, password: str | None = None, **extra) -> User:
        extra["role"] = Role.ADMIN
        extra.setdefault("is_active", True)
        return self.create_user(username, password, **extra)


class User(AbstractBaseUser):
    """Application user. Local, LDAP or OIDC (``auth_provider``).

    Passwords keep the v3 storage format (a raw bcrypt string in the
    ``password_hash`` column) so v3 and v4 can verify the same hashes.
    External-provider users have no password (NULL).
    """

    username = models.CharField(max_length=150)
    email = models.EmailField(max_length=254, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=255)
    password = models.CharField(max_length=128, null=True, blank=True, db_column="password_hash")
    auth_provider = models.CharField(max_length=32, default="local")
    external_id = models.CharField(max_length=255, null=True, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    team_member = models.ForeignKey(
        "teams.TeamMember", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="users", db_index=False,
    )
    last_login = models.DateTimeField(null=True, blank=True, db_column="last_login_at")
    created_at = created_at_field()
    updated_at = updated_at_field()
    # v4 only (decision D6). NULL = no saved choice: the browser's stored
    # choice, then the OS setting, then dark are used instead.
    theme_preference = models.CharField(
        max_length=8, choices=ThemePreference.choices, null=True, blank=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "users"
        ordering = ["username"]
        constraints = [
            models.UniqueConstraint(fields=["username"], name="ix_users_username"),
        ]

    def __str__(self) -> str:
        return self.username

    # -- passwords (v3-compatible raw bcrypt) ------------------------------
    def set_password(self, raw_password: str | None) -> None:
        if raw_password is None:
            self.set_unusable_password()
            return
        self.password = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt()).decode()
        self._password = raw_password

    def check_password(self, raw_password: str | None) -> bool:
        if not raw_password or not self.password or not self.password.startswith("$2"):
            return False
        try:
            return bcrypt.checkpw(raw_password.encode(), self.password.encode())
        except ValueError:
            return False

    # -- roles -------------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


class UserSession(models.Model):
    """Refresh-token store for the JWT API (v3 ``user_sessions``)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="sessions", db_index=False,
    )
    refresh_token = models.CharField(max_length=512, unique=True)
    expires_at = models.DateTimeField()
    created_at = created_at_field()

    class Meta:
        db_table = "user_sessions"
        indexes = [models.Index(fields=["user"], name="ix_user_sessions_user_id")]


class AuditLog(models.Model):
    """Append-only action log (v3 ``audit_log``)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="audit_logs", db_index=False,
    )
    action = models.CharField(max_length=128)
    resource_type = models.CharField(max_length=64, null=True, blank=True)
    resource_id = models.IntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, db_column="details_json")
    ip_address = models.CharField(max_length=64, null=True, blank=True)
    created_at = created_at_field()

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user"], name="ix_audit_log_user_id"),
            models.Index(fields=["action"], name="ix_audit_log_action"),
            models.Index(fields=["created_at"], name="ix_audit_log_created_at"),
        ]
