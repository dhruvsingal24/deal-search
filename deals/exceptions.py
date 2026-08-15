"""Uniform error envelope.

Every failure — validation, cursor, idempotency, 404, 405 — comes back as

    {"error": {"code": "...", "message": "...", "details": [...]}}

so clients branch on one shape.
"""

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class InvalidCursor(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_cursor"
    default_detail = "cursor is not a valid pagination token"


class InvalidIdempotencyKey(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_idempotency_key"
    default_detail = "Idempotency-Key is not valid"


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "idempotency_conflict"
    default_detail = "this Idempotency-Key was already used with a different request body"


def _flatten(detail, prefix: str = "") -> list[dict]:
    """DRF nests validation errors by field; flatten to a list clients can loop."""
    out: list[dict] = []
    if isinstance(detail, dict):
        for field, value in detail.items():
            path = f"{prefix}.{field}" if prefix else str(field)
            out.extend(_flatten(value, path))
    elif isinstance(detail, list):
        for item in detail:
            out.extend(_flatten(item, prefix))
    else:
        out.append(
            {
                "field": prefix,
                "message": str(detail),
                "type": getattr(detail, "code", "invalid"),
            }
        )
    return out


def error_envelope_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, ValidationError):
        # 422 rather than DRF's default 400: the body parsed fine, it just
        # failed the rules. Keeps 400 meaning "malformed request".
        return Response(
            {
                "error": {
                    "code": "validation_error",
                    "message": "request body failed validation",
                    "details": _flatten(exc.detail),
                }
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    code = getattr(exc, "default_code", None) or "http_error"
    detail = exc.detail if hasattr(exc, "detail") else str(exc)
    message = detail if isinstance(detail, str) else str(detail)
    response.data = {"error": {"code": code, "message": message}}
    return response
