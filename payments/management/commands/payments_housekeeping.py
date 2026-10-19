"""Cron-friendly housekeeping for the payments system.

Run every 10-15 minutes via cron / systemd timer:

    */15 * * * * cd /path/to/project && python manage.py payments_housekeeping

Does two things, keeping the platform fully autonomous:
1. Re-verifies tranches stuck in `initiated` against the provider APIs
   (safety net for lost webhooks or callbacks blocked by a firewall).
2. Cancels self-signups that never paid and went silent, freeing their
   e-mail for a fresh attempt.
"""

from django.core.management.base import BaseCommand

from payments.services import cancel_abandoned_enrollments, recheck_pending_payments


class Command(BaseCommand):
    help = "Re-check pending payments and clean up abandoned enrollments."

    def add_arguments(self, parser):
        parser.add_argument("--max-age-minutes", type=int, default=30,
                            help="Only re-check tranches older than this.")

    def handle(self, *args, **options):
        summary = recheck_pending_payments(max_age_minutes=options["max_age_minutes"])
        cancelled = cancel_abandoned_enrollments()
        self.stdout.write(self.style.SUCCESS(
            f"recheck: {summary} | abandoned enrollments cancelled: {cancelled}"
        ))
