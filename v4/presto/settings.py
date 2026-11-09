"""Django settings for PRESTO v4."""

from __future__ import annotations

import os
from pathlib import Path

from .db import database_config

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BASE_DIR.parent

APP_VERSION = os.environ.get("APP_VERSION", "").strip() or "4.0.0.dev0"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = _env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG and not _env_bool("DJANGO_ALLOW_INSECURE_KEY", False):
        # Fail loudly in production; dev and tests opt in explicitly.
        raise RuntimeError("DJANGO_SECRET_KEY must be set (or DJANGO_DEBUG=1 for local development)")
    SECRET_KEY = "insecure-dev-key-do-not-use-in-production"

ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")

# Django admin is intentionally not installed (decision D4): every admin
# task is an in-app page or a management command.
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.teams",
    "apps.catalog",
    "apps.inbox",
    "apps.estimations",
    "apps.integrations",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "presto.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "presto.wsgi.application"
ASGI_APPLICATION = "presto.asgi.application"

DATABASES = {"default": database_config(BASE_DIR)}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"  # INTEGER ids, same as v3

AUTH_USER_MODEL = "accounts.User"

# v3 stores naive timestamps (SQLite CURRENT_TIMESTAMP, i.e. UTC). Keep that
# so existing rows read back unchanged.
USE_TZ = False
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
USE_I18N = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT") or BASE_DIR / "data" / "uploads")

# Seed data shared with v3 until v4 replaces it at the repo root.
SEED_DATA_PATH = Path(os.environ.get("SEED_DATA_PATH") or REPO_DIR / "data" / "seed_data.json")

SESSION_COOKIE_HTTPONLY = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
