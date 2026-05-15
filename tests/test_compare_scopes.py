"""Failing tests driving the not-yet-implemented `compare_scopes` tool.

`compare_scopes` answers a real consultant question that no existing tool
covers: "What records exist in scope A but not scope B (and vice versa)?"
Use cases — pre-migration audits, finding records that drifted between
dev/staging/prod scopes, spotting orphaned customisations.

Diffs ONE table at a time (default `sys_script_include`) by an identity
field (default `name`). Returns three sets: only_in_a, only_in_b, in_both.

Implementation should live in `simple_servicenow_mcp.tools.audit`
alongside `audit_scope` and `upgrade_readiness_review`.

Behaviour-style names per Testing Conventions in CLAUDE.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Sample data ─────────────────────────────────────────────────────────

_SCOPE_A = {"sys_id": "scopeAaaaaaaaaaaaaaaaaaaaaaaaaaaa", "scope": "x_co_dev"}
_SCOPE_B = {"sys_id": "scopeBbbbbbbbbbbbbbbbbbbbbbbbbbbb", "scope": "x_co_prod"}

# Three script includes: one only in A, one only in B, one shared.
_SI_ONLY_A = {
    "sys_id": "si_a_only_00000000000000000000000",
    "name": "DevHelper",
    "api_name": "x_co_dev.DevHelper",
}
_SI_SHARED_IN_A = {
    "sys_id": "si_a_share_0000000000000000000000",
    "name": "SharedUtils",
    "api_name": "x_co_dev.SharedUtils",
}
_SI_ONLY_B = {
    "sys_id": "si_b_only_00000000000000000000000",
    "name": "ProdOnly",
    "api_name": "x_co_prod.ProdOnly",
}
_SI_SHARED_IN_B = {
    "sys_id": "si_b_share_0000000000000000000000",
    "name": "SharedUtils",
    "api_name": "x_co_prod.SharedUtils",
}


def _stub_two_scopes(fake_client):
    """Helper: prime sys_scope so the first two lookups resolve A then B."""
    # The fake client's list_responses returns the same list every time the same
    # table is queried — but compare_scopes queries sys_scope TWICE (once per
    # scope). The implementation passes the scope name in the query, so the fake
    # client can't disambiguate by query alone. Easiest test approach: stub the
    # response to contain BOTH scope records; the tool filters by query.
    fake_client.list_responses["sys_scope"] = [_SCOPE_A, _SCOPE_B]


# ── 1. Tool existence ───────────────────────────────────────────────────


def test_compare_scopes_tool_should_exist_in_audit_module() -> None:
    """compare_scopes must be importable from tools.audit — not implemented yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import audit as audit_module

    # Assert
    assert hasattr(audit_module, "compare_scopes"), (
        "compare_scopes is not yet implemented in simple_servicenow_mcp.tools.audit"
    )


# ── 2-3. Scope resolution ───────────────────────────────────────────────


async def test_compare_scopes_should_resolve_both_scopes_via_sys_scope(
    fake_client,
    fake_ctx,
) -> None:
    """Two sys_scope lookups happen, one per scope name, before any diff work."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.list_responses["sys_script_include"] = []

    # Act
    await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)

    # Assert
    scope_calls = [c for c in fake_client.calls if c.table == "sys_scope"]
    assert len(scope_calls) == 2, (
        f"expected 2 sys_scope lookups (one per scope), got {len(scope_calls)}"
    )
    queries = [c.kwargs.get("query") or "" for c in scope_calls]
    assert any("scope=x_co_dev" in q for q in queries), (
        f"missing scope_a lookup, queries: {queries!r}"
    )
    assert any("scope=x_co_prod" in q for q in queries), (
        f"missing scope_b lookup, queries: {queries!r}"
    )


async def test_compare_scopes_should_return_error_json_when_scope_a_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown scope_a returns an error payload identifying the missing scope."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_B]  # only B resolves

    # Act
    result = await compare_scopes("x_missing", "x_co_prod", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "x_missing" in parsed["error"]


async def test_compare_scopes_should_return_error_json_when_scope_b_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown scope_b returns an error payload identifying the missing scope."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    fake_client.list_responses["sys_scope"] = [_SCOPE_A]  # only A resolves

    # Act
    result = await compare_scopes("x_co_dev", "x_other_missing", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "x_other_missing" in parsed["error"]


# ── 4. Default table is sys_script_include ──────────────────────────────


async def test_compare_scopes_should_default_to_diffing_script_includes(
    fake_client,
    fake_ctx,
) -> None:
    """Omitting `table` defaults to sys_script_include — the most commonly
    scope-bleeding artefact in customer instances."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.list_responses["sys_script_include"] = []

    # Act
    await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)

    # Assert
    diffed_tables = {
        c.table for c in fake_client.calls if c.table not in ("sys_scope",) and c.method == "list"
    }
    assert "sys_script_include" in diffed_tables, (
        f"default table must be sys_script_include, queried: {diffed_tables!r}"
    )


