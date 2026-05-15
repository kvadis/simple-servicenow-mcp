"""Tests for the attachment and update-set tool families."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

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


# ── Attachment client stub ────────────────────────────────────────────


class FakeAttachmentClient:
    """Attachment-API stand-in. Mirrors the methods tools use."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.list_response: list[dict[str, Any]] = []
        self.metadata_response: dict[str, Any] = {}
        self.download_response: tuple[bytes, str] = (b"", "application/octet-stream")
        self.upload_response: dict[str, Any] = {"sys_id": "new-attach"}
        self.deleted: list[tuple[str, str]] = []

    async def list_records(self, table: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("list_records", (table,), kwargs))
        return self.list_response

    async def get_record(self, table: str, sys_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_record", (table, sys_id), kwargs))
        return self.metadata_response

    async def attachment_metadata(self, sys_id: str) -> dict[str, Any]:
        self.calls.append(("attachment_metadata", (sys_id,), {}))
        return self.metadata_response

    async def attachment_download(self, sys_id: str) -> tuple[bytes, str]:
        self.calls.append(("attachment_download", (sys_id,), {}))
        return self.download_response

    async def attachment_upload(
        self,
        table_name: str,
        table_sys_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "attachment_upload",
                (table_name, table_sys_id, file_name),
                {"content": content, "content_type": content_type},
            )
        )
        return self.upload_response

    async def delete_record(self, table: str, sys_id: str) -> None:
        self.deleted.append((table, sys_id))

    async def close(self) -> None:
        pass


# ── Attachments ───────────────────────────────────────────────────────


async def test_list_attachments_scopes_to_owning_record() -> None:
    from simple_servicenow_mcp.tools.attachment import list_attachments

    client = FakeAttachmentClient()
    client.list_response = [{"sys_id": "a1", "file_name": "log.txt"}]

    result = await list_attachments("incident", "INC123", _ctx(client))  # type: ignore[arg-type]

    assert result == [{"sys_id": "a1", "file_name": "log.txt"}]
    name, args, kwargs = client.calls[0]
    assert name == "list_records"
    assert args == ("sys_attachment",)
    assert kwargs["query"] == "table_name=incident^table_sys_id=INC123"


async def test_download_attachment_text_mime_returns_decoded_text() -> None:
    from simple_servicenow_mcp.tools.attachment import download_attachment

    client = FakeAttachmentClient()
    client.download_response = (b"hello world", "text/plain; charset=utf-8")

    result = await download_attachment("a1", _ctx(client))  # type: ignore[arg-type]

    assert result["encoding"] == "text"
    assert result["content"] == "hello world"
    assert result["size_bytes"] == 11


async def test_download_attachment_json_mime_returns_text() -> None:
    """``application/json`` is treated as text-like."""
    from simple_servicenow_mcp.tools.attachment import download_attachment

    client = FakeAttachmentClient()
    client.download_response = (b'{"k": 1}', "application/json")

    result = await download_attachment("a1", _ctx(client))  # type: ignore[arg-type]
    assert result["encoding"] == "text"
    assert result["content"] == '{"k": 1}'


async def test_download_attachment_binary_mime_returns_base64() -> None:
    from simple_servicenow_mcp.tools.attachment import download_attachment

    client = FakeAttachmentClient()
    client.download_response = (b"\x89PNG\r\n\x1a\n", "image/png")

    result = await download_attachment("a1", _ctx(client))  # type: ignore[arg-type]

    assert result["encoding"] == "base64"
    assert base64.b64decode(result["content"]) == b"\x89PNG\r\n\x1a\n"


async def test_download_attachment_exceeding_max_bytes_is_refused() -> None:
    from simple_servicenow_mcp.tools.attachment import download_attachment

    client = FakeAttachmentClient()
    client.download_response = (b"x" * 2000, "text/plain")

    with pytest.raises(ToolError, match="exceeds max_bytes=500"):
        await download_attachment("a1", _ctx(client), max_bytes=500)  # type: ignore[arg-type]


async def test_download_attachment_undecodable_text_falls_back_to_base64() -> None:
    """If a mime claims text but bytes aren't UTF-8, we base64 it."""
    from simple_servicenow_mcp.tools.attachment import download_attachment

    client = FakeAttachmentClient()
    client.download_response = (b"\xff\xfe\xfd", "text/plain")

    result = await download_attachment("a1", _ctx(client))  # type: ignore[arg-type]
    assert result["encoding"] == "base64"


async def test_upload_attachment_text_encoding_sends_utf8_bytes() -> None:
    from simple_servicenow_mcp.tools.attachment import upload_attachment

    client = FakeAttachmentClient()

    result = await upload_attachment(
        "incident",
        "INC123",
        "note.txt",
        "hello",
        _ctx(client),  # type: ignore[arg-type]
        content_type="text/plain",
        encoding="text",
    )

    assert result == {"sys_id": "new-attach"}
    _, args, kwargs = client.calls[0]
    assert args == ("incident", "INC123", "note.txt")
    assert kwargs["content"] == b"hello"
    assert kwargs["content_type"] == "text/plain"


