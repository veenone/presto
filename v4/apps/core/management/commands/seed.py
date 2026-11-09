from django.core.management.base import BaseCommand

from apps.core.bootstrap import ensure_defaults, load_seed_data


class Command(BaseCommand):
    help = "Load the seed catalog (if empty), default config keys and the default admin."

    def handle(self, *args, **options):
        loaded = load_seed_data()
        result = ensure_defaults()
        self.stdout.write(
            f"seed catalog: {'loaded' if loaded else 'skipped (catalog not empty)'}; "
            f"config keys created: {result['config_keys_created']}; "
            f"default admin: {'created' if result['admin_created'] else 'exists'}"
        )
