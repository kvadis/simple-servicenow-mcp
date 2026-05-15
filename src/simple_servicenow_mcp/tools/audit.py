"""Audit tools — surface upgrade-risk patterns across a scope's scripts."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
_GS_PRINT = "gs.print("
_SCRIPT_TABLES = ("sys_script", "sys_script_include")
_SCRIPT_FIELDS = "sys_id,name,script"
_SCRIPT_LIMIT = 200


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


def _scan(record: dict[str, Any]) -> list[dict[str, Any]]:
    script = record.get("script") or ""
    findings: list[dict[str, Any]] = []
    sys_id = record.get("sys_id", "")
    name = record.get("name", "")

    for match in _HEX32.findall(script):
        if match == sys_id:
            continue
        findings.append({
            "type": "hardcoded_sys_id",
            "sys_id": sys_id,
            "name": name,
            "evidence": match,
        })

    if _GS_PRINT in script:
        findings.append({
            "type": "deprecated_api",
            "sys_id": sys_id,
            "name": name,
            "evidence": _GS_PRINT,
        })

    return findings


@mcp.tool(annotations=_a.READ)
async def audit_scope(
    scope: str,
    ctx: Context,
    instance: str | None = None,
) -> str:
    """Audit a ServiceNow scope for upgrade-risk patterns in its scripts.

    Resolves the scope by name against sys_scope, then scans all business rules
    (sys_script) and script includes (sys_script_include) in that scope for
    hardcoded sys_ids and deprecated APIs (e.g. ``gs.print``).

    Args:
        scope: Scope name (e.g. ``x_co_app`` or ``global``).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        JSON string with ``scope``, ``records_audited`` and ``findings`` (list of
        ``{type, sys_id, name, evidence}``). On unknown scope returns
        ``{"error": "<msg>"}`` rather than raising.
    """
    client = _client(ctx, instance)
    try:
        scope_rows = await client.list_records(
            "sys_scope",
            query=f"scope={scope}",
            fields="sys_id,scope,name",
            limit=1,
        )
        if not scope_rows:
            return json.dumps({"error": f"scope not found: {scope}"})
        scope_sys_id = scope_rows[0]["sys_id"]

        query = f"sys_scope={scope_sys_id}"
        results = await asyncio.gather(*(
            client.list_records(table, query=query, fields=_SCRIPT_FIELDS, limit=_SCRIPT_LIMIT)
            for table in _SCRIPT_TABLES
        ))
        records = [r for batch in results for r in batch]

        findings: list[dict[str, Any]] = []
        for record in records:
            findings.extend(_scan(record))

        return json.dumps({
            "scope": scope,
            "records_audited": len(records),
            "findings": findings,
        })
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"audit_scope failed: {e}") from e
