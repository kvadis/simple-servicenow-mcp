"""Tests for Phase 2 tools — CMDB, Knowledge, Aggregate API.

Uses the shared FakeServiceNowClient from conftest.py for list/get behaviour;
adds an inline ``FakeAggregateClient`` subclass to model the Stats API.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from simple_servicenow_mcp.config import Settings
from simple_servicenow_mcp.server import AppContext


def _ctx(client: Any, read_only: bool = False) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://x.service-now.com",
        username="u",
        password="p",
        read_only=read_only,
    )
    app = AppContext(client=client, settings=settings)  # type: ignore[arg-type]
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


# ── CMDB ──────────────────────────────────────────────────────────────


async def test_list_cis_filters_by_class_when_ci_class_given(fake_client) -> None:
    from simple_servicenow_mcp.tools.cmdb import list_cis

    fake_client.list_response = [{"sys_id": "abc", "name": "db-1"}]

    result = await list_cis(_ctx(fake_client), ci_class="cmdb_ci_database")  # type: ignore[arg-type]

    assert result == [{"sys_id": "abc", "name": "db-1"}]
    call = fake_client.calls[0]
    assert call.table == "cmdb_ci"
    assert call.kwargs["query"] == "sys_class_name=cmdb_ci_database"
    assert call.kwargs["display_value"] == "true"


async def test_list_cis_combines_class_and_extra_query(fake_client) -> None:
    from simple_servicenow_mcp.tools.cmdb import list_cis

    await list_cis(_ctx(fake_client), ci_class="cmdb_ci_server", query="operational_status=1")  # type: ignore[arg-type]

    assert (
        fake_client.calls[0].kwargs["query"] == "sys_class_name=cmdb_ci_server^operational_status=1"
    )


async def test_get_ci_calls_cmdb_ci_with_sys_id(fake_client) -> None:
    from simple_servicenow_mcp.tools.cmdb import get_ci

    fake_client.get_response = {"sys_id": "abc", "name": "db-1"}

    result = await get_ci("abc", _ctx(fake_client))  # type: ignore[arg-type]

    assert result == {"sys_id": "abc", "name": "db-1"}
    assert fake_client.calls[0].table == "cmdb_ci"


async def test_list_ci_relationships_splits_outgoing_and_incoming(fake_client) -> None:
    from simple_servicenow_mcp.tools.cmdb import list_ci_relationships

    fake_client.list_responses = {
        "cmdb_rel_ci": [{"sys_id": "rel-1", "parent": "abc", "child": "xyz"}],
    }

    result = await list_ci_relationships("abc", _ctx(fake_client))  # type: ignore[arg-type]

    assert result["direction"] == "both"
    # Both directions issued queries against cmdb_rel_ci
    assert len(fake_client.calls) == 2
    queries = sorted(c.kwargs["query"] for c in fake_client.calls)
    assert queries == ["child=abc", "parent=abc"]
    assert result["total"] == 2  # same response replayed twice for both directions


async def test_list_ci_relationships_rejects_unknown_direction(fake_client) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from simple_servicenow_mcp.tools.cmdb import list_ci_relationships

    with pytest.raises(ToolError, match="direction must be"):
        await list_ci_relationships("abc", _ctx(fake_client), direction="sideways")  # type: ignore[arg-type]


async def test_find_cis_by_class_pins_sys_class_name(fake_client) -> None:
    from simple_servicenow_mcp.tools.cmdb import find_cis_by_class

    await find_cis_by_class("cmdb_ci_web_server", _ctx(fake_client))  # type: ignore[arg-type]

    assert fake_client.calls[0].kwargs["query"] == "sys_class_name=cmdb_ci_web_server"


# ── Knowledge ─────────────────────────────────────────────────────────


async def test_list_knowledge_articles_defaults_to_published(fake_client) -> None:
    from simple_servicenow_mcp.tools.knowledge import list_knowledge_articles

    await list_knowledge_articles(_ctx(fake_client))  # type: ignore[arg-type]

    assert fake_client.calls[0].kwargs["query"] == "workflow_state=published"


async def test_list_knowledge_articles_state_none_skips_workflow_filter(fake_client) -> None:
    from simple_servicenow_mcp.tools.knowledge import list_knowledge_articles

    await list_knowledge_articles(_ctx(fake_client), state=None)  # type: ignore[arg-type]

    # No query at all when state is None and no other filters
    assert fake_client.calls[0].kwargs["query"] is None


async def test_search_knowledge_builds_like_or_across_fields(fake_client) -> None:
    from simple_servicenow_mcp.tools.knowledge import search_knowledge

    await search_knowledge("vpn", _ctx(fake_client))  # type: ignore[arg-type]

    q = fake_client.calls[0].kwargs["query"]
    assert "short_descriptionLIKEvpn" in q
    assert "textLIKEvpn" in q
    assert "keywordsLIKEvpn" in q
    assert "^OR" in q
    assert "workflow_state=published" in q  # default state still applied


async def test_search_knowledge_rejects_empty_keyword(fake_client) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from simple_servicenow_mcp.tools.knowledge import search_knowledge

    with pytest.raises(ToolError, match="non-empty"):
        await search_knowledge("   ", _ctx(fake_client))  # type: ignore[arg-type]


async def test_get_knowledge_article_fetches_with_full_fields(fake_client) -> None:
    from simple_servicenow_mcp.tools.knowledge import get_knowledge_article

    fake_client.get_response = {"sys_id": "kb-1", "text": "body"}

    result = await get_knowledge_article("kb-1", _ctx(fake_client))  # type: ignore[arg-type]

    assert result == {"sys_id": "kb-1", "text": "body"}
    assert fake_client.calls[0].table == "kb_knowledge"
    assert "article_body" in fake_client.calls[0].kwargs["fields"]


# ── Aggregate ─────────────────────────────────────────────────────────


class FakeAggregateClient:
    """Stats-API stub — records ``aggregate`` calls and returns a canned payload."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def aggregate(self, table: str, **kwargs: Any) -> Any:
        self.calls.append({"table": table, **kwargs})
        return self.response

    async def close(self) -> None:
        pass


async def test_aggregate_records_passes_through_aggregation_params() -> None:
    from simple_servicenow_mcp.tools.table import aggregate_records

    client = FakeAggregateClient(response={"stats": {"count": "42"}})
    result = await aggregate_records(  # type: ignore[arg-type]
        "incident",
        _ctx(client),
        query="active=true",
        avg_fields="reassignment_count",
        group_by="priority",
    )

    call = client.calls[0]
    assert call["table"] == "incident"
    assert call["query"] == "active=true"
    assert call["avg_fields"] == "reassignment_count"
    assert call["group_by"] == "priority"
    assert call["count"] is True  # default
    assert result["result"] == {"stats": {"count": "42"}}


async def test_aggregate_records_rejects_no_aggregation() -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from simple_servicenow_mcp.tools.table import aggregate_records

    client = FakeAggregateClient(response={})
    with pytest.raises(ToolError, match="at least one aggregation"):
        await aggregate_records(  # type: ignore[arg-type]
            "incident",
            _ctx(client),
            count=False,  # and no sum/avg/min/max either
        )
