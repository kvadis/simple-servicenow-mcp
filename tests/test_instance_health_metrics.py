"""Failing tests driving the not-yet-implemented `instance_health_metrics` tool.

`instance_health_metrics` is the programmatic counterpart to the
`instance_health_check` *prompt* (`prompts.py:197-217`). Where the prompt
walks an LLM through 5 separate calls, the tool does the orchestration
itself and returns a single structured snapshot — useful both for direct
tool calls and as a building block for dashboards or scheduled checks.

Implementation should live in a new module
`simple_servicenow_mcp.tools.health` (alongside the existing
`servicenow://health` Resource, but distinct from it — the Resource is a
boolean connectivity probe, the tool is a metrics report).

Behaviour-style names per Testing Conventions in AGENTS.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Encoded-query strings exactly as the prompt specifies them ──────────

_Q_ACTIVE = "active=true"
_Q_ACTIVE_P1 = "active=true^priority=1"
_Q_UNASSIGNED = "active=true^assigned_toISEMPTY^ORDERBYDESCsys_created_on"
_Q_INFLIGHT_CHG = "active=true^state!=3^state!=7"
_Q_BUILD_NAME = "name=glide.product.build_name"


def _stub_healthy_instance(
    fake_client, active=0, p1=0, in_flight=0, unassigned=None, version="Yokohama"
):
    """Helper to lay down a complete, valid stub set for the 5 health calls."""
    fake_client.count_responses[("incident", _Q_ACTIVE)] = active
    fake_client.count_responses[("incident", _Q_ACTIVE_P1)] = p1
    fake_client.count_responses[("change_request", _Q_INFLIGHT_CHG)] = in_flight
    fake_client.list_responses["incident"] = unassigned or []
    fake_client.list_responses["sys_properties"] = [
        {"name": "glide.product.build_name", "value": version}
    ]


# ── 1. Module/tool existence ────────────────────────────────────────────


def test_instance_health_metrics_tool_should_exist_in_health_module() -> None:
    """instance_health_metrics must be importable from tools.health — not implemented yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import health as health_module

    # Assert
    assert hasattr(health_module, "instance_health_metrics"), (
        "instance_health_metrics is not yet implemented in simple_servicenow_mcp.tools.health"
    )


# ── 2-3. Incident counts ────────────────────────────────────────────────


async def test_instance_health_metrics_should_count_active_incidents(
    fake_client,
    fake_ctx,
) -> None:
    """Calls get_count on incident with `active=true` and returns the value as `active_incidents`."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    _stub_healthy_instance(fake_client, active=142)

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("active_incidents") == 142
    count_calls = [c for c in fake_client.calls if c.method == "count" and c.table == "incident"]
    assert any(c.kwargs.get("query") == _Q_ACTIVE for c in count_calls), (
        f"expected get_count('incident', '{_Q_ACTIVE}') call, got: {count_calls!r}"
    )


async def test_instance_health_metrics_should_count_p1_incidents(
    fake_client,
    fake_ctx,
) -> None:
    """Separate get_count call for P1s with the priority=1 clause."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    _stub_healthy_instance(fake_client, active=10, p1=3)

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("p1_incidents") == 3
    count_calls = [c for c in fake_client.calls if c.method == "count" and c.table == "incident"]
    assert any(c.kwargs.get("query") == _Q_ACTIVE_P1 for c in count_calls), (
        f"expected get_count('incident', '{_Q_ACTIVE_P1}') call, got: {count_calls!r}"
    )


# ── 4. Unassigned incidents list ────────────────────────────────────────


async def test_instance_health_metrics_should_list_top_unassigned_incidents(
    fake_client,
    fake_ctx,
) -> None:
    """Pulls the 5 newest unassigned active incidents and surfaces them as
    `unassigned_incidents`. Matches the prompt's limit=5 contract (prompts.py:206)."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    unassigned_rows = [
        {"number": f"INC001023{i}", "short_description": f"thing {i}", "priority": "3"}
        for i in range(5)
    ]
    _stub_healthy_instance(fake_client, unassigned=unassigned_rows)

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("unassigned_incidents") == unassigned_rows

    inc_list_calls = [c for c in fake_client.calls if c.method == "list" and c.table == "incident"]
    assert len(inc_list_calls) == 1
    assert inc_list_calls[0].kwargs.get("query") == _Q_UNASSIGNED
    assert inc_list_calls[0].kwargs.get("limit") == 5


# ── 5. Change request count ─────────────────────────────────────────────


async def test_instance_health_metrics_should_count_in_flight_change_requests(
    fake_client,
    fake_ctx,
) -> None:
    """Counts active change_requests excluding Closed (3) and Cancelled (7)."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    _stub_healthy_instance(fake_client, in_flight=17)

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("in_flight_changes") == 17
    chg_calls = [
        c for c in fake_client.calls if c.method == "count" and c.table == "change_request"
    ]
    assert chg_calls, "expected at least one count call against change_request"
    assert chg_calls[0].kwargs.get("query") == _Q_INFLIGHT_CHG


# ── 6. Instance version ─────────────────────────────────────────────────


async def test_instance_health_metrics_should_read_instance_version_from_sys_properties(
    fake_client,
    fake_ctx,
) -> None:
    """Reads `glide.product.build_name` from sys_properties and surfaces the value."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    _stub_healthy_instance(fake_client, version="Yokohama Patch 3")

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("instance_version") == "Yokohama Patch 3"

    prop_calls = [
        c for c in fake_client.calls if c.method == "list" and c.table == "sys_properties"
    ]
    assert len(prop_calls) == 1
    assert prop_calls[0].kwargs.get("query") == _Q_BUILD_NAME


# ── 7. Warning rules ────────────────────────────────────────────────────


async def test_instance_health_metrics_should_emit_warning_when_p1_count_is_nonzero(
    fake_client,
    fake_ctx,
) -> None:
    """Any active P1 should surface as a warning so a human sees it.

    The prompt at prompts.py:213 says "flag if P1 > 0". We pin a structured
    `warnings` list with a typed entry so callers can filter/sort.
    """
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    _stub_healthy_instance(fake_client, active=20, p1=2)

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    warnings = parsed.get("warnings", [])
    matching = [w for w in warnings if w.get("type") == "active_p1_incidents"]
    assert matching, f"expected an active_p1_incidents warning when P1 count > 0, got: {warnings!r}"


async def test_instance_health_metrics_should_not_emit_p1_warning_when_p1_count_is_zero(
    fake_client,
    fake_ctx,
) -> None:
    """Healthy state: no P1s in flight → no P1 warning. Lock this in so the
    detector doesn't fire on false positives."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    _stub_healthy_instance(fake_client, active=20, p1=0)

    # Act
    result = await instance_health_metrics(fake_ctx)

    # Assert
    parsed = json.loads(result)
    warnings = parsed.get("warnings", [])
    matching = [w for w in warnings if w.get("type") == "active_p1_incidents"]
    assert not matching, f"P1 warning must not fire when p1 count is 0, got: {warnings!r}"


# ── 8. Error wrapping ───────────────────────────────────────────────────


async def test_instance_health_metrics_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Underlying client failures mid-collection must surface as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.health import (
        instance_health_metrics,  # type: ignore[attr-defined]
    )

    fake_client.raise_on_count["incident"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await instance_health_metrics(fake_ctx)
