"""CMDB tools: CIs, relationships, and class-based filters.

ServiceNow CMDB is a polymorphic store rooted at ``cmdb_ci``. Every concrete
class (``cmdb_ci_server``, ``cmdb_ci_database``, ``cmdb_ci_web_server`` …)
inherits from ``cmdb_ci`` and adds its own columns. Querying ``cmdb_ci`` with
``sysparm_query=sys_class_name=cmdb_ci_database`` returns *only* that class.
Relationships live in ``cmdb_rel_ci`` keyed by ``parent`` / ``child`` / ``type``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_CI_FIELDS = (
    "sys_id,name,sys_class_name,install_status,operational_status,"
    "asset_tag,serial_number,ip_address,fqdn,manufacturer,model_id,"
    "support_group,assigned_to,sys_updated_on"
)
_REL_FIELDS = "sys_id,parent,child,type,parent_descriptor,child_descriptor"


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_cis(
    ctx: Context,
    query: str | None = None,
    ci_class: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List Configuration Items from ``cmdb_ci``, optionally filtered to one class.

    Args:
        query: Encoded query (e.g. ``operational_status=1^manufacturer.name=Dell``)
        ci_class: Concrete CMDB class to filter on (e.g. ``cmdb_ci_server``,
            ``cmdb_ci_database``, ``cmdb_ci_web_server``). Uses
            ``sys_class_name=`` so the polymorphism stays correct.
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all CI columns; default returns the cross-class
            commonly-useful subset (name, class, status, asset, network, owner).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    parts: list[str] = []
    if ci_class:
        parts.append(f"sys_class_name={ci_class}")
    if query:
        parts.append(query)
    combined = "^".join(parts) if parts else None

    try:
        return await _client(ctx, instance).list_records(
            "cmdb_ci",
            query=combined,
            fields=None if verbose else _CI_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_ci(
    sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> dict[str, Any]:
    """Get a single CI by sys_id with display values resolved.

    The record is fetched from ``cmdb_ci`` regardless of the concrete class —
    ServiceNow returns the columns of the actual subclass.

    Args:
        sys_id: The sys_id of the CI
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).get_record("cmdb_ci", sys_id)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def list_ci_relationships(
    ci_sys_id: str,
    ctx: Context,
    direction: str = "both",
    limit: int = 50,
    instance: str | None = None,
) -> dict[str, Any]:
    """List CMDB relationships from ``cmdb_rel_ci`` for a given CI.

    A relationship is *outgoing* when the CI is the ``parent`` and *incoming*
    when it's the ``child``. The returned shape always splits the two so the
    LLM can reason about graph direction without re-keying.

    Args:
        ci_sys_id: The sys_id of the CI whose relationships you want
        direction: ``outgoing``, ``incoming``, or ``both`` (default)
        limit: Per-direction max (1-100, default 50)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    if direction not in {"outgoing", "incoming", "both"}:
        raise ToolError(
            f"direction must be 'outgoing', 'incoming', or 'both' (got {direction!r})"
        )
    client = _client(ctx, instance)
    outgoing: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    try:
        if direction in {"outgoing", "both"}:
            outgoing = await client.list_records(
                "cmdb_rel_ci",
                query=f"parent={ci_sys_id}",
                fields=_REL_FIELDS,
                limit=limit,
                display_value="true",
            )
        if direction in {"incoming", "both"}:
            incoming = await client.list_records(
                "cmdb_rel_ci",
                query=f"child={ci_sys_id}",
                fields=_REL_FIELDS,
                limit=limit,
                display_value="true",
            )
    except Exception as e:
        raise ToolError(str(e)) from e
    return {
        "ci_sys_id": ci_sys_id,
        "direction": direction,
        "outgoing": outgoing,
        "incoming": incoming,
        "total": len(outgoing) + len(incoming),
    }


@mcp.tool(annotations=_a.READ)
async def find_cis_by_class(
    ci_class: str,
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """Convenience: list CIs of a single concrete class without writing the encoded query.

    Equivalent to ``list_cis(ci_class="cmdb_ci_database", query=…)`` but reads
    more naturally to the LLM when it already knows the class.

    Args:
        ci_class: Concrete CMDB class (e.g. ``cmdb_ci_database``)
        query: Optional extra encoded query
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    parts = [f"sys_class_name={ci_class}"]
    if query:
        parts.append(query)
    try:
        return await _client(ctx, instance).list_records(
            "cmdb_ci",
            query="^".join(parts),
            fields=_CI_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e
