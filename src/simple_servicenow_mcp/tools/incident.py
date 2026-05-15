"""Incident-specific shortcut tools.

Returns native types so FastMCP emits structuredContent.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_INCIDENT_FIELDS = (
    "sys_id,number,short_description,description,state,priority,urgency,impact,"
    "assigned_to,assignment_group,category,subcategory,caller_id,"
    "opened_at,resolved_at,closed_at,close_code,close_notes"
)


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


def _writable(ctx: Context, instance: str | None, op: str) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    app.ensure_writable(op)
    return app.client_for(instance)


@mcp.tool(annotations=_a.READ)
async def list_incidents(
    ctx: Context,
    query: str | None = None,
    limit: int = 20,
    offset: int = 0,
    verbose: bool = False,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List incidents with display values, human-readable fields, and pagination.

    Args:
        query: Encoded query (e.g. active=true^priority=1)
        limit: Max records (1-100, default 20)
        offset: Pagination offset
        verbose: If True, return all incident columns. Default returns a trimmed
            set (number, state, priority, assigned_to, opened_at, close_*) to keep
            list responses cheap in tokens.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _client(ctx, instance)
    try:
        return await client.list_records(
            "incident",
            query=query,
            fields=None if verbose else _INCIDENT_FIELDS,
            limit=limit,
            offset=offset,
            display_value="true",
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.CREATE)
async def create_incident(
    short_description: str,
    ctx: Context,
    description: str | None = None,
    urgency: str | None = None,
    impact: str | None = None,
    category: str | None = None,
    caller_id: str | None = None,
    assignment_group: str | None = None,
    instance: str | None = None,
) -> dict[str, Any]:
    """Create a new incident.

    Args:
        short_description: Brief summary of the incident
        description: Detailed description
        urgency: Urgency level (1=High, 2=Medium, 3=Low)
        impact: Impact level (1=High, 2=Medium, 3=Low)
        category: Incident category
        caller_id: sys_id of the caller
        assignment_group: sys_id of the assignment group
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "create_incident")
    data: dict[str, str] = {"short_description": short_description}
    if description:
        data["description"] = description
    if urgency:
        data["urgency"] = urgency
    if impact:
        data["impact"] = impact
    if category:
        data["category"] = category
    if caller_id:
        data["caller_id"] = caller_id
    if assignment_group:
        data["assignment_group"] = assignment_group

    try:
        return await client.create_record("incident", data)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.APPEND)
async def add_incident_comment(
    sys_id: str,
    comment: str,
    ctx: Context,
    comment_type: Literal["work_notes", "comments"] = "work_notes",
    instance: str | None = None,
) -> dict[str, Any]:
    """Add a work note or customer-visible comment to an incident.

    Args:
        sys_id: The sys_id of the incident
        comment: The comment text
        comment_type: "work_notes" (internal) or "comments" (customer-visible)
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "add_incident_comment")
    try:
        return await client.update_record("incident", sys_id, {comment_type: comment})
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.UPDATE)
async def resolve_incident(
    sys_id: str,
    close_code: str,
    close_notes: str,
    ctx: Context,
    close_state: str = "6",
    instance: str | None = None,
) -> dict[str, Any]:
    """Resolve an incident by setting state to Resolved with close code and notes.

    Args:
        sys_id: The sys_id of the incident
        close_code: Resolution code (e.g. "Solved (Permanently)", "Solved (Work Around)")
        close_notes: Resolution notes explaining the fix
        close_state: Numeric state value for "Resolved" — defaults to "6" (OOTB).
            Customers customise state values; pass an override if your instance differs.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "resolve_incident")
    try:
        return await client.update_record(
            "incident",
            sys_id,
            {
                "state": close_state,
                "close_code": close_code,
                "close_notes": close_notes,
            },
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def triage_incident(
    number: str,
    ctx: Context,
    instance: str | None = None,
) -> str:
    """Assemble a full triage bundle for an incident: record, journal, similar resolved.

    Programmatic counterpart to the ``triage_incident`` prompt — one tool call
    returns ``{incident, journal, similar_incidents}`` as a JSON string.

    Args:
        number: Incident display number (e.g. ``INC0010234``).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        JSON string with ``incident`` (dict), ``journal`` (list), and
        ``similar_incidents`` (list). On unknown number returns
        ``{"error": "<msg>"}`` rather than raising.
    """
    client = _client(ctx, instance)
    try:
        matches = await client.list_records(
            "incident",
            query=f"number={number}",
            fields=_INCIDENT_FIELDS,
            limit=1,
        )
        if not matches:
            return json.dumps({"error": f"incident not found: {number}"})
        incident = matches[0]
        sys_id = incident.get("sys_id", "")

        journal = await client.list_records(
            "sys_journal_field",
            query=f"element_id={sys_id}^elementINwork_notes,comments",
            limit=50,
        )

        words = (incident.get("short_description") or "").split()
        like_clause = "^OR".join(f"short_descriptionLIKE{w}" for w in words)
        similar_query = "stateIN6,7"
        if like_clause:
            similar_query = f"{similar_query}^{like_clause}"
        similar = await client.list_records(
            "incident",
            query=similar_query,
            fields=_INCIDENT_FIELDS,
            limit=5,
        )

        return json.dumps(
            {
                "incident": incident,
                "journal": journal,
                "similar_incidents": similar,
            }
        )
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"triage_incident failed: {e}") from e
