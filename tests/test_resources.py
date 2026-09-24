"""Resources: fixed URIs must be listable, and payloads must never raise."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from simple_servicenow_mcp import resources
from simple_servicenow_mcp.client import ServiceNowAPIError
from simple_servicenow_mcp.config import Settings
from simple_servicenow_mcp.server import AppContext, mcp


class _Client:
    """Just enough of ServiceNowClient for the resource payloads."""

    def __init__(self, url: str, fail: Exception | None = None, token: dict | None = None) -> None:
        self.settings = Settings(  # type: ignore[call-arg]
            instance_url=url, auth_method="basic", username="u", password="p"
        )
        self.fail = fail
        self.token = token
        self.calls: list[str] = []

    async def list_records(self, table: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(table)
        if self.fail:
            raise self.fail
        return [{"sys_id": "1"}]

    def token_status(self) -> dict | None:
        return self.token


def _use(monkeypatch, client: _Client, registry: Any = None) -> AppContext:
    app = AppContext(client=client, settings=client.settings, registry=registry)  # type: ignore[arg-type]
    monkeypatch.setattr(resources, "_app", lambda: app)
    return app


async def test_fixed_uri_resources_are_listed_and_templates_stay_templates() -> None:
    """A resource declared with a ``ctx`` parameter silently becomes a template
    and vanishes from ``resources/list``; info and health must be plain resources."""
    listed = {str(r.uri) for r in await mcp.list_resources()}
    templates = {t.uriTemplate for t in await mcp.list_resource_templates()}

    assert listed >= {"servicenow://instance/info", "servicenow://health"}
    assert templates == {"servicenow://schema/{table_name}", "servicenow://scope/{scope_name}"}


async def test_instance_info_single_instance_has_no_secrets(monkeypatch) -> None:
    _use(monkeypatch, _Client("https://a.service-now.com", token={"present": True}))

    info = json.loads(await resources.instance_info())

    assert info == {
        "mode": "single-instance",
        "url": "https://a.service-now.com",
        "auth_method": "basic",
        "token": {"present": True},
    }


async def test_instance_info_multi_instance_lists_the_registry(monkeypatch) -> None:
    a, b = _Client("https://a.service-now.com"), _Client("https://b.service-now.com")
    _use(monkeypatch, a, SimpleNamespace(default_name="a", clients={"a": a, "b": b}))

    info = json.loads(await resources.instance_info())

    assert (info["mode"], info["default"]) == ("multi-instance", "a")
    assert set(info["instances"]) == {"a", "b"}


async def test_health_ok_single_instance(monkeypatch) -> None:
    client = _Client("https://a.service-now.com")
    _use(monkeypatch, client)

    payload = json.loads(await resources.health())

    assert payload["status"] == "ok"
    assert client.calls == ["sys_user"]


async def test_health_reports_api_errors_with_token_state_instead_of_raising(monkeypatch) -> None:
    client = _Client(
        "https://a.service-now.com",
        fail=ServiceNowAPIError(401, "auth failed", "expired"),
        token={"present": True, "needs_login": True},
    )
    _use(monkeypatch, client)

    payload = json.loads(await resources.health())

    assert (payload["status"], payload["code"], payload["message"]) == ("error", 401, "auth failed")
    assert payload["token"]["needs_login"] is True


async def test_health_reports_network_errors_instead_of_raising(monkeypatch) -> None:
    _use(monkeypatch, _Client("https://a.service-now.com", fail=OSError("dns")))

    payload = json.loads(await resources.health())

    assert payload == {"status": "error", "instance": "https://a.service-now.com", "message": "dns"}


async def test_health_multi_instance_checks_every_instance(monkeypatch) -> None:
    a = _Client("https://a.service-now.com")
    b = _Client("https://b.service-now.com", fail=ServiceNowAPIError(403, "forbidden"))
    _use(monkeypatch, a, SimpleNamespace(default_name="a", clients={"a": a, "b": b}))

    payload = json.loads(await resources.health())

    assert payload["status"] == "error"
    assert payload["instances"]["a"]["status"] == "ok"
    assert payload["instances"]["b"]["code"] == 403


async def test_table_schema_pages_and_wraps_errors(fake_client, fake_ctx) -> None:
    fake_client.pages[("sys_dictionary", 0)] = [{"element": f"f{i}"} for i in range(100)]
    fake_client.pages[("sys_dictionary", 100)] = [{"element": "f100"}]

    payload = json.loads(await resources.table_schema("incident", fake_ctx))

    assert (payload["table"], len(payload["fields"]), payload["truncated"]) == (
        "incident",
        101,
        False,
    )

    fake_client.raise_on_table["sys_dictionary"] = RuntimeError("down")
    assert "down" in json.loads(await resources.table_schema("incident", fake_ctx))["error"]


async def test_app_scope_returns_record_or_error(fake_client, fake_ctx) -> None:
    fake_client.list_responses["sys_scope"] = [{"sys_id": "s1", "scope": "x_co_app"}]
    assert json.loads(await resources.app_scope("x_co_app", fake_ctx))["scope"] == "x_co_app"

    fake_client.list_responses["sys_scope"] = []
    assert "not found" in json.loads(await resources.app_scope("x_nope", fake_ctx))["error"]

    fake_client.raise_on_table["sys_scope"] = RuntimeError("down")
    assert "down" in json.loads(await resources.app_scope("x_co_app", fake_ctx))["error"]
