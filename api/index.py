"""Vercel serverless entry point.

Vercel's Python runtime looks for a WSGI callable named `app` (or `handler`)
in this module and routes every request here via vercel.json.
"""

import os
import sys
from pathlib import Path

# The project root is one level up from api/; Vercel does not add it to the
# path automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from config.wsgi import application  # noqa: E402
from deals.bootstrap import ensure_database  # noqa: E402

# Cold start: on an ephemeral filesystem there is no database yet, so build one.
# No-op when DATABASE_URL points at a real database that is already migrated.
ensure_database()

app = application
handler = application
