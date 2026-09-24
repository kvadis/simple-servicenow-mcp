"""Explicit paging for tools that need more than one page.

``ServiceNowClient.list_records`` clamps every request at ``SN_MAX_PAGE_SIZE``
(default 100), so a single call with ``limit=500`` silently returns 100 rows.
Tools that summarise a whole set — an update set's changes, a scope's tables,
a table's schema — page here instead and tell the caller when they hit the cap.
"""

from __future__ import annotations

from typing import Any

from ..client import ServiceNowClient

PAGE_SIZE = 100


async def list_all(
    client: ServiceNowClient,
    table: str,
    *,
    query: str,
    fields: str,
    max_pages: int = 20,
    page_size: int = PAGE_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    """Page through ``table`` until a short page. Returns ``(rows, truncated)``.

    ``truncated`` is True when ``max_pages`` full pages came back, i.e. there
    may be more rows the caller has not seen.
    """
    rows: list[dict[str, Any]] = []
    for page in range(max_pages):
        batch = await client.list_records(
            table, query=query, fields=fields, limit=page_size, offset=page * page_size
        )
        rows.extend(batch)
        if len(batch) < page_size:
            return rows, False
    return rows, True