async def test_upload_attachment_base64_encoding_decodes_first() -> None:
    from simple_servicenow_mcp.tools.attachment import upload_attachment

    client = FakeAttachmentClient()
    payload = base64.b64encode(b"\x89PNG\r\n").decode("ascii")

    await upload_attachment(
        "incident",
        "INC123",
        "icon.png",
        payload,
        _ctx(client),  # type: ignore[arg-type]
        content_type="image/png",
        encoding="base64",
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["content"] == b"\x89PNG\r\n"


async def test_upload_attachment_rejects_invalid_base64() -> None:
    from simple_servicenow_mcp.tools.attachment import upload_attachment

    client = FakeAttachmentClient()
    with pytest.raises(ToolError, match="not valid base64"):
        await upload_attachment(
            "incident",
            "INC123",
            "x.bin",
            "not-base64-!@#",
            _ctx(client),  # type: ignore[arg-type]
            encoding="base64",
        )


async def test_upload_attachment_refused_in_read_only() -> None:
    from simple_servicenow_mcp.tools.attachment import upload_attachment

    client = FakeAttachmentClient()
    with pytest.raises(ToolError, match="read-only mode"):
        await upload_attachment(
            "incident",
            "INC123",
            "x.txt",
            "x",
            _ctx(client, read_only=True),  # type: ignore[arg-type]
        )


async def test_delete_attachment_without_confirm_returns_preview() -> None:
    from simple_servicenow_mcp.tools.attachment import delete_attachment

    client = FakeAttachmentClient()
    client.metadata_response = {"file_name": "logs.zip", "size_bytes": 2048}

    result = await delete_attachment("a1", _ctx(client))  # type: ignore[arg-type]

    assert result["preview"] is True
    assert result["would_delete"] == {"file_name": "logs.zip", "size_bytes": 2048}
    assert client.deleted == []  # not deleted


async def test_delete_attachment_with_confirm_deletes() -> None:
    from simple_servicenow_mcp.tools.attachment import delete_attachment

    client = FakeAttachmentClient()
    result = await delete_attachment("a1", _ctx(client), confirm=True)  # type: ignore[arg-type]

    assert result == {"deleted": True, "sys_id": "a1"}
    assert client.deleted == [("sys_attachment", "a1")]


# ── Update Set tools ──────────────────────────────────────────────────


class FakeUpdateSetClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.list_response: list[dict[str, Any]] = []
        self.list_responses: dict[str, list[dict[str, Any]]] = {}
        self.get_response: dict[str, Any] = {}
        self.count_response: int = 0

    async def list_records(self, table: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("list_records", (table,), kwargs))
        return self.list_responses.get(table, self.list_response)

    async def get_record(self, table: str, sys_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_record", (table, sys_id), kwargs))
        return self.get_response

    async def get_count(self, table: str, query: str | None = None) -> int:
        self.calls.append(("get_count", (table,), {"query": query}))
        return self.count_response

    async def close(self) -> None:
        pass


async def test_list_update_sets_defaults_to_in_progress() -> None:
    from simple_servicenow_mcp.tools.update_set import list_update_sets

    client = FakeUpdateSetClient()
    await list_update_sets(_ctx(client))  # type: ignore[arg-type]

    _, args, kwargs = client.calls[0]
    assert args == ("sys_update_set",)
    assert kwargs["query"] == "state=in_progress"


async def test_get_update_set_returns_record_plus_change_count() -> None:
    from simple_servicenow_mcp.tools.update_set import get_update_set

    client = FakeUpdateSetClient()
    client.get_response = {"sys_id": "us-1", "name": "My Update Set"}
    client.count_response = 7

    result = await get_update_set("us-1", _ctx(client))  # type: ignore[arg-type]

    assert result["update_set"] == {"sys_id": "us-1", "name": "My Update Set"}
    assert result["change_count"] == 7


async def test_list_update_set_changes_scopes_query_to_set() -> None:
    from simple_servicenow_mcp.tools.update_set import list_update_set_changes

    client = FakeUpdateSetClient()
    await list_update_set_changes("us-1", _ctx(client))  # type: ignore[arg-type]

    _, args, kwargs = client.calls[0]
    assert args == ("sys_update_xml",)
    assert kwargs["query"] == "update_set=us-1"


async def test_summarize_update_set_flags_deletes_and_high_risk() -> None:
    """Aggregate by type/action, surface deletes and high-risk table touches."""
    from simple_servicenow_mcp.tools.update_set import summarize_update_set

    client = FakeUpdateSetClient()
    client.get_response = {"sys_id": "us-1", "name": "Risky Set"}
    client.list_responses = {
        "sys_update_xml": [
            {"type": "sys_script", "action": "INSERT_OR_UPDATE", "target_name": "BR.X"},
            {"type": "sys_security_acl", "action": "INSERT_OR_UPDATE", "target_name": "ACL.Y"},
            {"type": "incident", "action": "DELETE", "target_name": "INC-old"},
            {"type": "sys_property", "action": "INSERT_OR_UPDATE", "target_name": "prop.z"},
        ],
    }

    result = await summarize_update_set("us-1", _ctx(client))  # type: ignore[arg-type]

    assert result["total_changes"] == 4
    assert result["by_action"]["INSERT_OR_UPDATE"] == 3
    assert result["by_action"]["DELETE"] == 1
    assert result["by_type"]["sys_script"] == 1
    # sys_script + sys_security_acl are in the high-risk list
    high_risk_types = {c["type"] for c in result["high_risk_changes"]}
    assert high_risk_types == {"sys_script", "sys_security_acl"}
    # The DELETE row surfaces too
    assert len(result["deletes"]) == 1
    assert result["deletes"][0]["target_name"] == "INC-old"
