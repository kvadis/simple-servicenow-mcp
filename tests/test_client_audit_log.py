"""Audit trail: with HTTP OAuth every caller shares one ServiceNow identity, so
the server's request log has to say who made each call, and never the token."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from simple_servicenow_mcp.client import ServiceNowClient
from simple_servicenow_mcp.config import Settings
from simple_servicenow_mcp.logging_setup import setup_logging

_RAW_TOKEN = "eyJhbGciOiJSUzI1NiJ9.secret-token-body.signature"


def _client() -> ServiceNowClient:
    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://acmedev.service-now.com",
        auth_method="basic",
        username="u",
        password="p",
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"result": []}))
    return ServiceNowClient(settings, transport=transport)


@pytest.fixture
def signed_in() -> Iterator[None]:
    access = AccessToken(
        token=_RAW_TOKEN,
        client_id="client-abc",
        scopes=["mcp.read"],
        subject="user-1",
        claims={"sub": "user-1", "preferred_username": "ada@example.com"},
    )
    reset = auth_context_var.set(AuthenticatedUser(access))
    yield
    auth_context_var.reset(reset)


def _ok_record(stderr: str) -> dict:
    records = [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]
    return next(r for r in records if r.get("event") == "api.request.ok")


async def test_request_log_names_the_signed_in_user(signed_in, capsys) -> None:
    setup_logging(level="INFO", fmt="json")

    await _client().list_records("incident", limit=1)

    err = capsys.readouterr().err
    record = _ok_record(err)
    assert record["user"] == "user-1"
    assert record["username"] == "ada@example.com"
    assert _RAW_TOKEN not in err
    assert "secret-token-body" not in err


async def test_request_log_without_a_token_has_no_user(capsys) -> None:
    setup_logging(level="INFO", fmt="json")

    await _client().list_records("incident", limit=1)

    record = _ok_record(capsys.readouterr().err)
    assert "user" not in record
    assert "username" not in record
