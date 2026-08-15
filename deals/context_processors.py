"""Values every template needs, so views don't have to pass them."""

from django.conf import settings


def site(request):
    return {
        "currency": settings.DEALS_CURRENCY,
        # Surfaced in the footer so a demo deployment is honest about the fact
        # that its data will vanish on the next cold start.
        "ephemeral_database": getattr(settings, "EPHEMERAL_DATABASE", False),
    }
