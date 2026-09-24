"""Behavioural tests for the script-reading tools (tools/script.py).

The list tools are thin wrappers, so the tests pin the two things that matter
to the LLM: the query is passed through untouched, and the compact field list
contains what the next step needs (``script`` for scripted tables, and
``run_scripts`` for UI policies, whose code lives in ``script_true`` /
``script_false`` rather than ``script``).
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from simple_servicenow_mcp.tools.script import (
    get_script_body,
    list_business_rules,
    list_ui_policies,
)


def _fields(call) -> set[str]:
    return set((call.kwargs.get("fields") or "").split(","))


async def test_list_business_rules_should_pass_query_and_request_script(fake_client, fake_ctx):
    fake_client.list_response = [{"sys_id": "br1", "name": "Rule", "script": "gs.info(1);"}]

    rows = await list_business_rules(fake_ctx, query="sys_scope.scope=x_co_app^active=true")

    [call] = fake_client.calls
    assert (call.method, call.table) == ("list", "sys_script")
    # The wrapper appends its own ORDERBY; the caller's query must lead untouched.
    assert call.kwargs["query"].startswith("sys_scope.scope=x_co_app^active=true")
    assert "script" in _fields(call)
    assert rows == fake_client.list_response


async def test_list_ui_policies_should_request_run_scripts_not_script(fake_client, fake_ctx):
    """sys_ui_policy has no ``script`` column; ``run_scripts`` says whether code runs at all."""
    await list_ui_policies(fake_ctx)

    [call] = fake_client.calls
    assert call.table == "sys_ui_policy"
    assert "run_scripts" in _fields(call)
    assert "script" not in _fields(call)


async def test_list_ui_policies_verbose_should_request_every_field(fake_client, fake_ctx):
    await list_ui_policies(fake_ctx, verbose=True)

    [call] = fake_client.calls
    assert call.kwargs.get("fields") is None


@pytest.mark.parametrize(
    ("table", "expected", "unexpected"),
    [
        ("sys_script", {"script"}, {"script_true"}),
        ("sys_script_include", {"script"}, {"script_true"}),
        ("sys_ui_policy", {"script_true", "script_false", "run_scripts"}, {"script"}),
    ],
)
async def test_get_script_body_should_request_the_fields_that_hold_code(
    fake_client, fake_ctx, table, expected, unexpected
):
    fake_client.get_response = {"sys_id": "x"}

    await get_script_body(table, "x", fake_ctx)

    [call] = fake_client.calls
    assert (call.method, call.table, call.kwargs["sys_id"]) == ("get", table, "x")
    assert expected <= _fields(call)
    assert not (unexpected & _fields(call))


async def test_list_business_rules_should_wrap_client_errors_in_tool_error(fake_client, fake_ctx):
    fake_client.raise_on_table["sys_script"] = RuntimeError("network down")

    with pytest.raises(ToolError, match="network down"):
        await list_business_rules(fake_ctx)
