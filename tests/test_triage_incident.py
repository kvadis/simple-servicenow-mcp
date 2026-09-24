"""Failing tests driving the not-yet-implemented `triage_incident` tool.

`triage_incident` is the programmatic counterpart to the `triage_incident`
*prompt* (`prompts.py:153-193`). Given an incident number, it assembles
the full triage context — the incident record, its journal of work
notes/comments, and a shortlist of similar resolved incidents — and
returns the bundle as a single structured payload.

Implementation should live in `simple_servicenow_mcp.tools.incident`
alongside the existing incident-domain shortcuts.

Behaviour-style names per Testing Conventions in AGENTS.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Sample data ─────────────────────────────────────────────────────────

_INCIDENT_NUMBER = "INC0010234"
_INCIDENT_SYS_ID = "inc00000000000000000000000000abcd"

_INCIDENT_RECORD = {
    "sys_id": _INCIDENT_SYS_ID,
    "number": _INCIDENT_NUMBER,
    "short_description": "Password reset failing for finance users",
    "state": "2",
    "priority": "2",
}

_JOURNAL_ENTRY = {
    "sys_id": "jrn00000000000000000000000000001",
    "element_id": _INCIDENT_SYS_ID,
    "element": "work_notes",
    "value": "Tried IdP cache flush — no change.",
    "sys_created_on": "2026-05-14 08:21:00",
}

_SIMILAR_RESOLVED = {
    "sys_id": "inc00000000000000000000000000sim1",
    "number": "INC0009998",
    "short_description": "Password reset stuck for finance team",
    "state": "6",
    "close_code": "Solved (Permanently)",
    "close_notes": "IdP secret rotated.",
}


# ── 1. Tool existence ───────────────────────────────────────────────────


def test_triage_incident_tool_should_exist_in_incident_module() -> None:
    """triage_incident must be importable from tools.incident — not implemented yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import incident as incident_module

    # Assert
    assert hasattr(incident_module, "triage_incident"), (
        "triage_incident is not yet implemented in simple_servicenow_mcp.tools.incident"
    )


# ── 2-3. Incident resolution ────────────────────────────────────────────


async def test_triage_incident_should_resolve_incident_by_number_first(
    fake_client,
    fake_ctx,
) -> None:
    """First REST call must resolve the incident by its display number."""
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [_INCIDENT_RECORD]
    fake_client.list_responses["sys_journal_field"] = []

    # Act
    await triage_incident(_INCIDENT_NUMBER, fake_ctx)

    # Assert
    assert fake_client.calls, "triage_incident made no REST calls"
    first = fake_client.calls[0]
    assert first.table == "incident"
    query = first.kwargs.get("query") or ""
    assert f"number={_INCIDENT_NUMBER}" in query, (
        f"first call must look up the incident by number, got query: {query!r}"
    )


async def test_triage_incident_should_return_error_json_when_incident_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown incident number returns a JSON error payload — must not raise."""
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = []  # no match

    # Act
    result = await triage_incident("INC9999999", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "INC9999999" in parsed["error"]


# ── 4-5. Journal gathering ──────────────────────────────────────────────


async def test_triage_incident_should_query_journal_for_resolved_sys_id(
    fake_client,
    fake_ctx,
) -> None:
    """Journal lookup must filter by the incident's sys_id and the work_notes/comments fields.

    Encoded query taken verbatim from the prompt body (prompts.py:169).
    """
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [_INCIDENT_RECORD]
    fake_client.list_responses["sys_journal_field"] = [_JOURNAL_ENTRY]

    # Act
    await triage_incident(_INCIDENT_NUMBER, fake_ctx)

    # Assert
    jrn_calls = [c for c in fake_client.calls if c.table == "sys_journal_field"]
    assert len(jrn_calls) == 1
    query = jrn_calls[0].kwargs.get("query") or ""
    assert f"element_id={_INCIDENT_SYS_ID}" in query, (
        f"journal query must filter by the resolved sys_id, got: {query!r}"
    )
    assert "elementIN" in query and "work_notes" in query and "comments" in query, (
        f"journal query must restrict to work_notes/comments fields, got: {query!r}"
    )


async def test_triage_incident_should_surface_journal_entries_in_response(
    fake_client,
    fake_ctx,
) -> None:
    """Returned JSON must include the raw journal rows under a `journal` key."""
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [_INCIDENT_RECORD]
    fake_client.list_responses["sys_journal_field"] = [_JOURNAL_ENTRY]

    # Act
    result = await triage_incident(_INCIDENT_NUMBER, fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("journal") == [_JOURNAL_ENTRY]


# ── 6-7. Similar resolved incidents ─────────────────────────────────────


async def test_triage_incident_should_search_for_similar_resolved_incidents(
    fake_client,
    fake_ctx,
) -> None:
    """A second incident-table query searches for resolved/closed lookalikes.

    Contract from prompts.py:170: "filter the result to closed/resolved
    (state in 6,7)". The query must reference both state values.
    """
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [
        _INCIDENT_RECORD
    ]  # also returned for the similar search
    fake_client.list_responses["sys_journal_field"] = []

    # Act
    await triage_incident(_INCIDENT_NUMBER, fake_ctx)

    # Assert
    inc_calls = [c for c in fake_client.calls if c.table == "incident"]
    assert len(inc_calls) >= 2, (
        f"expected ≥2 incident table calls (lookup + similar search), got {len(inc_calls)}"
    )
    similar_call = inc_calls[1]
    query = similar_call.kwargs.get("query") or ""
    assert "stateIN6,7" in query or ("state=6" in query and "state=7" in query), (
        f"similar-incident query must restrict to resolved/closed state (6 or 7), got: {query!r}"
    )


async def test_triage_incident_should_derive_search_keywords_from_short_description(
    fake_client,
    fake_ctx,
) -> None:
    """The similar-incident query must search using words drawn from the
    incident's short_description — not the raw incident number or a hardcoded
    string. Loose check: the encoded query references at least one of the
    short_description's words via a LIKE clause.
    """
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [_INCIDENT_RECORD]
    fake_client.list_responses["sys_journal_field"] = []

    # Act
    await triage_incident(_INCIDENT_NUMBER, fake_ctx)

    # Assert
    inc_calls = [c for c in fake_client.calls if c.table == "incident"]
    similar_query = (inc_calls[1].kwargs.get("query") or "").lower()
    sd_words = _INCIDENT_RECORD["short_description"].lower().split()
    assert any(f"like{w}" in similar_query for w in sd_words), (
        f"similar-incident query must LIKE-match a word from short_description "
        f"({sd_words!r}), got: {similar_query!r}"
    )


# ── 8. Response shape ───────────────────────────────────────────────────


async def test_triage_incident_should_return_incident_journal_and_similar_keys(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level response must expose the three triage pillars."""
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [_INCIDENT_RECORD]
    fake_client.list_responses["sys_journal_field"] = [_JOURNAL_ENTRY]

    # Act
    result = await triage_incident(_INCIDENT_NUMBER, fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "incident" in parsed
    assert "journal" in parsed
    assert "similar_incidents" in parsed
    assert parsed["incident"].get("number") == _INCIDENT_NUMBER


# ── 9. Error wrapping ───────────────────────────────────────────────────


async def test_triage_incident_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Underlying client failures mid-collection must surface as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.incident import triage_incident  # type: ignore[attr-defined]

    fake_client.list_responses["incident"] = [_INCIDENT_RECORD]
    fake_client.raise_on_table["sys_journal_field"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await triage_incident(_INCIDENT_NUMBER, fake_ctx)
