"""Django settings for the Deal Search Service.

Deliberately lean: no admin, auth, sessions or templates. This service exposes
a handful of JSON endpoints and nothing renders HTML, so the middleware stack
and installed apps are trimmed to what's actually used.
"""

import contextlib
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-not-for-production")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
# Vercel sets VERCEL=1 in both build and runtime environments.
ON_VERCEL = bool(os.getenv("VERCEL"))

_hosts = os.getenv("DJANGO_ALLOWED_HOSTS")
if _hosts:
    ALLOWED_HOSTS = _hosts.split(",")
elif ON_VERCEL:
    # Narrow the default on Vercel rather than shipping "*" to production.
    ALLOWED_HOSTS = [".vercel.app"]
    if os.getenv("VERCEL_URL"):
        ALLOWED_HOSTS.append(os.environ["VERCEL_URL"])
    CSRF_TRUSTED_ORIGINS = ["https://*.vercel.app"]
else:
    ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "deals",
]

MIDDLEWARE = [
    "deals.middleware.LatencyHeaderMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves the frontend's CSS without a separate web server, which is what
    # makes this work on a serverless platform with no nginx in front.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
# CsrfViewMiddleware is deliberately absent: this is a token-less JSON API with
# no cookie-based auth, and DRF's APIView is csrf_exempt regardless. Add it back
# alongside SessionAuthentication if browser sessions are ever introduced.

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Set these behind TLS in production (see README).
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "0") == "1"
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "deals.context_processors.site",
            ],
        },
    },
]

STATIC_URL = "static/"
# /tmp is the only writable path on Vercel, and WhiteNoise warns about a
# missing STATIC_ROOT, so point it somewhere that can actually be created.
STATIC_ROOT = Path("/tmp/staticfiles") if ON_VERCEL else BASE_DIR / "staticfiles"
# A read-only filesystem is fine: WhiteNoise still serves via finders.
with contextlib.suppress(OSError):
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Compressed, but *not* manifest storage: manifest storage hard-fails if
    # collectstatic has not run, and on Vercel the build step is optional.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# Lets WhiteNoise serve straight from each app's static/ dir, so the frontend
# works even when collectstatic was never run (e.g. a zero-build deploy).
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG

# --------------------------------------------------------------------------
# Database
#
# Read the README's deployment section before shipping this to a serverless
# platform. Summary: this service writes on *every* search (a history row, and
# an idempotency record when a key is supplied), and a serverless filesystem is
# ephemeral. On Vercel, SQLite therefore lives in /tmp and disappears whenever
# the instance is recycled — fine for a demo, wrong for anything real. Set
# DATABASE_URL to a hosted Postgres and this all becomes durable.
# --------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            # Serverless invocations are short-lived and each one would
            # otherwise hold a connection open; pool on the database side
            # (PgBouncer, or Neon/Supabase's pooled endpoint) instead.
            conn_max_age=0 if ON_VERCEL else 600,
            ssl_require=ON_VERCEL,
        )
    }
    EPHEMERAL_DATABASE = False
else:
    # /tmp is the only writable path on Vercel's runtime.
    default_sqlite = "/tmp/deals.db" if ON_VERCEL else str(BASE_DIR / "deals.db")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("DEALS_DB_PATH", default_sqlite),
            "OPTIONS": {
                # WAL lets reads proceed during a write; the timeout stops
                # brief write contention surfacing as "database is locked".
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
                "timeout": 10,
            },
        }
    }
    EPHEMERAL_DATABASE = ON_VERCEL

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "deals.exceptions.error_envelope_handler",
    # Money is Decimal end to end internally; it goes over the wire as a JSON
    # number so clients don't have to parse strings.
    "COERCE_DECIMAL_TO_STRING": False,
    # DRF resolves AnonymousUser from django.contrib.auth by default. This
    # service has no users, so setting it to None keeps contrib.auth out of
    # INSTALLED_APPS entirely.
    "UNAUTHENTICATED_USER": None,
}

# In-process TTL + LRU cache for repeated identical searches.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "deal-search",
        "TIMEOUT": int(os.getenv("DEALS_CACHE_TTL", "60")),
        "OPTIONS": {"MAX_ENTRIES": int(os.getenv("DEALS_CACHE_MAX", "512")), "CULL_FREQUENCY": 4},
    }
}

# --- domain configuration -------------------------------------------------
DEALS_CURRENCY = os.getenv("DEALS_CURRENCY", "INR")
DEALS_MAX_AMOUNT = os.getenv("DEALS_MAX_AMOUNT", "10000000")
DEALS_PAGE_SIZE = int(os.getenv("DEALS_PAGE_SIZE", "20"))
DEALS_MAX_PAGE_SIZE = int(os.getenv("DEALS_MAX_PAGE_SIZE", "100"))
DEALS_IDEMPOTENCY_TTL = int(os.getenv("DEALS_IDEMPOTENCY_TTL", "86400"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        # 4xx responses are a normal, tested outcome here (validation, bad
        # cursors, idempotency conflicts) and shouldn't fill the log.
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