async def test_compare_scopes_should_honour_explicit_table_override(
    fake_client,
    fake_ctx,
) -> None:
    """An explicit `table` argument diffs that table instead of the default."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.list_responses["sys_script"] = []

    # Act
    await compare_scopes("x_co_dev", "x_co_prod", fake_ctx, table="sys_script")

    # Assert
    diffed_tables = {
        c.table for c in fake_client.calls if c.table != "sys_scope" and c.method == "list"
    }
    assert "sys_script" in diffed_tables
    assert "sys_script_include" not in diffed_tables, (
        f"explicit table override must not also query the default table, queried: {diffed_tables!r}"
    )


# ── 5. Diff semantics ───────────────────────────────────────────────────


async def test_compare_scopes_should_populate_only_in_a_with_records_missing_from_b(
    fake_client,
    fake_ctx,
) -> None:
    """Records whose identity-field value appears in A but not B land in only_in_a."""
    # Arrange — multi-scope-table support: stub returns a *combined* list and
    # the tool filters by scope sys_id in the query. Test the response shape.
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    # All script includes the FakeClient knows about for both scopes:
    fake_client.list_responses["sys_script_include"] = [
        _SI_ONLY_A,
        _SI_SHARED_IN_A,
        _SI_ONLY_B,
        _SI_SHARED_IN_B,
    ]

    # Act
    result = await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)

    # Assert
    parsed = json.loads(result)
    only_in_a_names = {r.get("name") for r in parsed.get("only_in_a", [])}
    assert "DevHelper" in only_in_a_names, (
        f"DevHelper should appear only in scope A, got only_in_a: {only_in_a_names!r}"
    )
    assert "ProdOnly" not in only_in_a_names


async def test_compare_scopes_should_populate_only_in_b_with_records_missing_from_a(
    fake_client,
    fake_ctx,
) -> None:
    """Records whose identity-field value appears in B but not A land in only_in_b."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.list_responses["sys_script_include"] = [
        _SI_ONLY_A,
        _SI_SHARED_IN_A,
        _SI_ONLY_B,
        _SI_SHARED_IN_B,
    ]

    # Act
    result = await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)

    # Assert
    parsed = json.loads(result)
    only_in_b_names = {r.get("name") for r in parsed.get("only_in_b", [])}
    assert "ProdOnly" in only_in_b_names, (
        f"ProdOnly should appear only in scope B, got only_in_b: {only_in_b_names!r}"
    )
    assert "DevHelper" not in only_in_b_names


async def test_compare_scopes_should_populate_in_both_with_shared_identity_values(
    fake_client,
    fake_ctx,
) -> None:
    """Records whose identity-field value appears in BOTH scopes land in in_both.

    Shared records are the highest-risk diff entries — they're potential
    naming collisions or duplicated logic that drifted independently.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.list_responses["sys_script_include"] = [
        _SI_ONLY_A,
        _SI_SHARED_IN_A,
        _SI_ONLY_B,
        _SI_SHARED_IN_B,
    ]

    # Act
    result = await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)

    # Assert
    parsed = json.loads(result)
    in_both = parsed.get("in_both") or []
    # in_both can be reported as bare identity values OR as record dicts —
    # accept either shape (this is a contract decision deferred to GREEN).
    in_both_identities = {(r.get("name") if isinstance(r, dict) else r) for r in in_both}
    assert "SharedUtils" in in_both_identities, (
        f"SharedUtils exists in both scopes — expected in in_both, got: {in_both!r}"
    )


# ── 6. Echo scope names ─────────────────────────────────────────────────


async def test_compare_scopes_should_echo_both_scope_names_in_response(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level response must echo scope_a and scope_b so the diff is self-describing."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.list_responses["sys_script_include"] = []

    # Act
    result = await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("scope_a") == "x_co_dev"
    assert parsed.get("scope_b") == "x_co_prod"


# ── 7. Error wrapping ───────────────────────────────────────────────────


async def test_compare_scopes_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Underlying client failures mid-diff must surface as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import compare_scopes  # type: ignore[attr-defined]

    _stub_two_scopes(fake_client)
    fake_client.raise_on_table["sys_script_include"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await compare_scopes("x_co_dev", "x_co_prod", fake_ctx)
