"""Knowledge Base tools: articles, search, retrieval.

The primary table is ``kb_knowledge``. Categories live in ``kb_category``,
bases in ``kb_knowledge_base``. We surface a search tool separately because
ServiceNow's keyword grammar across the body+short_description+keywords
columns is a common LLM stumbling block.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_KB_LIST_FIELDS = (
    "sys_id,number,short_description,workflow_state,kb_knowledge_base,"
    "kb_category,author,published,valid_to,view_count,sys_updated_on"
)
_KB_FULL_FIELDS = (
    "sys_id,number,short_description,text,article_body,workflow_state,"
    "kb_knowledge_base,kb_category,author,published,valid_to,view_count,"
    "keywords,meta,sys_updated_on"
)


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_knowledge_articles(
    ctx: Context,
    query: str | None = None,
    knowledge_base: str | None = None,
    state: str | None = "published",
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List Knowledge Base articles from ``kb_knowledge``.

    Defaults to ``workflow_state=published`` because the LLM rarely wants
    drafts. Pass ``state=None`` (or an explicit value like ``draft``) to widen.

    Args:
        query: Encoded query (e.g. ``view_count>100^author=...``)
        knowledge_base: sys_id (or display name) of the KB to filter on
        state: Workflow state filter — defaults to ``published``. ``None`` to skip.
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return the full article body fields too (expensive).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    parts: list[str] = []
    if state:
        parts.append(f"workflow_state={state}")
    if knowledge_base:
        parts.append(f"kb_knowledge_base={knowledge_base}")
    if query:
        parts.append(query)
    combined = "^".join(parts) if parts else None

    try:
        return await _client(ctx, instance).list_records(
            "kb_knowledge",
            query=combined,
            fields=_KB_FULL_FIELDS if verbose else _KB_LIST_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def search_knowledge(
    keyword: str,
    ctx: Context,
    limit: int = 10,
    offset: int = 0,
    state: str | None = "published",
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """Keyword search across knowledge articles (title + body + keywords).

    Builds an ``^OR``-joined LIKE query so the LLM doesn't have to know
    ServiceNow's encoded-query grammar. Defaults to published articles only.

    Args:
        keyword: Substring to match (case-insensitive)
        limit: Max records (1-50, default 10)
        offset: Pagination offset
        state: Workflow state to require (default ``published``; ``None`` to skip)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    if not keyword.strip():
        raise ToolError("keyword must be non-empty")
    search = "^OR".join(f"{f}LIKE{keyword}" for f in ("short_description", "text", "keywords"))
    parts = [search]
    if state:
        parts.append(f"workflow_state={state}")
    try:
        return await _client(ctx, instance).list_records(
            "kb_knowledge",
            query="^".join(parts),
            fields=_KB_LIST_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_knowledge_article(
    sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> dict[str, Any]:
    """Get the full body and metadata of one knowledge article.

    Args:
        sys_id: The sys_id of the ``kb_knowledge`` record
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).get_record(
            "kb_knowledge", sys_id, fields=_KB_FULL_FIELDS
        )
    except Exception as e:
        raise ToolError(str(e)) from e
