"""Service Catalog tools: categories, items, variables.

Returns native types so FastMCP emits structuredContent.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_CAT_FIELDS = "sys_id,title,description,parent,active"
_ITEM_FIELDS = "sys_id,name,short_description,category,active,order,type,sys_scope"
_VAR_FIELDS = "sys_id,name,question_text,type,mandatory,default_value,order,active,reference,lookup_table"
_MANDATORY_VAR_LIMIT = 5


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_catalog_categories(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List service catalog categories (sc_category).

    Args:
        query: Encoded query (e.g. active=true)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all columns. Default returns a trimmed set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).list_records(
            "sc_category",
            query=query,
            fields=None if verbose else _CAT_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def list_catalog_items(
    ctx: Context,
    category: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List service catalog items (sc_cat_item) with optional category filter.

    Args:
        category: Filter by category title (e.g. "Hardware")
        query: Additional encoded query
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all columns. Default returns a trimmed set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    parts: list[str] = []
    if category:
        parts.append(f"category.title={category}")
    if query:
        parts.append(query)
    combined = "^".join(parts) if parts else None

    try:
        return await _client(ctx, instance).list_records(
            "sc_cat_item",
            query=combined,
            fields=None if verbose else _ITEM_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_catalog_item_variables(
    item_sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """Get all variables (form fields) for a catalog item.

    Args:
        item_sys_id: The sys_id of the catalog item
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).list_records(
            "item_option_new",
            query=f"cat_item={item_sys_id}",
            fields=_VAR_FIELDS,
            limit=100,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def analyze_catalog_item(
    item_sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> str:
    """Analyse a catalog item for UX-quality issues.

    Pulls the item + its variables and reports findings for missing
    short_description, no variables, variables missing question_text, and
    mandatory-field overload.

    Args:
        item_sys_id: ``sys_id`` of the catalog item.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        JSON string with ``sys_id``, ``name``, ``variable_count`` and
        ``findings``. On unknown item returns ``{"error": "<msg>"}``.
    """
    client = _client(ctx, instance)
    try:
        items = await client.list_records(
            "sc_cat_item",
            query=f"sys_id={item_sys_id}",
            fields="sys_id,name,short_description,active",
            limit=1,
        )
        if not items:
            return json.dumps({"error": f"catalog item not found: {item_sys_id}"})
        item = items[0]

        variables = await client.list_records(
            "item_option_new",
            query=f"cat_item={item_sys_id}",
            fields=_VAR_FIELDS,
            limit=100,
        )

        findings: list[dict[str, Any]] = []

        if not (item.get("short_description") or "").strip():
            findings.append({"type": "missing_short_description", "sys_id": item_sys_id})

        if not variables:
            findings.append({"type": "no_variables", "sys_id": item_sys_id})

        for var in variables:
            if not (var.get("question_text") or "").strip():
                findings.append({
                    "type": "missing_question_text",
                    "sys_id": var.get("sys_id", ""),
                    "name": var.get("name", ""),
                })

        mandatory_count = sum(
            1 for v in variables if str(v.get("mandatory", "")).lower() == "true"
        )
        if mandatory_count > _MANDATORY_VAR_LIMIT:
            findings.append({
                "type": "excessive_mandatory_variables",
                "count": mandatory_count,
                "limit": _MANDATORY_VAR_LIMIT,
            })

        return json.dumps({
            "sys_id": item.get("sys_id", item_sys_id),
            "name": item.get("name", ""),
            "variable_count": len(variables),
            "findings": findings,
        })
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"analyze_catalog_item failed: {e}") from e
