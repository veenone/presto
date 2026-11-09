"""Check that the database matches the v4 models (used when adopting a v3 DB).

Typical upgrade of an existing v3 database::

    python manage.py check_v3_compat --before-migrate
    python manage.py migrate --fake-initial
    python manage.py check_v3_compat
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.config import DJANGO_SCHEMA_MARKER, SCHEMA_VERSION_KEY

PROJECT_APPS = {
    "core", "accounts", "teams", "catalog", "inbox", "estimations", "integrations", "notifications",
}
V3_SCHEMA_VERSION = 24
# Columns added by v4 migrations; absent from a v3 database until `migrate` runs.
V4_ONLY_COLUMNS = {("users", "theme_preference")}


class Command(BaseCommand):
    help = "Compare database tables and columns with the v4 models."

    def add_arguments(self, parser):
        parser.add_argument(
            "--before-migrate", action="store_true",
            help="Ignore columns that v4 migrations have not added yet.",
        )

    def handle(self, *args, before_migrate=False, **options):
        errors: list[str] = []
        warnings: list[str] = []
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            for model in apps.get_models():
                if model._meta.app_label not in PROJECT_APPS:
                    continue
                table = model._meta.db_table
                if table not in tables:
                    errors.append(f"missing table {table}")
                    continue
                db_cols = {c.name for c in connection.introspection.get_table_description(cursor, table)}
                model_cols = {f.column for f in model._meta.concrete_fields if f.column}
                for col in sorted(model_cols - db_cols):
                    if before_migrate and (table, col) in V4_ONLY_COLUMNS:
                        continue
                    errors.append(f"missing column {table}.{col}")
                for col in sorted(db_cols - model_cols):
                    warnings.append(f"extra column {table}.{col} (ignored by v4)")

            version = None
            if "configuration" in tables:
                cursor.execute("SELECT value FROM configuration WHERE key = %s", [SCHEMA_VERSION_KEY])
                row = cursor.fetchone()
                version = row[0] if row else None

        if version is None:
            warnings.append("no schema_version key (new database?)")
        elif version == DJANGO_SCHEMA_MARKER:
            self.stdout.write("schema_version: managed by Django")
        else:
            try:
                number = int(version)
            except ValueError:
                errors.append(f"unrecognised schema_version {version!r}")
            else:
                if number < V3_SCHEMA_VERSION:
                    errors.append(
                        f"v3 schema_version is {number}; start v3.4 once to upgrade it to "
                        f"{V3_SCHEMA_VERSION} before adopting"
                    )
                else:
                    self.stdout.write(f"schema_version: v3 schema {number}")

        for w in warnings:
            self.stdout.write(self.style.WARNING(f"warning: {w}"))
        if errors:
            for e in errors:
                self.stderr.write(self.style.ERROR(f"error: {e}"))
            raise CommandError(f"{len(errors)} compatibility error(s)")
        self.stdout.write(self.style.SUCCESS("database is compatible with the v4 models"))
