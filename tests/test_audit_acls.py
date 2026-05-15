"""Failing tests driving the not-yet-implemented `audit_acls` tool.

`audit_acls` answers the highest-stakes consultant question that nothing in
this MCP currently covers: **which custom tables in this scope have no
access controls, and are therefore an exfiltration risk?**

The tool:
  1. Resolves the named scope against `sys_scope`.
  2. Enumerates custom tables in that scope via `sys_db_object`
     (filtered by `sys_scope=<sys_id>`).
  3. For each table, counts ACLs in `sys_security_acl` whose `name` starts
     with the table name (covers record-level and field-level ACLs).
  4. Flags tables with **zero** ACLs as `severity="blocking"`
     (`type="no_acls"`).

Implementation should live in `simple_servicenow_mcp.tools.audit` alongside
`audit_scope`, `upgrade_readiness_review`, and `compare_scopes`.

Behaviour-style names per Testing Conventions in CLAUDE.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError


# ── Sample data ─────────────────────────────────────────────────────────

_SCOPE_RECORD = {
    "sys_id": "scope00000000000000000000000app",
    "scope": "x_co_app",
    "name": "Customer App",
}

# A table with zero ACLs — the headline finding.
_TABLE_UNPROTECTED = {
    "sys_id": "tbl_unprot_00000000000000000000",
    "name": "u_unprotected_thing",
    "label": "Unprotected Thing",
}
# A table with one or more ACLs — must NOT be flagged.
_TABLE_PROTECTED = {
    "sys_id": "tbl_protec_00000000000000000000",
    "name": "u_protected_thing",
    "label": "Protected Thing",
}


# ── 1. Tool existence ───────────────────────────────────────────────────

def test_audit_acls_tool_should_exist_in_audit_module() -> None:
    """audit_acls must be importable from tools.audit — not implemented yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import audit as audit_module

    # Assert
    assert hasattr(audit_module, "audit_acls"), (
        "audit_acls is not yet implemented in simple_servicenow_mcp.tools.audit"
    )


# ── 2-3. Scope resolution ───────────────────────────────────────────────

async def test_audit_acls_should_resolve_scope_via_sys_scope_first(
    fake_client,
    fake_ctx,
) -> None:
    """First REST call must resolve the scope by name against sys_scope."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = []

    # Act
    await audit_acls("x_co_app", fake_ctx)

    # Assert
    assert fake_client.calls, "audit_acls made no REST calls"
    first = fake_client.calls[0]
    assert first.table == "sys_scope"
    query = first.kwargs.get("query") or ""
    assert "scope=x_co_app" in query, (
        f"first call must look up the scope by name, got query: {query!r}"
    )


async def test_audit_acls_should_return_error_json_when_scope_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown scope returns a JSON error payload — must not raise."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = []  # no match

    # Act
    result = await audit_acls("x_missing_scope", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "x_missing_scope" in parsed["error"]


# ── 4. Table enumeration ────────────────────────────────────────────────

async def test_audit_acls_should_enumerate_scope_tables_via_sys_db_object(
    fake_client,
    fake_ctx,
) -> None:
    """After scope resolution, query sys_db_object filtered by the scope's sys_id.

    This is how scoped custom tables are discovered. Pins the table source —
    NOT sys_dictionary, NOT sys_documentation.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = []

    # Act
    await audit_acls("x_co_app", fake_ctx)

    # Assert
    dbo_calls = [c for c in fake_client.calls if c.table == "sys_db_object"]
    assert len(dbo_calls) == 1, (
        f"expected exactly one sys_db_object query, got {len(dbo_calls)}"
    )
    query = dbo_calls[0].kwargs.get("query") or ""
    assert f"sys_scope={_SCOPE_RECORD['sys_id']}" in query, (
        f"sys_db_object query must filter by the resolved scope sys_id, got: {query!r}"
    )


# ── 5. ACL lookup ───────────────────────────────────────────────────────

