"""Cold-start database bootstrap.

Serverless instances start with an empty /tmp, so the SQLite file that
`DEALS_DB_PATH` points at will not exist. This runs migrations and seeds the
sample data once per instance.

This is a convenience for demo deployments, not a deployment strategy. With
DATABASE_URL set it does nothing beyond a cheap existence check: migrations
there belong in the build step, where they run once rather than per instance.
"""

import logging
import threading

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError

log = logging.getLogger(__name__)

_lock = threading.Lock()
_done = False


def _needs_bootstrap() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='deals_deal'"
            )
            return cursor.fetchone() is None
    except OperationalError:
        return True


def ensure_database(force: bool = False) -> bool:
    """Returns True if it migrated and seeded, False if there was nothing to do."""
    global _done
    if _done and not force:
        return False

    with _lock:
        if _done and not force:
            return False

        # Only ever self-migrate an ephemeral SQLite file. Auto-migrating a
        # shared Postgres from inside a request path is how you get two
        # instances racing the same DDL.
        if not getattr(settings, "EPHEMERAL_DATABASE", False) and not force:
            _done = True
            return False

        if not _needs_bootstrap() and not force:
            _done = True
            return False

        log.warning("Bootstrapping ephemeral database at %s", settings.DATABASES["default"]["NAME"])
        call_command("migrate", "--no-input", verbosity=0)
        from .seeds import seed_database

        summary = seed_database()
        log.warning(
            "Seeded %s deals (%s duplicates collapsed)",
            summary["deals_after_dedupe"],
            summary["duplicates_collapsed"],
        )
        _done = True
        return True


def reset_flag() -> None:
    """Test hook."""
    global _done
    _done = False
