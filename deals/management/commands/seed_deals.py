"""Seed the store from the two sample feeds.

python manage.py seed_deals            # idempotent top-up
python manage.py seed_deals --flush    # wipe deals/cards/brands first
"""

import json

from django.core.management.base import BaseCommand

from deals.seeds import seed_database


class Command(BaseCommand):
    help = "Seed sample deals, cards and brands (merged and de-duplicated)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing deals, cards and brands before seeding.",
        )

    def handle(self, *args, **options):
        summary = seed_database(flush=options["flush"])
        self.stdout.write(json.dumps(summary, indent=2))
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {summary['deals_after_dedupe']} deals "
                f"({summary['duplicates_collapsed']} duplicates collapsed)"
            )
        )
