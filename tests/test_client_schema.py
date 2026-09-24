"""Schema discovery on the real client, through httpx.MockTransport.

``get_table_field_names`` feeds write validation; a task-derived table has
several hundred fields, so a single clamped page would reject valid ones.
"""

from __future__ import annotations

import httpx

from simple_servicenow_mcp.client import ServiceNowClient
from simple_servicenow_mcp.config import Settings

_PARENT = {"incident": "task", "task": ""}
_TOTAL_FIELDS = 130


def _handler(calls: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        params = dict(request.url.params)
        if request.url.path.endswith("/table/sys_db_object"):
            name = params["sysparm_query"].split("=", 1)[1]
            return httpx.Response(
                200, json={"result": [{"super_class.name": _PARENT.get(name, "")}]}
            )
        if request.url.path.endswith("/table/sys_dictionary"):
            offset, limit = int(params["sysparm_offset"]), int(params["sysparm_limit"])
            n = max(0, min(limit, _TOTAL_FIELDS - offset))
            return httpx.Response(
                200, json={"result": [{"element": f"f{offset + i}"} for i in range(n)]}
            )
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    return handle


def _client(calls: list[httpx.Request]) -> ServiceNowClient:
    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://acmedev.service-now.com",
        auth_method="basic",
        username="u",
        password="p",
        max_page_size=100,
    )
    return ServiceNowClient(settings, transport=httpx.MockTransport(_handler(calls)))


async def test_get_table_field_names_walks_the_chain_and_pages_past_the_clamp() -> None:
    calls: list[httpx.Request] = []
    client = _client(calls)

    fields = await client.get_table_field_names("incident")
    await client.close()

    assert fields is not None and len(fields) == _TOTAL_FIELDS
    dictionary = [r for r in calls if r.url.path.endswith("/sys_dictionary")]
    assert [r.url.params["sysparm_offset"] for r in dictionary] == ["0", "100"]
    assert "nameINincident,task" in dictionary[0].url.params["sysparm_query"]


async def test_table_chain_is_public_and_ordered_child_first() -> None:
    calls: list[httpx.Request] = []
    client = _client(calls)

    chain = await client.table_chain("incident")
    await client.close()

    assert chain == ["incident", "task"]
