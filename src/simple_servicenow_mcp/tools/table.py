"""Generic Table API tools: list, get, create, update, delete, count, describe, search.

Tools return native Python objects (dict / list[dict] / int) so FastMCP emits
`structuredContent` alongside the text content, letting clients render tables
without re-parsing strings.

Every tool accepts an optional ``instance`` parameter; when ``SN_INSTANCES_FILE``
is configured, this selects which named ServiceNow instance to talk to. Omit it
to use the configured default.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a
from ._paging import list_all

_SCHEMA_FIELDS = (
    "name,element,column_label,internal_type,max_length,mandatory,reference,default_value,active"
)

_DEFAULT_SEARCH_FIELDS = "short_description,description,name,number"


async def _validate_field_names(client: ServiceNowClient, table: str, data: dict[str, Any]) -> None:
    """Reject obvious typos before sending to ServiceNow.

    Looks up the table's valid field set (walking inheritance) and raises
    ``ToolError`` listing unknown keys. Field discovery may be skipped silently
    if the credential lacks read on ``sys_dictionary``; that's intentional —
    validation is a best-effort guard rail, not enforcement.
    """
    get_fields = getattr(client, "get_table_field_names", None)
    if get_fields is None:
        return
    valid = await get_fields(table)
    if valid is None:
        return
    unknown = sorted(k for k in data if k not in valid and not k.startswith("sys_"))
    if unknown:
        raise ToolError(
            f"Unknown fields for table '{table}': {unknown}. "
            f"Use describe_table('{table}') to discover valid field names, "
            f"or pass skip_validation=true to bypass this check."
        )


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


def _writable(ctx: Context, instance: str | None, op: str) -> ServiceNowClient:
    """Resolve the client and enforce read-only mode in one call."""
    app: AppContext = ctx.request_context.lifespan_context
    app.ensure_writable(op)
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_records(
    table: str,
    ctx: Context,
    query: str | None = None,
    fields: str | None = None,
    limit: int = 20,
    offset: int = 0,
    display_value: str = "false",
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List records from any ServiceNow table with optional encoded query, field selection, and pagination.

    Args:
        table: Table name (e.g. incident, sc_cat_item, sys_user)
        query: Encoded query string (e.g. active=true^priority=1)
        fields: Comma-separated field names to return
        limit: Max records to return (1-100, default 20)
        offset: Pagination offset (default 0)
        display_value: Return display values instead of sys_ids ("true", "false", or "all")
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _client(ctx, instance)
    try:
        return await client.list_records(
            table,
            query=query,
            fields=fields,
            limit=limit,
            offset=offset,
            display_value=display_value,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_record(
    table: str,
    sys_id: str,
    ctx: Context,
    fields: str | None = None,
    instance: str | None = None,
) -> dict[str, Any]:
    """Get a single ServiceNow record by sys_id.

    Args:
        table: Table name
        sys_id: The sys_id of the record
        fields: Comma-separated field names to return
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _client(ctx, instance)
    try:
        return await client.get_record(table, sys_id, fields=fields)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.CREATE)
async def create_record(
    table: str,
    data: dict[str, Any],
    ctx: Context,
    skip_validation: bool = False,
    instance: str | None = None,
) -> dict[str, Any]:
    """Create a new record on a ServiceNow table.

    Field names in ``data`` are validated against ``sys_dictionary`` (including
    inherited fields) before the request is sent, so typos surface locally
    instead of as opaque ``400`` responses. Pass ``skip_validation=true`` to
    bypass — useful for fields added by extensions that aren't in the
    dictionary, or when the credential can't read ``sys_dictionary``.

    Args:
        table: Table name
        data: Field values for the new record
        skip_validation: Bypass local field-name validation (default False)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "create_record")
    if not skip_validation:
        await _validate_field_names(client, table, data)
    try:
        return await client.create_record(table, data)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.UPDATE)
async def update_record(
    table: str,
    sys_id: str,
    data: dict[str, Any],
    ctx: Context,
    skip_validation: bool = False,
    instance: str | None = None,
) -> dict[str, Any]:
    """Update an existing ServiceNow record by sys_id.

    Args:
        table: Table name
        sys_id: The sys_id of the record to update
        data: Field values to update
        skip_validation: Bypass local field-name validation (default False)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "update_record")
    if not skip_validation:
        await _validate_field_names(client, table, data)
    try:
        return await client.update_record(table, sys_id, data)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.DELETE)
