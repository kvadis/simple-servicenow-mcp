"""MCP Resources: instance info, health, table schema, app scopes."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context

from .client import ServiceNowAPIError
from .server import AppContext, mcp


@mcp.resource("servicenow://instance/info")
async def instance_info(ctx: Context) -> str:
    """Connection info for the configured ServiceNow instance(s) (no secrets).

    In multi-instance mode, returns the full registry (names + URLs + default).
    In single-instance mode, returns the configured URL + auth method.
    """
    app: AppContext = ctx.request_context.lifespan_context
    if app.registry is not None:
        return json.dumps(
            {
                "mode": "multi-instance",
                "default": app.registry.default_name,
                "instances": {
                    name: {
                        "url": client.settings.instance_url,
                        "auth_method": client.settings.auth_method,
                    }
                    for name, client in app.registry.clients.items()
                },
            }
        )
    return json.dumps(
        {
            "mode": "single-instance",
            "url": app.settings.instance_url,
            "auth_method": app.settings.auth_method,
        }
    )


@mcp.resource("servicenow://health")
async def health(ctx: Context) -> str:
    """Validate connectivity + auth against the configured instance.

    Performs a one-row sys_user fetch to confirm credentials work. Returns
    ``{"status": "ok"}`` or ``{"status": "error", ...}`` — never raises, so
    clients can use this as a safe pre-flight check.
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        await app.client.list_records("sys_user", fields="sys_id", limit=1)
        return json.dumps(
            {
                "status": "ok",
                "instance": app.settings.instance_url,
                "auth_method": app.settings.auth_method,
            }
        )
    except ServiceNowAPIError as e:
        return json.dumps(
            {
                "status": "error",
                "code": e.status,
                "message": e.message,
                "detail": e.detail,
            }
        )
    except Exception as e:  # network failures, DNS, etc.
        return json.dumps({"status": "error", "message": str(e)})


@mcp.resource("servicenow://schema/{table_name}")
async def table_schema(table_name: str, ctx: Context) -> str:
    """Field definitions from sys_dictionary for a given table."""
    app: AppContext = ctx.request_context.lifespan_context
    records = await app.client.list_records(
        "sys_dictionary",
        query=f"name={table_name}^internal_type!=collection",
        fields="element,column_label,internal_type,max_length,mandatory,reference,default_value,active",
        limit=200,
    )
    return json.dumps(records)


@mcp.resource("servicenow://scope/{scope_name}")
async def app_scope(scope_name: str, ctx: Context) -> str:
    """App scope metadata from sys_scope."""
    app: AppContext = ctx.request_context.lifespan_context
    records = await app.client.list_records(
        "sys_scope",
        query=f"scope={scope_name}",
        fields="sys_id,scope,name,short_description,version,vendor,active",
        limit=1,
    )
    if not records:
        return json.dumps({"error": f"Scope '{scope_name}' not found"})
    return json.dumps(records[0])
