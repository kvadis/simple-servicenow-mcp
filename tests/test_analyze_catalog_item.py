"""Failing tests driving the not-yet-implemented `analyze_catalog_item` tool.

`analyze_catalog_item` is the per-item analytical counterpart to the
`analyze_catalog` *prompt* (`prompts.py:42-71`). Given a catalog item's
sys_id, it pulls the item plus its variables and grades the item's UX
quality — missing descriptions, empty variable labels, mandatory-field
overload, etc.

Implementation should live alongside the other catalog tools in
`simple_servicenow_mcp.tools.catalog`.

Behaviour-style names per Testing Conventions in AGENTS.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Sample data ─────────────────────────────────────────────────────────

_ITEM_SYS_ID = "item000000000000000000000000aaaa"

_HEALTHY_ITEM = {
    "sys_id": _ITEM_SYS_ID,
    "name": "Request Laptop",
    "short_description": "Order a corporate-managed laptop for a new hire or replacement.",
    "active": "true",
}
_ITEM_NO_SHORT_DESC = {
    "sys_id": _ITEM_SYS_ID,
    "name": "Mystery item",
    "short_description": "",
    "active": "true",
}

_GOOD_VAR = {
    "sys_id": "var00000000000000000000000000001",
    "name": "employee_name",
    "question_text": "Employee name",
    "mandatory": "true",
    "type": "string",
}
_VAR_NO_QUESTION_TEXT = {
    "sys_id": "var00000000000000000000000000002",
    "name": "cost_center",
    "question_text": "",
    "mandatory": "false",
    "type": "string",
}


def _make_mandatory_vars(count: int) -> list[dict]:
    return [
        {
            "sys_id": f"varmand{i:025d}",
            "name": f"field_{i}",
            "question_text": f"Field {i}?",
            "mandatory": "true",
            "type": "string",
        }
        for i in range(count)
    ]


# ── 1. Module/tool existence ────────────────────────────────────────────


def test_analyze_catalog_item_tool_should_exist_in_catalog_module() -> None:
    """analyze_catalog_item must be importable from tools.catalog — not yet implemented."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import catalog as catalog_module

    # Assert
    assert hasattr(catalog_module, "analyze_catalog_item"), (
        "analyze_catalog_item is not yet implemented in simple_servicenow_mcp.tools.catalog"
    )


# ── 2-3. Data gathering ─────────────────────────────────────────────────


async def test_analyze_catalog_item_should_query_sc_cat_item_by_sys_id_first(
    fake_client,
    fake_ctx,
) -> None:
    """First REST call must resolve the item by sys_id against sc_cat_item."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = [_GOOD_VAR]

    # Act
    await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    assert fake_client.calls, "analyze_catalog_item made no REST calls"
    first = fake_client.calls[0]
    assert first.table == "sc_cat_item"
    query = first.kwargs.get("query") or ""
    assert f"sys_id={_ITEM_SYS_ID}" in query, (
        f"item resolution must filter by sys_id, got: {query!r}"
    )


async def test_analyze_catalog_item_should_query_variables_via_item_option_new(
    fake_client,
    fake_ctx,
) -> None:
    """Variables are pulled from item_option_new filtered by cat_item — matches
    the existing get_catalog_item_variables pattern at tools/catalog.py:99."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = [_GOOD_VAR]

    # Act
    await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    var_calls = [c for c in fake_client.calls if c.table == "item_option_new"]
    assert len(var_calls) == 1
    query = var_calls[0].kwargs.get("query") or ""
    assert f"cat_item={_ITEM_SYS_ID}" in query


# ── 4. Error path ───────────────────────────────────────────────────────


async def test_analyze_catalog_item_should_return_error_json_when_item_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown sys_id returns a JSON error payload — must not raise."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = []  # no match

    # Act
    result = await analyze_catalog_item("nonexistent00000000000000000000a", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "nonexistent" in parsed["error"]


# ── 5-8. UX-quality detectors ───────────────────────────────────────────


async def test_analyze_catalog_item_should_flag_missing_short_description(
    fake_client,
    fake_ctx,
) -> None:
    """Empty or whitespace short_description produces a missing_short_description finding."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_ITEM_NO_SHORT_DESC]
    fake_client.list_responses["item_option_new"] = [_GOOD_VAR]

    # Act
    result = await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [f for f in findings if f.get("type") == "missing_short_description"]
    assert matching, f"expected a missing_short_description finding, got findings: {findings!r}"


async def test_analyze_catalog_item_should_flag_item_with_no_variables(
    fake_client,
    fake_ctx,
) -> None:
    """A catalog item with zero variables is almost certainly misconfigured."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = []

    # Act
    result = await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [f for f in findings if f.get("type") == "no_variables"]
    assert matching, (
        f"expected a no_variables finding for an item with no form fields, got: {findings!r}"
    )


async def test_analyze_catalog_item_should_flag_variable_missing_question_text(
    fake_client,
    fake_ctx,
) -> None:
    """A variable without question_text shows up unlabelled on the form."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = [_GOOD_VAR, _VAR_NO_QUESTION_TEXT]

    # Act
    result = await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f
        for f in findings
        if f.get("type") == "missing_question_text"
        and f.get("sys_id") == _VAR_NO_QUESTION_TEXT["sys_id"]
    ]
    assert matching, (
        f"expected a missing_question_text finding for variable "
        f"{_VAR_NO_QUESTION_TEXT['sys_id']}, got: {findings!r}"
    )


async def test_analyze_catalog_item_should_flag_excessive_mandatory_variables(
    fake_client,
    fake_ctx,
) -> None:
    """More than 5 mandatory variables creates form friction — flag as a UX issue.

    Threshold = 5 chosen from the prompt's "too many or too few required fields"
    guidance at prompts.py:62. Re-tune in GREEN if user feedback says otherwise.
    """
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = _make_mandatory_vars(6)

    # Act
    result = await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [f for f in findings if f.get("type") == "excessive_mandatory_variables"]
    assert matching, (
        f"expected excessive_mandatory_variables finding for 6 mandatory fields, got: {findings!r}"
    )


# ── 9. Summary metadata ─────────────────────────────────────────────────


async def test_analyze_catalog_item_should_return_variable_count_in_response(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level variable_count equals number of variables inspected."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = _make_mandatory_vars(3)

    # Act
    result = await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("variable_count") == 3


# ── 9b. Item identity in response (PDI-observed: items carry name) ──────


async def test_analyze_catalog_item_should_echo_item_name_and_sys_id_in_response(
    fake_client,
    fake_ctx,
) -> None:
    """Response must echo the item's ``name`` and ``sys_id`` for caller display.

    PDI ``sc_cat_item`` records have meaningful human names
    (e.g. ``"Manage Knowledge Ownership Group"``); a report without the name is
    unreadable when the LLM analyses several items.
    """
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.list_responses["item_option_new"] = [_GOOD_VAR]

    # Act
    result = await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert parsed.get("sys_id") == _ITEM_SYS_ID
    assert parsed.get("name") == _HEALTHY_ITEM["name"]


# ── 10. Error wrapping ──────────────────────────────────────────────────


async def test_analyze_catalog_item_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Underlying client errors mid-analysis must be re-raised as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.catalog import (
        analyze_catalog_item,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sc_cat_item"] = [_HEALTHY_ITEM]
    fake_client.raise_on_table["item_option_new"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await analyze_catalog_item(_ITEM_SYS_ID, fake_ctx)