async def delete_record(
    table: str,
    sys_id: str,
    ctx: Context,
    confirm: bool = False,
    instance: str | None = None,
) -> dict[str, Any]:
    """Delete a ServiceNow record by sys_id. **Destructive — requires confirm=true.**

    Safety rail: when ``confirm=False`` (default), the tool returns a preview of
    the record that would be deleted *without* deleting it, so the LLM can show
    the user what's about to disappear. Re-issue the same call with
    ``confirm=True`` to actually delete.

    Args:
        table: Table name
        sys_id: The sys_id of the record to delete
        confirm: Must be ``True`` to perform the delete. Default ``False`` returns
            a preview only — nothing is mutated.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "delete_record")
    try:
        if not confirm:
            preview = await client.get_record(table, sys_id)
            return {
                "preview": True,
                "table": table,
                "sys_id": sys_id,
                "would_delete": preview,
                "instruction": "Re-issue this call with confirm=true to actually delete.",
            }
        await client.delete_record(table, sys_id)
        return {"deleted": True, "table": table, "sys_id": sys_id}
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def search_records(
    table: str,
    keyword: str,
    ctx: Context,
    search_fields: str = _DEFAULT_SEARCH_FIELDS,
    result_fields: str | None = None,
    limit: int = 20,
    offset: int = 0,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """Keyword search a table without writing encoded-query syntax.

    Builds a LIKE-OR query across `search_fields` (substring match) so the LLM
    can search without knowing ServiceNow's encoded-query grammar. Non-existent
    fields on the target table will surface as a 400 with a hint.

    Args:
        table: Table name to search (e.g. incident, kb_knowledge)
        keyword: Substring to match (case-insensitive in ServiceNow)
        search_fields: Comma-separated fields to search in. Default covers the
            most common name/description columns across ITSM tables.
        result_fields: Comma-separated fields to return (default: all)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    if not keyword.strip():
        raise ToolError("keyword must be non-empty")
    fields_list = [f.strip() for f in search_fields.split(",") if f.strip()]
    if not fields_list:
        raise ToolError("search_fields must contain at least one field name")

    query = "^OR".join(f"{f}LIKE{keyword}" for f in fields_list)
    client = _client(ctx, instance)
    try:
        return await client.list_records(
            table,
            query=query,
            fields=result_fields,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def describe_table(
    table: str,
    ctx: Context,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """Describe a ServiceNow table's schema — field names, types, and constraints.

    Returns sys_dictionary entries for the table **and every table it extends**
    (``incident`` includes the ``task`` fields), excluding collection-type
    fields. Each entry's ``name`` says which table in the chain defines it. Use
    this before list_records or create_record to discover the right column
    names and types.

    Args:
        table: Table name (e.g. incident, sc_cat_item, sys_user)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _client(ctx, instance)
    try:
        chain = await client.table_chain(table) or [table]
        scope = f"name={chain[0]}" if len(chain) == 1 else f"nameIN{','.join(chain)}"
        rows, _ = await list_all(
            client,
            "sys_dictionary",
            query=f"{scope}^internal_type!=collection",
            fields=_SCHEMA_FIELDS,
        )
        return rows
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def count_records(
    table: str,
    ctx: Context,
    query: str | None = None,
    instance: str | None = None,
) -> dict[str, Any]:
    """Get the count of records matching a query on a ServiceNow table.

    Args:
        table: Table name
        query: Encoded query string
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _client(ctx, instance)
    try:
        count = await client.get_count(table, query)
        return {"table": table, "query": query, "count": count}
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def aggregate_records(
    table: str,
    ctx: Context,
    query: str | None = None,
    sum_fields: str | None = None,
    avg_fields: str | None = None,
    min_fields: str | None = None,
    max_fields: str | None = None,
    group_by: str | None = None,
    having: str | None = None,
    count: bool = True,
    display_value: str = "false",
    instance: str | None = None,
) -> dict[str, Any]:
    """Stats API aggregation: count + sum / avg / min / max, optionally grouped.

    Lets the LLM compute totals across many records in one round-trip instead
    of paginating raw rows. Example: ``aggregate_records("incident",
    query="active=true", group_by="priority")`` returns one bucket per priority
    with a count per bucket.

    Args:
        table: Table name
        query: Encoded query to scope the aggregation
        sum_fields: Comma-separated numeric fields to sum
        avg_fields: Comma-separated numeric fields to average
        min_fields: Comma-separated numeric fields to find the min of
        max_fields: Comma-separated numeric fields to find the max of
        group_by: Field(s) to GROUP BY; comma-separated for multi-key grouping
        having: HAVING clause to filter post-aggregation
        count: Include count in the result (default True)
        display_value: ``true`` / ``false`` / ``all`` — display values for refs
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        ``{"table": ..., "result": <raw stats payload>}`` — when ``group_by`` is
        set, ``result`` is a list of group buckets; otherwise an object with
        ``stats`` (containing ``count``, ``sum``, ``avg`` etc.).
    """
    if not any([count, sum_fields, avg_fields, min_fields, max_fields]):
        raise ToolError(
            "aggregate_records needs at least one aggregation — count=true or "
            "one of sum_fields/avg_fields/min_fields/max_fields."
        )
    client = _client(ctx, instance)
    try:
        result = await client.aggregate(
            table,
            query=query,
            count=count,
            sum_fields=sum_fields,
            avg_fields=avg_fields,
            min_fields=min_fields,
            max_fields=max_fields,
            group_by=group_by,
            having=having,
            display_value=display_value,
        )
    except Exception as e:
        raise ToolError(str(e)) from e
    return {"table": table, "query": query, "group_by": group_by, "result": result}
