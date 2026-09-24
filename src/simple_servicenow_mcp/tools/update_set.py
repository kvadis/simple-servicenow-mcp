"""Update Set tooling: list, fetch, change-set browsing, summarise.

Update Sets are ServiceNow's promotion vehicle for config changes. The set
itself lives in ``sys_update_set``; each captured change is a
``sys_update_xml`` row keyed back by ``update_set``.

These tools give the LLM enough to answer "what's in update set X" and "is it
safe to promote" without writing encoded queries by hand. Pair with the
``update_set_review`` prompt for an opinionated audit.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a
from ._paging import list_all

_SET_FIELDS = (
    "sys_id,name,description,state,application,parent,release_date,"
    "is_default,sys_created_by,sys_created_on,sys_updated_on"
)
_CHANGE_FIELDS = (
    "sys_id,name,type,target_name,action,update_set,category,"
    "sys_created_by,sys_created_on,sys_updated_on,payload"
)
_CHANGE_LIGHT_FIELDS = "sys_id,name,type,target_name,action,category,sys_created_by,sys_created_on"

# System tables — changes here are higher-risk and worth flagging in a summary.
_HIGH_RISK_TYPES = {
    "sys_security_acl",
    "sys_user_role",
    "sys_user_has_role",
    "sys_script",
    "sys_script_include",
    "sys_db_object",
    "sys_dictionary",
}


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_update_sets(
    ctx: Context,
    state: str | None = "in progress",
    application: str | None = None,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List update sets from ``sys_update_set``.

    Defaults to ``state=in progress`` because that's the actionable set. Pass
    ``state=None`` to widen, or e.g. ``state=complete`` for promotion candidates.

    Args:
        state: Workflow state choice value: ``in progress`` (with a space),
            ``complete``, or ``ignore``. ``None`` to skip.
        application: Filter by ``application`` sys_id or scope name (dot-walked).
        query: Extra encoded query
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    parts: list[str] = []
    if state:
        parts.append(f"state={state}")
    if application:
        parts.append(f"application={application}")
    if query:
        parts.append(query)
    combined = "^".join(parts) if parts else None

    try:
        return await _client(ctx, instance).list_records(
            "sys_update_set",
            query=combined,
            fields=_SET_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_update_set(
    sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> dict[str, Any]:
    """Fetch an update set's metadata plus its change count.

    Args:
        sys_id: sys_id of the ``sys_update_set`` record
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _client(ctx, instance)
    try:
        record = await client.get_record("sys_update_set", sys_id)
        change_count = await client.get_count("sys_update_xml", f"update_set={sys_id}")
    except Exception as e:
        raise ToolError(str(e)) from e
    return {"update_set": record, "change_count": change_count}


@mcp.tool(annotations=_a.READ)
async def list_update_set_changes(
    update_set_sys_id: str,
    ctx: Context,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List the ``sys_update_xml`` records captured by an update set.

    Excludes the heavy ``payload`` column by default — pass ``verbose=true``
    to include it (one payload can be tens of KiB of XML).

    Args:
        update_set_sys_id: sys_id of the parent ``sys_update_set``
        query: Extra encoded query (e.g. ``action=DELETE``)
        limit: Max records (1-100, default 50)
        offset: Pagination offset
        verbose: Include ``payload`` (raw XML)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    parts = [f"update_set={update_set_sys_id}"]
    if query:
        parts.append(query)

    try:
        return await _client(ctx, instance).list_records(
            "sys_update_xml",
            query="^".join(parts),
            fields=_CHANGE_FIELDS if verbose else _CHANGE_LIGHT_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def summarize_update_set(
    update_set_sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> dict[str, Any]:
    """Risk-aware summary of an update set.

    Aggregates changes by ``type`` and ``action`` (INSERT_OR_UPDATE / DELETE)
    and flags entries touching high-risk tables (ACLs, roles, business rules,
    script includes, schema). Designed to give the LLM a fast read on whether
    an update set is safe to promote without inspecting every XML payload.

    Args:
        update_set_sys_id: sys_id of the ``sys_update_set``
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        ``{update_set, total_changes, by_type, by_action, high_risk_changes,
        deletes}`` — ``high_risk_changes`` and ``deletes`` are lists, not just
        counts, so the LLM can quote specific entries.
    """
    client = _client(ctx, instance)
    try:
        record = await client.get_record("sys_update_set", update_set_sys_id, fields=_SET_FIELDS)
        # Up to 500 changes (5 pages) for the summary; deeper sets can be
        # browsed via list_update_set_changes. The client clamps single calls
        # at 100 rows, so this has to page.
        changes, truncated = await list_all(
            client,
            "sys_update_xml",
            query=f"update_set={update_set_sys_id}",
            fields=_CHANGE_LIGHT_FIELDS,
            max_pages=5,
        )
    except Exception as e:
        raise ToolError(str(e)) from e

    by_type: Counter[str] = Counter(c.get("type", "") for c in changes)
    by_action: Counter[str] = Counter(c.get("action", "") for c in changes)
    deletes = [c for c in changes if str(c.get("action", "")).upper() == "DELETE"]
    high_risk = [c for c in changes if c.get("type", "") in _HIGH_RISK_TYPES]

    return {
        "update_set": record,
        "total_changes": len(changes),
        "by_type": dict(by_type),
        "by_action": dict(by_action),
        "deletes": deletes,
        "high_risk_changes": high_risk,
        "truncated": truncated,
    }
