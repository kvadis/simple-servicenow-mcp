"""HTTP transport: Host allow-list and OAuth resource-server mode.

The tests drive the real CLI entry point (`server.main`) with `mcp.run` swapped
out, then exercise the Starlette app the server would have served.
"""

from __future__ import annotations

import gc
from collections.abc import Iterator

import httpx
import pytest

from _fake_idp import FakeIdP, mint
from simple_servicenow_mcp import http_auth, server
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


async def _request(
    method: str, path: str, host: str, *, token: str | None = None, origin: str | None = None
) -> httpx.Response:
    headers = {
        "Host": host,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
    app = mcp.streamable_http_app()
    # The session manager only runs inside the app's lifespan.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            body = _INITIALIZE if method == "POST" else None
            response = await c.request(method, path, json=body, headers=headers)
            if session := response.headers.get("mcp-session-id"):
                # End the session as a client would, so its streams are closed.
                await c.delete("/mcp", headers={**headers, "mcp-session-id": session})
    # Collect here so the SDK's stream leak (see pytestmark) lands on this test.
    gc.collect()
    return response


async def _post_initialize(host: str, origin: str | None = None) -> int:
    return (await _request("POST", "/mcp", host, origin=origin)).status_code


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


# ── OAuth resource-server mode, end to end through main() ────────────────

IDP_ISSUER = "https://idp.example.com/oauth2/default"
RESOURCE = "https://mcp.example.com/mcp"


@pytest.fixture
def oauth(served, monkeypatch) -> FakeIdP:
    """SN_HTTP_AUTH=oauth against a fake IdP; main() still does the wiring."""
    idp = FakeIdP(
        issuer=IDP_ISSUER, discovery_path="/oauth2/default/.well-known/openid-configuration"
    )
    monkeypatch.setenv("SN_HTTP_AUTH", "oauth")
    monkeypatch.setenv("SN_HTTP_AUTH_ISSUER", IDP_ISSUER)
    monkeypatch.setenv("SN_HTTP_AUTH_RESOURCE_URL", RESOURCE)
    monkeypatch.setattr(
        server,
        "configure_http_auth",
        lambda m, settings: http_auth.configure_http_auth(m, settings, transport=idp.transport),
        raising=False,
    )
    return idp


def _token(**overrides) -> str:
    return mint({"iss": IDP_ISSUER, "aud": RESOURCE, **overrides})


async def test_request_without_a_token_gets_401_naming_the_metadata(oauth) -> None:
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    response = await _request("POST", "/mcp", "mcp.example.com")

    assert response.status_code == 401
    assert (
        'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'
        in response.headers["www-authenticate"]
    )


async def test_protected_resource_metadata_points_at_the_issuer(oauth, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH_REQUIRED_SCOPES", "mcp.access")
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    response = await _request("GET", "/.well-known/oauth-protected-resource/mcp", "mcp.example.com")

    assert response.status_code == 200
    document = response.json()
    assert document["resource"] == RESOURCE
    assert document["authorization_servers"] == [IDP_ISSUER]
    assert document["scopes_supported"] == ["mcp.access"]


async def test_valid_token_on_the_resource_host_initializes(oauth) -> None:
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    response = await _request("POST", "/mcp", "mcp.example.com", token=_token())

    assert response.status_code == 200


async def test_token_without_the_required_scope_gets_403(oauth, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH_REQUIRED_SCOPES", "mcp.access")
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    response = await _request("POST", "/mcp", "mcp.example.com", token=_token(scope="mcp.read"))

    assert response.status_code == 403


async def test_token_for_another_audience_gets_401(oauth) -> None:
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    token = _token(aud="https://other.example.com/mcp")

    assert (await _request("POST", "/mcp", "mcp.example.com", token=token)).status_code == 401


async def test_audience_setting_accepts_an_api_identifier(oauth, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH_AUDIENCE", f"api://simple-sn,{RESOURCE}")
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    token = _token(aud="api://simple-sn")

    assert (await _request("POST", "/mcp", "mcp.example.com", token=token)).status_code == 200


async def test_explicit_jwks_url_skips_discovery(oauth, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH_JWKS_URL", "https://idp.example.com/keys")
    server.main(["--transport", "http", "--host", "0.0.0.0"])

    await _request("POST", "/mcp", "mcp.example.com", token=_token())

    assert oauth.paths == ["/keys"]


def test_plain_http_issuer_is_refused(oauth, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH_ISSUER", "http://idp.example.com")

    with pytest.raises(SystemExit, match="https"):
        server.main(["--transport", "http"])


def test_plain_http_issuer_on_loopback_is_allowed_for_testing(oauth, monkeypatch) -> None:
    monkeypatch.setenv("SN_HTTP_AUTH_ISSUER", "http://127.0.0.1:9000")

    server.main(["--transport", "http"])

    assert oauth is not None and server.mcp.settings.auth is not None