async def test_audit_acls_should_check_acl_presence_for_each_table(
    fake_client,
    fake_ctx,
) -> None:
    """For each enumerated table, the tool must consult sys_security_acl.

    Counting is the natural shape (we only need presence, not bodies), so
    the implementation should use ``get_count`` against sys_security_acl
    with a name filter scoped to each table.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = [_TABLE_UNPROTECTED, _TABLE_PROTECTED]
    # Default count is 0; no per-query stubs needed for this assertion.

    # Act
    await audit_acls("x_co_app", fake_ctx)

    # Assert
    acl_calls = [c for c in fake_client.calls if c.table == "sys_security_acl"]
    assert len(acl_calls) >= 2, (
        f"expected ≥2 sys_security_acl checks (one per table), got {len(acl_calls)}"
    )
    queried_names = " ".join(
        c.kwargs.get("query") or "" for c in acl_calls
    )
    assert _TABLE_UNPROTECTED["name"] in queried_names, (
        f"ACL lookup must reference {_TABLE_UNPROTECTED['name']!r}, queries: {queried_names!r}"
    )
    assert _TABLE_PROTECTED["name"] in queried_names, (
        f"ACL lookup must reference {_TABLE_PROTECTED['name']!r}, queries: {queried_names!r}"
    )


# ── 6-7. Detector rules ─────────────────────────────────────────────────

async def test_audit_acls_should_flag_tables_with_zero_acls_as_blocking(
    fake_client,
    fake_ctx,
) -> None:
    """A table with zero matching ACLs is the headline finding — severity blocking."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = [_TABLE_UNPROTECTED]
    # No count stub → default 0 → unprotected.

    # Act
    result = await audit_acls("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f for f in findings
        if f.get("type") == "no_acls" and f.get("table") == _TABLE_UNPROTECTED["name"]
    ]
    assert matching, (
        f"expected a no_acls finding for {_TABLE_UNPROTECTED['name']!r}, got: {findings!r}"
    )
    assert matching[0].get("severity") == "blocking", (
        f"no_acls findings must be severity=blocking, got: {matching[0]!r}"
    )


async def test_audit_acls_should_not_flag_tables_with_at_least_one_acl(
    fake_client,
    fake_ctx,
) -> None:
    """Tables that have ACLs must not appear in findings — no false positives.

    Pinning the negative case prevents the detector from firing on healthy
    state, which is what reviewers care about most.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = [_TABLE_PROTECTED]
    # The exact ACL query the implementation will emit is unspecified at the
    # RED stage, so use count_defaults to make *every* sys_security_acl
    # count call return 3 — i.e. every table appears protected.
    fake_client.count_defaults["sys_security_acl"] = 3

    # Act
    result = await audit_acls("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f for f in findings
        if f.get("type") == "no_acls" and f.get("table") == _TABLE_PROTECTED["name"]
    ]
    assert not matching, (
        f"protected table must not appear in findings, got: {findings!r}"
    )


# ── 8. Response shape ───────────────────────────────────────────────────

async def test_audit_acls_should_echo_scope_and_tables_audited_count(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level response must surface scope name and the table-count audited.

    Useful for the LLM to summarise: "Audited 12 tables in x_co_app, 3 unprotected."
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = [_TABLE_UNPROTECTED, _TABLE_PROTECTED]

    # Act
    result = await audit_acls("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("scope") == "x_co_app"
    assert parsed.get("tables_audited") == 2, (
        f"tables_audited must reflect the sys_db_object count, got: {parsed.get('tables_audited')!r}"
    )


async def test_audit_acls_should_return_empty_findings_when_all_tables_protected(
    fake_client,
    fake_ctx,
) -> None:
    """Healthy state: every table has ACLs → findings list is empty (not missing)."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_db_object"] = [_TABLE_PROTECTED]
    fake_client.count_defaults["sys_security_acl"] = 5  # all protected

    # Act
    result = await audit_acls("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("findings") == [], (
        f"expected empty findings list when all tables protected, got: {parsed.get('findings')!r}"
    )


# ── 9. Error wrapping ───────────────────────────────────────────────────

async def test_audit_acls_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Underlying client failures mid-audit must surface as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import audit_acls  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.raise_on_table["sys_db_object"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await audit_acls("x_co_app", fake_ctx)
