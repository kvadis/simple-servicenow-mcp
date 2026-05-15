"""Script reading tools: business rules, script includes, client scripts, UI policies, UI actions, get body.

Returns native types so FastMCP emits structuredContent.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_BR_FIELDS = "sys_id,name,collection,when,order,active,script,sys_scope,description"
_SI_FIELDS = "sys_id,name,api_name,client_callable,active,script,sys_scope,description"
_CS_FIELDS = "sys_id,name,table,type,ui_type,active,script,sys_scope,description"
_UP_FIELDS = "sys_id,short_description,table,conditions,active,on_load,reverse_if_false,sys_scope"
_UA_FIELDS = "sys_id,name,table,action_name,form_button,form_link,list_action,active,condition,script,sys_scope"
_BODY_FIELDS = "sys_id,name,script,active,sys_scope"


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_business_rules(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List business rules (sys_script) with optional filtering.

    Args:
        query: Encoded query (e.g. active=true^collection=incident)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all sys_script columns. Default returns a
            trimmed analyst-friendly set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    base_query = "ORDERBYcollection"
    if query:
        base_query = f"{query}^{base_query}"
    try:
        return await _client(ctx, instance).list_records(
            "sys_script",
            query=base_query,
            fields=None if verbose else _BR_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def list_script_includes(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List script includes (sys_script_include) with optional filtering.

    Args:
        query: Encoded query (e.g. active=true^sys_scope=global)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all sys_script_include columns. Default returns
            a trimmed analyst-friendly set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    base_query = "ORDERBYname"
    if query:
        base_query = f"{query}^{base_query}"
    try:
        return await _client(ctx, instance).list_records(
            "sys_script_include",
            query=base_query,
            fields=None if verbose else _SI_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def list_client_scripts(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List client scripts (sys_script_client) with optional filtering.

    Args:
        query: Encoded query (e.g. active=true^table=incident)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all columns. Default returns a trimmed set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    base_query = "ORDERBYtable"
    if query:
        base_query = f"{query}^{base_query}"
    try:
        return await _client(ctx, instance).list_records(
            "sys_script_client",
            query=base_query,
            fields=None if verbose else _CS_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def list_ui_policies(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List UI policies (sys_ui_policy) with optional filtering.

    Args:
        query: Encoded query (e.g. active=true^table=incident)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all columns. Default returns a trimmed set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    base_query = "ORDERBYtable"
    if query:
        base_query = f"{query}^{base_query}"
    try:
        return await _client(ctx, instance).list_records(
            "sys_ui_policy",
            query=base_query,
            fields=None if verbose else _UP_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def list_ui_actions(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List UI actions (sys_ui_action) — form buttons, links, and list actions.

    Args:
        query: Encoded query (e.g. active=true^table=incident)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all columns. Default returns a trimmed set.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    base_query = "ORDERBYtable"
    if query:
        base_query = f"{query}^{base_query}"
    try:
        return await _client(ctx, instance).list_records(
            "sys_ui_action",
            query=base_query,
            fields=None if verbose else _UA_FIELDS,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_script_body(
    table: Literal["sys_script", "sys_script_include", "sys_script_client", "sys_ui_policy", "sys_ui_action"],
    sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> dict[str, Any]:
    """Get the full script body of a business rule, script include, client script, UI policy, or UI action.

    Args:
        table: Script table (sys_script, sys_script_include, sys_script_client, sys_ui_policy, sys_ui_action)
        sys_id: The sys_id of the script record
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).get_record(table, sys_id, fields=_BODY_FIELDS)
    except Exception as e:
        raise ToolError(str(e)) from e
