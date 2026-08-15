#!/bin/bash
# Optional Vercel build step.
#
# Not required: WHITENOISE_USE_FINDERS lets WhiteNoise serve static files
# straight from each app's static/ directory with no build at all. Use this
# when you want compressed, pre-collected assets, and when DATABASE_URL points
# at a real database that should be migrated once at build time rather than
# per cold start.
set -e

pip install -r requirements.txt
python manage.py collectstatic --no-input

if [ -n "$DATABASE_URL" ]; then
  python manage.py migrate --no-input
  python manage.py seed_deals
fi
