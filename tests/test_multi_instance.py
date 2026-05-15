"""Tests for multi-instance support and the --transport CLI flag.

Avoids real network: instances are loaded, ``ServiceNowClient`` is constructed
(httpx client setup is in-memory), and we never actually hit ServiceNow.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from simple_servicenow_mcp.config import Settings
from simple_servicenow_mcp.instances import (
    InstanceRegistry,
    _merge,
    load_instances,
)
from simple_servicenow_mcp.server import AppContext, _parse_args

# ── _merge ───────────────────────────────────────────────────────────


def test_merge_applies_overrides_on_top_of_fallback() -> None:
    """Per-instance fields should override fallback Settings values."""
    fallback = Settings(  # type: ignore[call-arg]
        instance_url="https://shared.service-now.com",
        auth_method="basic",
        username="u",
        password="p",
    )
    merged = _merge(fallback, {"instance_url": "https://other.service-now.com"})
    assert merged.instance_url == "https://other.service-now.com"
    assert merged.username == "u"


def test_merge_rejects_unknown_fields() -> None:
    fallback = Settings(  # type: ignore[call-arg]
        instance_url="https://shared.service-now.com", username="u", password="p"
    )
    with pytest.raises(ValueError, match="Unknown instance field"):
        _merge(fallback, {"not_a_real_field": "x"})


# ── load_instances ───────────────────────────────────────────────────


def _write(tmp_path: Path, payload: dict) -> Path:
    f = tmp_path / "instances.json"
    f.write_text(json.dumps(payload))
    return f


def test_load_instances_builds_a_client_per_entry(tmp_path: Path) -> None:
    fallback = Settings(  # type: ignore[call-arg]
        instance_url="", username="u", password="p"
    )
    path = _write(
        tmp_path,
        {
            "default": "prod",
            "instances": {
                "prod": {"instance_url": "https://prod.service-now.com"},
                "dev": {"instance_url": "https://dev.service-now.com"},
            },
        },
    )

    registry = load_instances(path, fallback=fallback)

    assert registry.default_name == "prod"
    assert registry.names() == ["dev", "prod"]
    assert registry.client_for("prod").settings.instance_url == "https://prod.service-now.com"
    assert registry.client_for("dev").settings.instance_url == "https://dev.service-now.com"


def test_load_instances_uses_first_entry_when_default_omitted(tmp_path: Path) -> None:
    fallback = Settings(  # type: ignore[call-arg]
        instance_url="", username="u", password="p"
    )
    path = _write(
        tmp_path,
        {
            "instances": {
                "alpha": {"instance_url": "https://alpha.service-now.com"},
                "beta": {"instance_url": "https://beta.service-now.com"},
            },
        },
    )
    registry = load_instances(path, fallback=fallback)
    assert registry.default_name == "alpha"


def test_load_instances_rejects_missing_instance_url(tmp_path: Path) -> None:
    fallback = Settings(  # type: ignore[call-arg]
        instance_url="", username="u", password="p"
    )
    path = _write(tmp_path, {"instances": {"prod": {"username": "x"}}})
    with pytest.raises(ValueError, match="no instance_url"):
        load_instances(path, fallback=fallback)


def test_load_instances_rejects_default_not_in_instances(tmp_path: Path) -> None:
    fallback = Settings(  # type: ignore[call-arg]
        instance_url="", username="u", password="p"
    )
    path = _write(
        tmp_path,
        {
            "default": "missing",
            "instances": {"prod": {"instance_url": "https://prod.service-now.com"}},
        },
    )
    with pytest.raises(ValueError, match="default 'missing'"):
        load_instances(path, fallback=fallback)


# ── AppContext.client_for ────────────────────────────────────────────


class _Stub:
    """Stub client with a Settings attribute — enough for client_for to return."""

    def __init__(self, url: str) -> None:
        self.settings = Settings(  # type: ignore[call-arg]
            instance_url=url, username="u", password="p"
        )

    async def close(self) -> None:
        pass


def test_client_for_returns_default_when_instance_is_none() -> None:
    default = _Stub("https://default.service-now.com")
    app = AppContext(client=default, settings=default.settings)  # type: ignore[arg-type]
    assert app.client_for(None) is default


def test_client_for_raises_when_named_but_no_registry() -> None:
    default = _Stub("https://default.service-now.com")
    app = AppContext(client=default, settings=default.settings)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="multi-instance is not configured"):
        app.client_for("prod")


def test_client_for_resolves_named_instance_from_registry() -> None:
    prod = _Stub("https://prod.service-now.com")
    dev = _Stub("https://dev.service-now.com")
    registry = InstanceRegistry(clients={"prod": prod, "dev": dev}, default_name="prod")  # type: ignore[arg-type]
    app = AppContext(client=prod, settings=prod.settings, registry=registry)  # type: ignore[arg-type]

    assert app.client_for("dev") is dev
    assert app.client_for("prod") is prod
    assert app.client_for(None) is prod  # default


def test_client_for_raises_on_unknown_named_instance() -> None:
    prod = _Stub("https://prod.service-now.com")
    registry = InstanceRegistry(clients={"prod": prod}, default_name="prod")  # type: ignore[arg-type]
    app = AppContext(client=prod, settings=prod.settings, registry=registry)  # type: ignore[arg-type]

    with pytest.raises(ToolError, match="Unknown instance 'staging'"):
        app.client_for("staging")


# ── CLI parsing ──────────────────────────────────────────────────────


def test_parse_args_defaults_to_stdio() -> None:
    args = _parse_args([])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_parse_args_accepts_http() -> None:
    args = _parse_args(["--transport", "http", "--host", "0.0.0.0", "--port", "9000"])
    assert args.transport == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_parse_args_rejects_unknown_transport() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--transport", "websocket"])


# ── Tool param wiring ────────────────────────────────────────────────


async def test_tool_uses_instance_param_to_select_client(fake_client) -> None:
    """When ``instance`` is passed, the registry-resolved client receives the call."""
    from simple_servicenow_mcp.tools.table import list_records

    other = type(fake_client)()  # second FakeServiceNowClient
    other.list_response = [{"sys_id": "from-other"}]

    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://default.service-now.com", username="u", password="p"
    )
    registry = InstanceRegistry(
        clients={"default": fake_client, "other": other},  # type: ignore[arg-type]
        default_name="default",
    )
    app = AppContext(client=fake_client, settings=settings, registry=registry)  # type: ignore[arg-type]
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))

    result = await list_records("incident", ctx, instance="other")  # type: ignore[arg-type]

    assert result == [{"sys_id": "from-other"}]
    assert len(other.calls) == 1
    assert other.calls[0].table == "incident"
    assert len(fake_client.calls) == 0  # default not touched


# ── Read-only mode ───────────────────────────────────────────────────


def _app(read_only: bool) -> AppContext:
    stub = _Stub("https://x.service-now.com")
    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://x.service-now.com",
        username="u",
        password="p",
        read_only=read_only,
    )
    return AppContext(client=stub, settings=settings)  # type: ignore[arg-type]


def test_ensure_writable_allows_writes_when_off() -> None:
    _app(read_only=False).ensure_writable("create_record")  # does not raise


def test_ensure_writable_blocks_writes_when_on() -> None:
    with pytest.raises(ToolError, match="read-only mode"):
        _app(read_only=True).ensure_writable("create_record")


async def test_create_record_refuses_in_read_only_mode(fake_client) -> None:
    """create_record must surface a clear refusal before reaching the client."""
    from simple_servicenow_mcp.tools.table import create_record

    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://x.service-now.com", username="u", password="p", read_only=True
    )
    app = AppContext(client=fake_client, settings=settings)  # type: ignore[arg-type]
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))

    with pytest.raises(ToolError, match="read-only mode"):
        await create_record("incident", {"short_description": "x"}, ctx, skip_validation=True)  # type: ignore[arg-type]

    assert fake_client.calls == []  # never reached the client


async def test_list_records_works_in_read_only_mode(fake_client) -> None:
    """Reads must keep working when read_only=true."""
    from simple_servicenow_mcp.tools.table import list_records

    fake_client.list_response = [{"sys_id": "abc"}]
    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://x.service-now.com", username="u", password="p", read_only=True
    )
    app = AppContext(client=fake_client, settings=settings)  # type: ignore[arg-type]
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))

    result = await list_records("incident", ctx)  # type: ignore[arg-type]
    assert result == [{"sys_id": "abc"}]


def test_parse_args_read_only_flag() -> None:
    args = _parse_args(["--read-only"])
    assert args.read_only is True
    args = _parse_args([])
    assert args.read_only is False
