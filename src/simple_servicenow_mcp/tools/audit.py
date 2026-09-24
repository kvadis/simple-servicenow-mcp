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

_ACL_TABLE = "sys_security_acl"
_TABLE_FIELDS = "sys_id,name,label,super_class.name"
_PAGE_SIZE = 100  # the client clamps at SN_MAX_PAGE_SIZE (default 100); page explicitly
_MAX_PAGES = 20
_ACL_CONCURRENCY = 8  # parallel stats calls per audit — enough to be fast, not enough to 429


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


async def _resolve_scope(client: ServiceNowClient, scope: str) -> dict[str, Any] | None:
    rows = await client.list_records(
        "sys_scope", query=f"scope={scope}", fields="sys_id,scope,name", limit=1
    )
    return rows[0] if rows else None


async def _list_all(
    client: ServiceNowClient, table: str, *, query: str, fields: str
) -> tuple[list[dict[str, Any]], bool]:
    """Page through ``table`` until a short page. Returns (rows, truncated)."""
    rows: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        batch = await client.list_records(
            table, query=query, fields=fields, limit=_PAGE_SIZE, offset=page * _PAGE_SIZE
        )
        rows.extend(batch)
        if len(batch) < _PAGE_SIZE:
            return rows, False
    return rows, True


def _acl_query(table_name: str) -> str:
    # Record ACLs are named after the table; field ACLs are ``table.field`` /
    # ``table.*``. The trailing dot keeps ``u_thing`` from claiming
    # ``u_thing_extra``'s ACLs. ``^OR`` binds to the preceding condition, so
    # this reads: type AND active AND (name = t OR name STARTSWITH "t.").
    return f"type=record^active=true^name={table_name}^ORnameSTARTSWITH{table_name}."


def _scan(record: dict[str, Any]) -> list[dict[str, Any]]:
    script = record.get("script") or ""
    findings: list[dict[str, Any]] = []
    sys_id = record.get("sys_id", "")
    name = record.get("name", "")

    for match in _HEX32.findall(script):
        if match == sys_id:
            continue
        findings.append(
            {
                "type": "hardcoded_sys_id",
                "sys_id": sys_id,
                "name": name,
                "evidence": match,
            }
        )

    if _GS_PRINT in script:
        findings.append(
            {
                "type": "deprecated_api",
                "sys_id": sys_id,
                "name": name,
                "evidence": _GS_PRINT,
            }
        )

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
        scope_row = await _resolve_scope(client, scope)
        if scope_row is None:
            return json.dumps({"error": f"scope not found: {scope}"})

        query = f"sys_scope={scope_row['sys_id']}"
        results = await asyncio.gather(
            *(
                client.list_records(table, query=query, fields=_SCRIPT_FIELDS, limit=_SCRIPT_LIMIT)
                for table in _SCRIPT_TABLES
            )
        )
        records = [r for batch in results for r in batch]

        findings: list[dict[str, Any]] = []
        for record in records:
            findings.extend(_scan(record))

        return json.dumps(
            {
                "scope": scope,
                "records_audited": len(records),
                "findings": findings,
            }
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"audit_scope failed: {e}") from e


@mcp.tool(annotations=_a.READ)
async def audit_acls(
    scope: str,
    ctx: Context,
    instance: str | None = None,
) -> str:
    """Find tables in a scope that have no access controls of their own.

    Resolves the scope against sys_scope, lists its tables from sys_db_object,
    and counts active record-type ACLs in sys_security_acl named ``<table>``
    (record-level) or ``<table>.<field>`` (field-level) for each one.

    A table with zero ACLs is reported as ``no_acls``:

    - ``severity="blocking"`` when the table extends nothing — only the
      platform wildcard (``*``) rules govern access to it.
    - ``severity="warning"`` when it extends another table (``inherits_from``),
      because ServiceNow falls back to the parent table's record ACLs.

    Args:
        scope: Scope name (e.g. ``x_co_app``).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        JSON string with ``scope``, ``tables_audited``, ``truncated`` (true if
        the scope has more than 2000 tables) and ``findings`` (list of
        ``{type, severity, table, label, sys_id, acl_count, remediation}``).
        On unknown scope returns ``{"error": "<msg>"}`` rather than raising.
    """
    client = _client(ctx, instance)
    try:
        scope_row = await _resolve_scope(client, scope)
        if scope_row is None:
            return json.dumps({"error": f"scope not found: {scope}"})

        tables, truncated = await _list_all(
            client,
            "sys_db_object",
            query=f"sys_scope={scope_row['sys_id']}",
            fields=_TABLE_FIELDS,
        )

        gate = asyncio.Semaphore(_ACL_CONCURRENCY)

        async def _count(table: dict[str, Any]) -> int:
            async with gate:
                return await client.get_count(_ACL_TABLE, _acl_query(table["name"]))

        counts = await asyncio.gather(*(_count(t) for t in tables))

        findings: list[dict[str, Any]] = []
        for table, acl_count in zip(tables, counts, strict=True):
            if acl_count > 0:
                continue
            name = table["name"]
            parent = table.get("super_class.name") or ""
            finding: dict[str, Any] = {
                "type": "no_acls",
                "severity": "warning" if parent else "blocking",
                "table": name,
                "label": table.get("label", ""),
                "sys_id": table.get("sys_id", ""),
                "acl_count": 0,
            }
            if parent:
                finding["inherits_from"] = parent
                finding["remediation"] = (
                    f"{name} relies on {parent}'s record ACLs. Add its own "
                    "read/write/create/delete ACLs if it needs tighter access than its parent."
                )
            else:
                finding["remediation"] = (
                    f"Add record-level ACLs (read/write/create/delete) for {name}; "
                    "until then only the platform wildcard (*) rules govern access to it."
                )
            findings.append(finding)

        return json.dumps(
            {
                "scope": scope,
                "tables_audited": len(tables),
                "truncated": truncated,
                "findings": findings,
            }
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"audit_acls failed: {e}") from e
