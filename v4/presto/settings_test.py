"""Settings for the test suite: no production secret, in-memory SQLite."""

import os

os.environ.setdefault("DJANGO_ALLOW_INSECURE_KEY", "1")

from .settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # unused by User; speeds up contrib code
