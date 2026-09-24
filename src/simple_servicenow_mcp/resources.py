"""MCP Resources: instance info, health, table schema, app scopes."""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context

from .client import ServiceNowAPIError, ServiceNowClient
from .server import AppContext, mcp
from .tools._paging import list_all


def _app() -> AppContext:
    """The lifespan context of the request being served.

    Fixed-URI resources must not declare a ``ctx`` parameter: FastMCP treats
    any parameter as a sign of a URI template, and a template never appears in
    ``resources/list``. Fetch the context from the server instead.
    """
    app: AppContext = mcp.get_context().request_context.lifespan_context
    return app


@mcp.resource("servicenow://instance/info")
async def instance_info() -> str:
    """Connection info for the configured ServiceNow instance(s) (no secrets).

    In multi-instance mode, returns the full registry (names + URLs + default).
    In single-instance mode, returns the configured URL + auth method.
    """
    app = _app()
    if app.registry is not None:
        return json.dumps(
            {
                "mode": "multi-instance",
                "default": app.registry.default_name,
                "instances": {
                    name: _describe_client(client) for name, client in app.registry.clients.items()
                },
            }
        )
    info = _describe_client(app.client)
    info["mode"] = "single-instance"
    return json.dumps(info)


def _describe_client(client: ServiceNowClient) -> dict[str, object]:
    """Connection facts for one instance, secrets omitted."""
    info: dict[str, object] = {
        "url": client.settings.instance_url,
        "auth_method": client.settings.auth_method,
    }
    token = client.token_status()
    if token is not None:
        info["token"] = token
    return info


async def _check(client: ServiceNowClient) -> dict[str, object]:
    """One-row sys_user fetch; ``{"status": "ok"}`` or an error payload, never raises."""
    try:
        await client.list_records("sys_user", fields="sys_id", limit=1)
        return {
            "status": "ok",
            "instance": client.settings.instance_url,
            "auth_method": client.settings.auth_method,
        }
    except ServiceNowAPIError as e:
        payload: dict[str, object] = {
            "status": "error",
            "instance": client.settings.instance_url,
            "code": e.status,
            "message": e.message,
            "detail": e.detail,
        }
        token = client.token_status()
        if token is not None:
            payload["token"] = token
        return payload
    except Exception as e:  # network failures, DNS, etc.
        return {"status": "error", "instance": client.settings.instance_url, "message": str(e)}


@mcp.resource("servicenow://health")
async def health() -> str:
    """Validate connectivity + auth against the configured instance(s).

    Performs a one-row sys_user fetch per instance to confirm credentials work.
    Single-instance mode returns ``{"status": "ok"}`` or ``{"status": "error",
    ...}``; multi-instance mode returns ``{"status": <worst>, "instances":
    {name: ...}}``. Never raises, so clients can use it as a pre-flight check.
    """
    app = _app()
    if app.registry is None:
        return json.dumps(await _check(app.client))
    results = {name: await _check(client) for name, client in app.registry.clients.items()}
    overall = "ok" if all(r["status"] == "ok" for r in results.values()) else "error"
    return json.dumps(
        {"status": overall, "default": app.registry.default_name, "instances": results}
    )


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


@mcp.resource("servicenow://schema/{table_name}")
async def table_schema(table_name: str, ctx: Context) -> str:
    """Field definitions from sys_dictionary for a given table (own fields only).

    ``{"error": ...}`` on failure rather than a raw exception. Use the
    ``describe_table`` tool for inherited fields and a named instance.
    """
    try:
        records, truncated = await list_all(
            _client(ctx, None),
            "sys_dictionary",
            query=f"name={table_name}^internal_type!=collection",
            fields="element,column_label,internal_type,max_length,mandatory,reference,default_value,active",
        )
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"table": table_name, "truncated": truncated, "fields": records})


@mcp.resource("servicenow://scope/{scope_name}")
async def app_scope(scope_name: str, ctx: Context) -> str:
    """App scope metadata from sys_scope. ``{"error": ...}`` when unknown or unreachable."""
    try:
        records = await _client(ctx, None).list_records(
            "sys_scope",
            query=f"scope={scope_name}",
            fields="sys_id,scope,name,short_description,version,vendor,active",
            limit=1,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})
    if not records:
        return json.dumps({"error": f"Scope '{scope_name}' not found"})
    return json.dumps(records[0])
