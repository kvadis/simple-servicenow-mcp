"""Failing tests driving the not-yet-implemented `audit_scope` tool.

`audit_scope` is the first analytical-intelligence tool: given a scope name,
it pulls every script-bearing record in that scope (business rules + script
includes) and surfaces patterns that put the scope at risk during an upgrade —
hardcoded sys_ids and deprecated APIs.

The implementation lives in a module that does not yet exist
(``simple_servicenow_mcp.tools.audit``). These tests must fail with
ImportError / ModuleNotFoundError until GREEN.

Behaviour-style names per Testing Conventions in AGENTS.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Sample data reused by several tests ─────────────────────────────────

_SCOPE_RECORD = {"sys_id": "abc1111111111111111111111111aaaa", "scope": "x_co_app"}
_HARDCODED_SYS_ID_IN_SCRIPT = "deadbeefdeadbeefdeadbeefdeadbeef"
_BR_WITH_HARDCODED = {
    "sys_id": "br00000000000000000000000000001",
    "name": "Set assignment from group",
    "script": (
        "if (current.assignment_group == '" + _HARDCODED_SYS_ID_IN_SCRIPT + "') {\n"
        "  current.assigned_to = gs.getUserID();\n"
        "}"
    ),
}
_SI_WITH_GS_PRINT = {
    "sys_id": "si00000000000000000000000000001",
    "name": "LegacyLogger",
    "script": "gs.print('legacy log line');",
}
_BR_CLEAN = {
    "sys_id": "br00000000000000000000000000002",
    "name": "Clean rule",
    "script": "gs.info('all good');",
}


# ── 1. Module/tool existence ────────────────────────────────────────────


def test_audit_scope_tool_should_exist_in_audit_module() -> None:
    """audit_scope must be importable from tools.audit — module doesn't exist yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import audit as audit_module

    # Assert
    assert hasattr(audit_module, "audit_scope"), (
        "audit_scope is not yet implemented in simple_servicenow_mcp.tools.audit"
    )


# ── 2-3. Scope resolution against sys_scope ─────────────────────────────


async def test_audit_scope_should_resolve_scope_via_sys_scope_first(
    fake_client,
    fake_ctx,
) -> None:
    """First REST call must be a sys_scope lookup keyed on the scope name."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = []
    fake_client.list_responses["sys_script_include"] = []

    # Act
    await audit_scope("x_co_app", fake_ctx)

    # Assert
    assert fake_client.calls, "audit_scope made no REST calls"
    first = fake_client.calls[0]
    assert first.table == "sys_scope"
    assert "scope=x_co_app" in (first.kwargs.get("query") or "")


async def test_audit_scope_should_return_error_json_when_scope_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown scope returns a JSON error payload — must not raise."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = []  # no match

    # Act
    result = await audit_scope("x_does_not_exist", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "x_does_not_exist" in parsed["error"]


# ── 4-5. Querying the script tables by resolved scope sys_id ────────────


async def test_audit_scope_should_query_business_rules_filtered_by_scope_sys_id(
    fake_client,
    fake_ctx,
) -> None:
    """After resolution, sys_script is queried filtered by the resolved scope's sys_id."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = []
    fake_client.list_responses["sys_script_include"] = []

    # Act
    await audit_scope("x_co_app", fake_ctx)

    # Assert
    sys_script_calls = [c for c in fake_client.calls if c.table == "sys_script"]
    assert len(sys_script_calls) == 1
    query = sys_script_calls[0].kwargs.get("query") or ""
    assert _SCOPE_RECORD["sys_id"] in query, (
        f"sys_script query must filter by the resolved scope sys_id, got: {query!r}"
    )


