"""Attachment calls on the real client, through httpx.MockTransport.

The tool tests use a fake client, so they never reach ``_request``. These do:
the Attachment API needs per-call headers (``Accept: */*`` to download the
file, the file's own ``Content-Type`` to upload it) on top of the auth header.
"""

from __future__ import annotations

import httpx

from simple_servicenow_mcp.client import ServiceNowClient
from simple_servicenow_mcp.config import Settings


def _client(calls: list[httpx.Request]) -> ServiceNowClient:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/file") and request.method == "GET":
            return httpx.Response(200, content=b"hello", headers={"Content-Type": "text/plain"})
        return httpx.Response(201, json={"result": {"sys_id": "att1", "file_name": "a.txt"}})

    settings = Settings(  # type: ignore[call-arg]
        instance_url="https://acmedev.service-now.com",
        auth_method="basic",
        username="u",
        password="p",
    )
    return ServiceNowClient(settings, transport=httpx.MockTransport(handle))


async def test_download_sends_auth_and_accept_any() -> None:
    calls: list[httpx.Request] = []

    body, content_type = await _client(calls).attachment_download("att1")

    assert (body, content_type) == (b"hello", "text/plain")
    assert calls[0].url.path == "/api/now/attachment/att1/file"
    assert calls[0].headers["Accept"] == "*/*"
    assert calls[0].headers["Authorization"].startswith("Basic ")


async def test_upload_sends_auth_and_the_file_content_type() -> None:
    calls: list[httpx.Request] = []

    result = await _client(calls).attachment_upload(
        "incident", "inc1", "a.txt", b"hello", content_type="text/plain"
    )

    assert result["sys_id"] == "att1"
    sent = calls[0]
    assert sent.url.path == "/api/now/attachment/file"
    assert dict(sent.url.params) == {
        "table_name": "incident",
        "table_sys_id": "inc1",
        "file_name": "a.txt",
    }
    assert sent.content == b"hello"
    assert sent.headers["Content-Type"] == "text/plain"
    assert sent.headers["Accept"] == "application/json"
    assert sent.headers["Authorization"].startswith("Basic ")
