"""Audit tools — surface upgrade-risk patterns across a scope's scripts."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a
from ._paging import list_all

_HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
_GS_PRINT = "gs.print("
_SCRIPT_TABLES = ("sys_script", "sys_script_include")
_SCRIPT_FIELDS = "sys_id,name,script"

_ACL_TABLE = "sys_security_acl"
_TABLE_FIELDS = "sys_id,name,label,super_class.name"
_ACL_CONCURRENCY = 8  # parallel stats calls per audit — enough to be fast, not enough to 429


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


async def _resolve_scope(client: ServiceNowClient, scope: str) -> dict[str, Any] | None:
    rows = await client.list_records(
        "sys_scope", query=f"scope={scope}", fields="sys_id,scope,name", limit=1
    )
    return rows[0] if rows else None


# ── upgrade_readiness_review ─────────────────────────────────────────

_CLIENT_TABLES = frozenset({"sys_script_client", "sys_ui_policy", "sys_ui_action"})

# table → (fields, query suffix, script-bearing fields)
# sys_ui_policy keeps its code in script_true/script_false, and both carry
# ``function onCondition() {}`` boilerplate even when scripting is off, so the
# discriminating filter is run_scripts=true rather than an ISNOTEMPTY check.
_REVIEW_TABLES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "sys_script": ("sys_id,name,when,script", "^active=true", ("script",)),
    "sys_script_include": ("sys_id,name,script", "^active=true", ("script",)),
    "sys_script_client": ("sys_id,name,script", "^active=true", ("script",)),
    "sys_ui_policy": (
        "sys_id,short_description,script_true,script_false",
        "^active=true^run_scripts=true",
        ("script_true", "script_false"),
    ),
    "sys_ui_action": ("sys_id,name,script", "^active=true^scriptISNOTEMPTY", ("script",)),
}

_SEVERITIES = ("blocking", "risk", "info")

_OUT_OF_SCOPE = [
    "Flows, subflows and actions (Flow Designer)",
    "Scheduled jobs, scripted REST resources and REST/SOAP messages",
    "UI pages, UI macros, widgets and Service Portal client controllers",
    "Modified OOTB records (compare against sys_metadata baselines separately)",
    "Unbounded GlideRecord queries and missing try/catch — heuristic checks omitted to avoid false positives",
]


@dataclass(frozen=True)
class _Rule:
    type: str
    severity: str
    pattern: re.Pattern[str]
    tables: frozenset[str] | None  # None = every table
    recommendation: str


_RULES: tuple[_Rule, ...] = (
    _Rule(
        "sync_ajax",
        "blocking",
        re.compile(r"getXMLWait\("),
        None,
        "Replace getXMLWait() with getXML(callback); synchronous AJAX is not "
        "supported in Service Portal or Next Experience.",
    ),
    _Rule(
        "dom_access",
        "blocking",
        re.compile(r"\bdocument\.|\$j\(|\$\$\("),
        _CLIENT_TABLES,
        "Remove direct DOM access; use g_form / g_list APIs. DOM manipulation only "
        "works in the legacy UI.",
    ),
    _Rule(
        "hardcoded_sys_id",
        "risk",
        _HEX32,
        None,
        "Move the sys_id into a system property or look the record up by name; "
        "it will not match on another instance.",
    ),
    _Rule(
        "current_update_in_rule",
        "risk",
        re.compile(r"current\.update\("),
        frozenset({"sys_script"}),
        "current.update() inside a before/after business rule re-fires rules and can "
        "recurse; a before rule should set fields and let the platform save.",
    ),
    _Rule(
        "legacy_log",
        "info",
        re.compile(r"gs\.log\("),
        None,
        "Use gs.info() / gs.warn() / gs.error(); gs.log() is legacy and unavailable "
        "in scoped applications.",
    ),
    _Rule(
        "deprecated_print",
        "info",
        re.compile(r"gs\.print\("),
        None,
        "Use gs.info(); gs.print() is legacy.",
    ),
)


def _line_of(text: str, pos: int) -> tuple[int, str]:
    """1-based line number and the stripped source line containing ``pos``."""
    line_no = text.count("\n", 0, pos) + 1
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    line = text[start : end if end != -1 else len(text)].strip()
    return line_no, line[:160]


def _review_record(
    table: str, record: dict[str, Any], script_fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    sys_id = record.get("sys_id", "")
    name = record.get("name") or record.get("short_description") or ""
    when = record.get("when", "")
    findings: list[dict[str, Any]] = []
    seen_sys_ids: set[str] = set()

    for field_name in script_fields:
        script = record.get(field_name) or ""
        if not script:
            continue
        for rule in _RULES:
            if rule.tables is not None and table not in rule.tables:
                continue
            if rule.type == "current_update_in_rule" and when not in ("before", "after"):
                continue
            for match in rule.pattern.finditer(script):
                if rule.type == "hardcoded_sys_id":
                    if match.group(0) == sys_id or match.group(0) in seen_sys_ids:
                        continue
                    seen_sys_ids.add(match.group(0))
                line_no, evidence = _line_of(script, match.start())
                findings.append(
                    {
                        "type": rule.type,
                        "severity": rule.severity,
                        "table": table,
                        "sys_id": sys_id,
                        "name": name,
                        "field": field_name,
                        "line": line_no,
                        "evidence": evidence,
                        "recommendation": rule.recommendation,
                    }
                )
                if rule.type != "hardcoded_sys_id":
                    break  # one finding per rule per field is enough for a ticket
    return findings


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
        JSON string with ``scope``, ``records_audited``, ``truncated`` (a page
        cap was hit, so the audit is partial) and ``findings`` (list of
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
                list_all(client, table, query=query, fields=_SCRIPT_FIELDS)
                for table in _SCRIPT_TABLES
            )
        )
        records = [r for batch, _ in results for r in batch]

        findings: list[dict[str, Any]] = []
        for record in records:
            findings.extend(_scan(record))

        return json.dumps(
            {
                "scope": scope,
                "records_audited": len(records),
                "truncated": any(was_truncated for _, was_truncated in results),
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

        tables, truncated = await list_all(
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


@mcp.tool(annotations=_a.READ)
async def upgrade_readiness_review(
    scope: str,
    ctx: Context,
    instance: str | None = None,
    target_version: str | None = None,
) -> str:
    """Grade a scope's scripts for upgrade risk: blocking, risk or info.

    Resolves the scope against sys_scope, then pulls every active business rule,
    script include, client script, scripted UI policy (``run_scripts=true``) and
    scripted UI action in it, and runs a fixed set of detectors over the source:

    - ``blocking``: ``getXMLWait()`` (synchronous AJAX); direct DOM access
      (``document.``, ``$j(``, ``$$(``) in client-side scripts.
    - ``risk``: hardcoded sys_ids; ``current.update()`` in before/after rules.
    - ``info``: ``gs.log()``, ``gs.print()``.

    Only patterns that cannot misfire are checked; heuristics such as unbounded
    queries or missing try/catch are listed under ``out_of_scope`` instead.

    Args:
        scope: Scope name (e.g. ``x_co_app``).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
        target_version: Optional release name (e.g. ``Zurich``), echoed back so
            the report can be cross-referenced with that release's deprecations.

    Returns:
        JSON string with ``scope``, ``target_version``, ``records_audited``,
        ``per_table``, ``truncated``, ``severity_counts``, ``verdict``
        (``red`` / ``yellow`` / ``green``), ``findings`` (list of ``{type,
        severity, table, sys_id, name, field, line, evidence, recommendation}``)
        and ``out_of_scope``. On unknown scope returns ``{"error": "<msg>"}``.
    """
    client = _client(ctx, instance)
    try:
        scope_row = await _resolve_scope(client, scope)
        if scope_row is None:
            return json.dumps({"error": f"scope not found: {scope}"})
        base_query = f"sys_scope={scope_row['sys_id']}"

        pages = await asyncio.gather(
            *(
                list_all(client, table, query=base_query + suffix, fields=fields)
                for table, (fields, suffix, _) in _REVIEW_TABLES.items()
            )
        )

        findings: list[dict[str, Any]] = []
        per_table: dict[str, int] = {}
        truncated = False
        for (table, (_, _, script_fields)), (rows, was_truncated) in zip(
            _REVIEW_TABLES.items(), pages, strict=True
        ):
            per_table[table] = len(rows)
            truncated = truncated or was_truncated
            for record in rows:
                findings.extend(_review_record(table, record, script_fields))

        counts = {s: 0 for s in _SEVERITIES}
        for finding in findings:
            counts[finding["severity"]] += 1
        verdict = "red" if counts["blocking"] else "yellow" if counts["risk"] else "green"

        return json.dumps(
            {
                "scope": scope,
                "target_version": target_version,
                "records_audited": sum(per_table.values()),
                "per_table": per_table,
                "truncated": truncated,
                "severity_counts": counts,
                "verdict": verdict,
                "findings": findings,
                "out_of_scope": _OUT_OF_SCOPE,
            }
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"upgrade_readiness_review failed: {e}") from e
