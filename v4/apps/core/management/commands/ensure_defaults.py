from django.core.management.base import BaseCommand

from apps.core.bootstrap import ensure_defaults


class Command(BaseCommand):
    help = "Insert missing configuration keys and create admin/admin if there are no users."

    def handle(self, *args, **options):
        result = ensure_defaults()
        self.stdout.write(
            f"config keys created: {result['config_keys_created']}; "
            f"default admin: {'created' if result['admin_created'] else 'exists'}"
        )
