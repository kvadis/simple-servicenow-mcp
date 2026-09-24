"""Failing tests driving the not-yet-implemented `find_orphaned_records` tool.

`find_orphaned_records` is a data-quality scan: given any table and one of
its reference fields, find records whose reference points at a sys_id that
no longer exists in the target table. This is the classic "left a dangling
pointer behind" symptom of half-finished bulk deletes, scope migrations,
or broken imports.

Flow:
  1. Resolve the reference field's *target table* via sys_dictionary
     (filter ``name=<table>^element=<ref_field>^internal_type=reference``).
     If the field doesn't exist OR isn't a reference, return error JSON.
  2. Walk the source ``table`` paging through records with non-empty
     ``ref_field`` values.
  3. Validate each unique referenced sys_id against the target table.
  4. Surface every source record whose ref value didn't resolve.

Implementation should live in `simple_servicenow_mcp.tools.audit` (sibling
of `audit_scope`, `upgrade_readiness_review`, `compare_scopes`,
`audit_acls`).

Behaviour-style names per Testing Conventions in AGENTS.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Sample data ─────────────────────────────────────────────────────────

_DICT_REF = {
    "sys_id": "dict0000000000000000000000000001",
    "name": "incident",
    "element": "assigned_to",
    "internal_type": "reference",
    "reference": "sys_user",
}
_DICT_NOT_REFERENCE = {
    "sys_id": "dict0000000000000000000000000002",
    "name": "incident",
    "element": "short_description",
    "internal_type": "string",
    "reference": "",
}

_LIVE_USER_SYS_ID = "userLiveLiveLiveLiveLiveLiveLive"
_DEAD_USER_SYS_ID = "userDeadDeadDeadDeadDeadDeadDead"

# Source-table rows: one points at a live user, one at a dead user, one is empty.
_INC_HEALTHY = {
    "sys_id": "inc00000000000000000000000healthy",
    "number": "INC0010001",
    "assigned_to": _LIVE_USER_SYS_ID,
}
_INC_ORPHAN = {
    "sys_id": "inc00000000000000000000000orphand",
    "number": "INC0010002",
    "assigned_to": _DEAD_USER_SYS_ID,
}
_INC_UNASSIGNED = {
    "sys_id": "inc00000000000000000000empty_ref",
    "number": "INC0010003",
    "assigned_to": "",
}

# Target-table rows: only the live user resolves.
_USER_LIVE = {"sys_id": _LIVE_USER_SYS_ID, "user_name": "alice"}


# ── 1. Tool existence ───────────────────────────────────────────────────


def test_find_orphaned_records_tool_should_exist_in_audit_module() -> None:
    """find_orphaned_records must be importable from tools.audit — not implemented yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import audit as audit_module

    # Assert
    assert hasattr(audit_module, "find_orphaned_records"), (
        "find_orphaned_records is not yet implemented in simple_servicenow_mcp.tools.audit"
    )


# ── 2-4. Field-definition resolution ────────────────────────────────────


