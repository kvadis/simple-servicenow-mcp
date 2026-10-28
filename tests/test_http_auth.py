"""HTTP transport: Host allow-list and OAuth resource-server mode.

The tests drive the real CLI entry point (`server.main`) with `mcp.run` swapped
out, then exercise the Starlette app the server would have served.
"""

from __future__ import annotations

import gc
from collections.abc import Iterator

import httpx
import pytest

from simple_servicenow_mcp import server
from simple_servicenow_mcp.server import mcp

# mcp 1.29.1 leaves a MemoryObjectReceiveStream unclosed when a streamable-HTTP
# session ends; a bare FastMCP() reproduces it, so it is upstream, not ours. Only
# that warning is ignored, and only in this module.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Exception ignored in.*MemoryObjectReceiveStream:pytest.PytestUnraisableExceptionWarning"
)

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture
def served(monkeypatch) -> Iterator[list[str]]:
    """Run `main()` without starting a server; restore the shared `mcp` afterwards.

    Yields the transports `mcp.run` was called with.
    """
    for name, value in {
        "SN_INSTANCE_URL": "https://acme.service-now.com",
        "SN_USERNAME": "u",
        "SN_PASSWORD": "p",
        "SN_READ_ONLY": "true",  # main() writes it; setenv makes monkeypatch restore it
    }.items():
        monkeypatch.setenv(name, value)
    saved_settings = mcp.settings.model_copy()
    saved_verifier = mcp._token_verifier
    calls: list[str] = []
    monkeypatch.setattr(mcp, "run", lambda transport: calls.append(transport))
    yield calls
    mcp.settings = saved_settings
    mcp._token_verifier = saved_verifier
    mcp._session_manager = None


async def _post_initialize(host: str, origin: str | None = None) -> int:
    headers = {
        "Host": host,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if origin:
        headers["Origin"] = origin
    app = mcp.streamable_http_app()
    # The session manager only runs inside the app's lifespan.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            response = await c.post("/mcp", json=_INITIALIZE, headers=headers)
            if session := response.headers.get("mcp-session-id"):
                # End the session as a client would, so its streams are closed.
                await c.delete("/mcp", headers={**headers, "mcp-session-id": session})
            status = response.status_code
    # Collect here so the SDK's stream leak (see pytestmark) lands on this test.
    gc.collect()
    return status


async def test_unlisted_host_is_refused(served) -> None:
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    assert await _post_initialize("mcp.example.com") == 421


async def test_host_listed_in_allowed_hosts_is_accepted(served, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_ALLOWED_HOSTS", "mcp.example.com")

    server.main(["--transport", "http", "--host", "0.0.0.0"])

    assert served == ["streamable-http"]
    assert await _post_initialize("mcp.example.com") == 200


async def test_allowed_host_with_its_own_origin_is_accepted(served, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_ALLOWED_HOSTS", "mcp.example.com")

    server.main(["--transport", "http", "--host", "0.0.0.0"])

    assert await _post_initialize("mcp.example.com", origin="https://mcp.example.com") == 200


# ── Startup: OAuth settings and the exposure warning ─────────────────────


def test_oauth_without_issuer_stops_with_the_variable_named(served, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH", "oauth")
    monkeypatch.setenv("SN_HTTP_AUTH_RESOURCE_URL", "https://mcp.example.com/mcp")

    with pytest.raises(SystemExit, match="SN_HTTP_AUTH_ISSUER"):
        server.main(["--transport", "http"])
    assert served == []


def test_oauth_without_resource_url_stops_with_the_variable_named(served, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH", "oauth")
    monkeypatch.setenv("SN_HTTP_AUTH_ISSUER", "https://idp.example.com")

    with pytest.raises(SystemExit, match="SN_HTTP_AUTH_RESOURCE_URL"):
        server.main(["--transport", "http"])
    assert served == []


def test_oauth_on_the_sse_transport_is_refused(served, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH", "oauth")
    monkeypatch.setenv("SN_HTTP_AUTH_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("SN_HTTP_AUTH_RESOURCE_URL", "https://mcp.example.com/mcp")

    with pytest.raises(SystemExit, match="--transport http"):
        server.main(["--transport", "sse"])
    assert served == []


def test_unauthenticated_http_on_a_public_interface_warns(served, capsys) -> None:
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    err = capsys.readouterr().err
    assert "server.http_unauthenticated" in err
    assert "SN_HTTP_AUTH" in err


def test_unauthenticated_http_on_loopback_does_not_warn(served, capsys) -> None:
    server.main(["--transport", "http", "--host", "127.0.0.1"])

    assert "server.http_unauthenticated" not in capsys.readouterr().err
