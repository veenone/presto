from django.core.management.base import BaseCommand

from apps.core.bootstrap import reset_admin


class Command(BaseCommand):
    help = "Create an admin account, or reset its password, role and active flag."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", default="admin")

    def handle(self, *args, username, password, **options):
        _, created = reset_admin(username, password)
        self.stdout.write(f"Admin account {'created' if created else 'reset'}: {username}")