async def test_find_orphaned_records_should_resolve_field_via_sys_dictionary_first(
    fake_client,
    fake_ctx,
) -> None:
    """First REST call must look up the ref_field definition in sys_dictionary.

    The encoded query must constrain by *both* the source table (``name``)
    AND the field name (``element``) so the right row is found — fields
    are not globally unique by name.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.list_responses["incident"] = []
    fake_client.list_responses["sys_user"] = []

    # Act
    await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    assert fake_client.calls, "find_orphaned_records made no REST calls"
    first = fake_client.calls[0]
    assert first.table == "sys_dictionary"
    query = first.kwargs.get("query") or ""
    assert "name=incident" in query, (
        f"sys_dictionary query must filter by source table, got: {query!r}"
    )
    assert "element=assigned_to" in query, (
        f"sys_dictionary query must filter by field name, got: {query!r}"
    )


async def test_find_orphaned_records_should_return_error_json_when_field_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown field returns an error payload — must not raise."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = []  # field doesn't exist

    # Act
    result = await find_orphaned_records("incident", "u_made_up", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "u_made_up" in parsed["error"]


async def test_find_orphaned_records_should_return_error_json_when_field_is_not_a_reference(
    fake_client,
    fake_ctx,
) -> None:
    """A scalar field (e.g. string) has no target table — must reject with error JSON.

    Without this guard the tool would silently scan nothing and lie that
    everything is clean.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_NOT_REFERENCE]

    # Act
    result = await find_orphaned_records("incident", "short_description", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    # Error message must explain *why* — to surface "not a reference field"
    # so the human/LLM knows to pick a different field.
    msg = parsed["error"].lower()
    assert "reference" in msg, (
        f"error must explain that the field is not a reference type, got: {parsed['error']!r}"
    )


# ── 5. Source-table walk ────────────────────────────────────────────────


async def test_find_orphaned_records_should_walk_source_table_for_non_empty_refs(
    fake_client,
    fake_ctx,
) -> None:
    """After resolving the field, the tool queries the source table filtered
    to records where ref_field is set (``<ref_field>ISNOTEMPTY``)."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.list_responses["incident"] = [_INC_HEALTHY, _INC_ORPHAN]
    fake_client.list_responses["sys_user"] = [_USER_LIVE]

    # Act
    await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    inc_calls = [c for c in fake_client.calls if c.method == "list" and c.table == "incident"]
    assert inc_calls, "expected at least one list call against the source table"
    query = inc_calls[0].kwargs.get("query") or ""
    assert "assigned_toISNOTEMPTY" in query, (
        f"source-table query must restrict to non-empty refs via ISNOTEMPTY, got: {query!r}"
    )


# ── 6-7. Target-table validation ────────────────────────────────────────


async def test_find_orphaned_records_should_validate_refs_against_target_table(
    fake_client,
    fake_ctx,
) -> None:
    """Referenced sys_ids must be checked against the target table (sys_user here).

    The exact lookup shape (per-ref get vs batched sys_idIN list) is left
    flexible — the test pins only that the tool *consults* the target
    table at all.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.list_responses["incident"] = [_INC_HEALTHY, _INC_ORPHAN]
    fake_client.list_responses["sys_user"] = [_USER_LIVE]  # only LIVE resolves

    # Act
    await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    user_calls = [
        c for c in fake_client.calls if c.table == "sys_user" and c.method in ("list", "get")
    ]
    assert user_calls, "expected at least one lookup against the target table sys_user — none found"


async def test_find_orphaned_records_should_surface_records_whose_ref_does_not_resolve(
    fake_client,
    fake_ctx,
) -> None:
    """The orphan record (assigned_to → dead user) must appear in `orphans`.
    The healthy record must NOT appear.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.list_responses["incident"] = [_INC_HEALTHY, _INC_ORPHAN]
    fake_client.list_responses["sys_user"] = [_USER_LIVE]

    # Act
    result = await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    parsed = json.loads(result)
    orphans = parsed.get("orphans", [])
    orphan_sys_ids = {o.get("sys_id") for o in orphans}
    assert _INC_ORPHAN["sys_id"] in orphan_sys_ids, (
        f"orphan incident must be surfaced, got orphans: {orphans!r}"
    )
    assert _INC_HEALTHY["sys_id"] not in orphan_sys_ids, (
        f"healthy incident must not be flagged, got orphans: {orphans!r}"
    )


async def test_find_orphaned_records_should_attach_dead_ref_value_to_each_orphan(
    fake_client,
    fake_ctx,
) -> None:
    """Each orphan finding must carry the dead reference value so the human
    operator can see *what* was missing, not just that something was."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.list_responses["incident"] = [_INC_ORPHAN]
    fake_client.list_responses["sys_user"] = []  # nothing resolves

    # Act
    result = await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    parsed = json.loads(result)
    orphans = parsed.get("orphans", [])
    assert orphans, "expected at least one orphan"
    target = next((o for o in orphans if o.get("sys_id") == _INC_ORPHAN["sys_id"]), None)
    assert target is not None, f"orphan missing for sys_id {_INC_ORPHAN['sys_id']!r}"
    assert target.get("ref_value") == _DEAD_USER_SYS_ID, (
        f"orphan must include the dead ref_value, got: {target!r}"
    )


# ── 8. Negative cases ───────────────────────────────────────────────────


async def test_find_orphaned_records_should_not_flag_records_with_empty_ref_field(
    fake_client,
    fake_ctx,
) -> None:
    """Empty ref values are not orphans — they're just unassigned. Don't bring
    them in even if they leak through into the source-table response."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    # Even if ISNOTEMPTY filtering is bypassed and an empty row leaks through,
    # the tool must still discard it before flagging.
    fake_client.list_responses["incident"] = [_INC_UNASSIGNED]
    fake_client.list_responses["sys_user"] = []

    # Act
    result = await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    parsed = json.loads(result)
    orphans = parsed.get("orphans", [])
    orphan_sys_ids = {o.get("sys_id") for o in orphans}
    assert _INC_UNASSIGNED["sys_id"] not in orphan_sys_ids, (
        f"records with empty ref must not be flagged as orphans, got: {orphans!r}"
    )


# ── 9. Response shape ───────────────────────────────────────────────────


async def test_find_orphaned_records_should_echo_table_ref_field_and_target_table(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level response must include `table`, `ref_field`, and `target_table`
    so the diff is self-describing.

    `target_table` matters most — it's the only one not in the call args, so
    callers learn what was actually validated against.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.list_responses["incident"] = []
    fake_client.list_responses["sys_user"] = []

    # Act
    result = await find_orphaned_records("incident", "assigned_to", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("table") == "incident"
    assert parsed.get("ref_field") == "assigned_to"
    assert parsed.get("target_table") == "sys_user", (
        f"target_table must echo the reference target from sys_dictionary, got: {parsed!r}"
    )


# ── 10. Error wrapping ──────────────────────────────────────────────────


async def test_find_orphaned_records_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Underlying client failures mid-scan must surface as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        find_orphaned_records,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_dictionary"] = [_DICT_REF]
    fake_client.raise_on_table["incident"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await find_orphaned_records("incident", "assigned_to", fake_ctx)