async def test_audit_scope_should_query_script_includes_filtered_by_scope_sys_id(
    fake_client,
    fake_ctx,
) -> None:
    """After resolution, sys_script_include is queried filtered by the scope sys_id."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = []
    fake_client.list_responses["sys_script_include"] = []

    # Act
    await audit_scope("x_co_app", fake_ctx)

    # Assert
    si_calls = [c for c in fake_client.calls if c.table == "sys_script_include"]
    assert len(si_calls) == 1
    query = si_calls[0].kwargs.get("query") or ""
    assert _SCOPE_RECORD["sys_id"] in query, (
        f"sys_script_include query must filter by the resolved scope sys_id, got: {query!r}"
    )


# ── 6-7. Detector rules ─────────────────────────────────────────────────


async def test_audit_scope_should_flag_hardcoded_sys_id_in_script_body(
    fake_client,
    fake_ctx,
) -> None:
    """A 32-char hex string in a script body produces a hardcoded_sys_id finding."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = [_BR_WITH_HARDCODED]
    fake_client.list_responses["sys_script_include"] = []

    # Act
    result = await audit_scope("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f
        for f in findings
        if f.get("type") == "hardcoded_sys_id" and f.get("sys_id") == _BR_WITH_HARDCODED["sys_id"]
    ]
    assert matching, (
        f"expected a hardcoded_sys_id finding for {_BR_WITH_HARDCODED['sys_id']}, "
        f"got findings: {findings!r}"
    )
    assert _HARDCODED_SYS_ID_IN_SCRIPT in (matching[0].get("evidence") or ""), (
        "finding should cite the hardcoded sys_id as evidence"
    )


async def test_audit_scope_should_flag_deprecated_gs_print_in_script_body(
    fake_client,
    fake_ctx,
) -> None:
    """`gs.print(` in a script body produces a deprecated_api finding."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = []
    fake_client.list_responses["sys_script_include"] = [_SI_WITH_GS_PRINT]

    # Act
    result = await audit_scope("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f
        for f in findings
        if f.get("type") == "deprecated_api" and f.get("sys_id") == _SI_WITH_GS_PRINT["sys_id"]
    ]
    assert matching, (
        f"expected a deprecated_api finding for gs.print in {_SI_WITH_GS_PRINT['sys_id']}, "
        f"got findings: {findings!r}"
    )
    assert "gs.print(" in (matching[0].get("evidence") or ""), (
        "finding should cite gs.print( as evidence"
    )


# ── 8. Audit summary metadata ───────────────────────────────────────────


async def test_audit_scope_should_return_records_audited_count_matching_total_scripts(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level records_audited equals business rules + script includes inspected."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = [_BR_CLEAN, _BR_WITH_HARDCODED]
    fake_client.list_responses["sys_script_include"] = [_SI_WITH_GS_PRINT]

    # Act
    result = await audit_scope("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("records_audited") == 3


async def test_audit_scope_should_page_past_the_100_row_clamp(fake_client, fake_ctx) -> None:
    """A scope with more than 100 business rules must be audited in full, and a
    finding on the second page must still be reported."""
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.pages[("sys_script", 0)] = [
        {**_BR_CLEAN, "sys_id": f"br{i:030d}"} for i in range(100)
    ]
    fake_client.pages[("sys_script", 100)] = [_BR_WITH_HARDCODED]
    fake_client.list_responses["sys_script_include"] = [_SI_WITH_GS_PRINT]

    parsed = json.loads(await audit_scope("x_co_app", fake_ctx))

    assert parsed["records_audited"] == 102
    assert parsed["truncated"] is False
    assert _BR_WITH_HARDCODED["sys_id"] in {f["sys_id"] for f in parsed["findings"]}


# ── 9. Scope identity in response (PDI-observed: scope records carry scope+name) ──


async def test_audit_scope_should_echo_scope_input_in_response(
    fake_client,
    fake_ctx,
) -> None:
    """Response must echo the scope key (e.g. ``x_co_app``) so callers can correlate.

    PDI ``sys_scope`` records expose both a machine ``scope`` field (e.g. ``global``)
    and a human ``name`` (e.g. ``"Global"``). Reports that omit the scope identity
    are hard to read when multiple scopes are audited.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = []
    fake_client.list_responses["sys_script_include"] = []

    # Act
    result = await audit_scope("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("scope") == "x_co_app"


# ── 10. Error wrapping ──────────────────────────────────────────────────


async def test_audit_scope_should_wrap_unexpected_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Unexpected client errors mid-audit must be re-raised as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_scope  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = []
    fake_client.raise_on_table["sys_script_include"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await audit_scope("x_co_app", fake_ctx)
