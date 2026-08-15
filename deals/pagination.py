"""Cursor pagination.

Keyset, not LIMIT/OFFSET. The cursor wraps the last primary key seen, so rows
inserted mid-pagination land ahead of the cursor instead of shifting the
window and duplicating a row onto the next page.

DRF ships a CursorPagination class, but it renders a `next`/`previous` URL
envelope and this API returns `{items, pageInfo}`; the keyset query underneath
is four lines, so it's implemented directly.
"""

from .exceptions import InvalidCursor
from .util import CursorError, decode_cursor, encode_cursor


def paginate(queryset, limit: int, cursor: str | None) -> tuple[list, dict]:
    """Newest-first page plus pageInfo. Queryset must be ordered by -pk."""
    if cursor:
        try:
            after_pk = decode_cursor(cursor)
        except CursorError as exc:
            raise InvalidCursor(str(exc)) from exc
        queryset = queryset.filter(pk__lt=after_pk)
    # An empty string is treated as "no cursor", so a client that serialises a
    # null cursor as "" gets page one rather than a 400.

    # Fetch one extra row to learn `hasMore` without a second COUNT(*).
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1].pk) if page and has_more else None
    return page, {"limit": limit, "hasMore": has_more, "nextCursor": next_cursor}
