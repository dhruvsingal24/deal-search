"""Idempotency for write endpoints.

Contract:
  * Client sends `Idempotency-Key: <opaque string>` on a write.
  * First request under a key runs normally and the exact response body is
    stored against (key, endpoint, request_hash).
  * A replay with the same key *and* the same body returns the stored response
    verbatim, with `Idempotency-Replayed: true`.
  * A replay with the same key but a *different* body returns 409 rather than
    a misleading success — that's a client bug, and quietly returning the old
    answer would hide it.

Records live in the database, so the guarantee survives a restart, and are
pruned past their TTL on write.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .exceptions import IdempotencyConflict, InvalidIdempotencyKey
from .models import IdempotencyRecord
from .util import body_hash

MAX_KEY_LENGTH = 200


def normalise_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise InvalidIdempotencyKey(f"Idempotency-Key must be 1-{MAX_KEY_LENGTH} characters")
    return key


class Idempotency:
    """Per-request helper. With no key it degrades to a no-op, which is the
    correct default for a search: repeated searches are each recorded."""

    def __init__(self, key: str | None, endpoint: str, payload: dict):
        self.key = key
        self.endpoint = endpoint
        self.request_hash = body_hash(payload)

    def replay(self) -> tuple[int, dict] | None:
        if not self.key:
            return None
        cutoff = timezone.now() - timedelta(seconds=settings.DEALS_IDEMPOTENCY_TTL)
        record = IdempotencyRecord.objects.filter(
            key=self.key, endpoint=self.endpoint, created_at__gte=cutoff
        ).first()
        if record is None:
            return None
        if record.request_hash != self.request_hash:
            raise IdempotencyConflict(
                "this Idempotency-Key was already used with a different request body"
            )
        return record.status_code, record.response_body

    def remember(self, status_code: int, response_body: dict) -> None:
        if not self.key:
            return
        cutoff = timezone.now() - timedelta(seconds=settings.DEALS_IDEMPOTENCY_TTL)
        IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
        IdempotencyRecord.objects.update_or_create(
            key=self.key,
            endpoint=self.endpoint,
            defaults={
                "request_hash": self.request_hash,
                "status_code": status_code,
                "response_body": response_body,
            },
        )
