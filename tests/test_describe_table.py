"""Behavioural tests for the `describe_table` tool.

Tools now return native Python objects (FastMCP serialises them to
structuredContent) so the return assertion checks the list directly.
"""

from __future__ import annotations


def test_describe_table_tool_should_exist_in_table_module() -> None:
    """describe_table must be importable from tools.table."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import table as table_module

    # Assert
    assert hasattr(table_module, "describe_table"), (
        "describe_table is missing from simple_servicenow_mcp.tools.table"
    )


async def test_describe_table_should_query_sys_dictionary_filtered_by_table_name(
    fake_client: FakeServiceNowClient,  # type: ignore[name-defined]  # noqa: F821
    fake_ctx,
) -> None:
    """describe_table must query sys_dictionary for the requested table only."""
    # Arrange
    from simple_servicenow_mcp.tools.table import describe_table

    # Act
    await describe_table("incident", fake_ctx)

    # Assert
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call.table == "sys_dictionary"
    assert call.kwargs.get("query") == "name=incident^internal_type!=collection"


async def test_describe_table_should_return_list_of_field_records(
    fake_client: FakeServiceNowClient,  # type: ignore[name-defined]  # noqa: F821
    fake_ctx,
) -> None:
    """describe_table must return the underlying field records as a native list."""
    # Arrange
    from simple_servicenow_mcp.tools.table import describe_table

    fake_client.list_response = [
        {"element": "number", "internal_type": "string", "mandatory": "true"},
        {"element": "priority", "internal_type": "integer", "mandatory": "false"},
    ]

    # Act
    result = await describe_table("incident", fake_ctx)

    # Assert
    assert result == fake_client.list_response


async def test_describe_table_should_return_empty_list_when_table_does_not_exist(
    fake_client: FakeServiceNowClient,  # type: ignore[name-defined]  # noqa: F821
    fake_ctx,
) -> None:
    """Unknown table → PDI returns 200 + empty list; describe_table must surface that as []."""
    # Arrange — PDI shape captured from sys_dictionary?name=zzz_no_such_table_42
    from simple_servicenow_mcp.tools.table import describe_table

    fake_client.list_response = []

    # Act
    result = await describe_table("zzz_no_such_table_42", fake_ctx)

    # Assert
    assert result == []


async def test_describe_table_should_include_fields_inherited_from_parent_tables(
    fake_client,
    fake_ctx,
) -> None:
    """incident inherits most of its fields from task; the schema must show them."""
    from simple_servicenow_mcp.tools.table import describe_table

    fake_client.chains["incident"] = ["incident", "task"]

    await describe_table("incident", fake_ctx)

    [call] = fake_client.calls
    assert call.kwargs["query"] == "nameINincident,task^internal_type!=collection"


async def test_describe_table_should_page_past_the_client_clamp(fake_client, fake_ctx) -> None:
    """A single list_records call is capped at 100 rows; schemas are often larger."""
    from simple_servicenow_mcp.tools.table import describe_table

    fake_client.pages[("sys_dictionary", 0)] = [{"element": f"f{i}"} for i in range(100)]
    fake_client.pages[("sys_dictionary", 100)] = [{"element": "f100"}]

    result = await describe_table("incident", fake_ctx)

    assert len(result) == 101
    assert [c.kwargs.get("offset") for c in fake_client.calls] == [0, 100]
