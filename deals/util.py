"""Small shared helpers with no Django dependencies."""

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
CENTS = Decimal("0.01")


def brand_key(brand: str) -> str:
    """Normalise a brand for lookup: casefold, strip accents and punctuation.

    'Big Basket', 'bigbasket' and 'BIG-BASKET!' all resolve to 'bigbasket'.
    """
    s = unicodedata.normalize("NFKD", brand or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return _NON_ALNUM.sub("", s.casefold())


def money(value) -> Decimal:
    """Round to 2 dp, half-up. Money that rounds differently from the checkout
    page is a support ticket, so never bankers' rounding."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def jsonable(value):
    """Decimal -> JSON number, recursively, at the response boundary.

    Internal maths stays in Decimal; responses carry plain numbers so clients
    don't have to parse strings. Also makes payloads safe to persist in the
    idempotency JSONField.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def body_hash(payload: dict) -> str:
    """Stable hash of a request body, for detecting idempotency-key reuse."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class CursorError(ValueError):
    pass


def encode_cursor(pk: int) -> str:
    raw = json.dumps({"v": 1, "seq": int(pk)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    """Returns the pk the caller should read *after*. Raises CursorError."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CursorError("cursor is not a valid pagination token") from exc
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("seq"), int):
        raise CursorError("cursor is not a valid pagination token")
    return data["seq"]
