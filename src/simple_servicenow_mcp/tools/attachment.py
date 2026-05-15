"""Attachment tools: list, metadata, download, upload, delete.

ServiceNow attachments are stored in ``sys_attachment`` with the binary payload
served by ``/api/now/attachment/{sys_id}/file``. Because LLMs can't usefully
consume arbitrary binaries, ``download_attachment`` text-decodes anything that
looks textual (mime ``text/*``, ``application/json``, ``application/xml``,
``application/yaml``) and base64-encodes the rest, with a size cap so we don't
flood the LLM with megabytes.
"""

from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from ..client import ServiceNowClient
from ..server import AppContext, mcp
from . import _annotations as _a

_ATTACHMENT_FIELDS = (
    "sys_id,file_name,content_type,size_bytes,size_compressed,table_name,"
    "table_sys_id,sys_created_on,sys_created_by,download_link"
)

_DEFAULT_MAX_BYTES = 1_000_000  # 1 MiB cap for direct returns
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_SUFFIXES = ("+json", "+xml", "+yaml", "+yml")
_TEXT_MIMES = {
    "application/json",
    "application/xml",
    "application/yaml",
    "application/yml",
    "application/javascript",
    "application/x-yaml",
    "application/x-sh",
}


def _client(ctx: Context, instance: str | None) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    return app.client_for(instance)


def _writable(ctx: Context, instance: str | None, op: str) -> ServiceNowClient:
    app: AppContext = ctx.request_context.lifespan_context
    app.ensure_writable(op)
    return app.client_for(instance)


def _is_text_mime(mime: str) -> bool:
    base = mime.split(";", 1)[0].strip().lower()
    if base.startswith(_TEXT_MIME_PREFIXES):
        return True
    if any(base.endswith(s) for s in _TEXT_MIME_SUFFIXES):
        return True
    return base in _TEXT_MIMES


@mcp.tool(annotations=_a.READ)
async def list_attachments(
    table: str,
    sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> list[dict[str, Any]]:
    """List all attachments on a given record from ``sys_attachment``.

    Args:
        table: Owning table name (e.g. ``incident``, ``change_request``)
        sys_id: sys_id of the owning record
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).list_records(
            "sys_attachment",
            query=f"table_name={table}^table_sys_id={sys_id}",
            fields=_ATTACHMENT_FIELDS,
            limit=100,
        )
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def get_attachment_metadata(
    attachment_sys_id: str,
    ctx: Context,
    instance: str | None = None,
) -> dict[str, Any]:
    """Get metadata for one attachment without downloading the body.

    Args:
        attachment_sys_id: sys_id of the ``sys_attachment`` record
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    try:
        return await _client(ctx, instance).attachment_metadata(attachment_sys_id)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.READ)
async def download_attachment(
    attachment_sys_id: str,
    ctx: Context,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    force_base64: bool = False,
    instance: str | None = None,
) -> dict[str, Any]:
    """Download an attachment's body.

    Text-like payloads (``text/*``, JSON, XML, YAML, JS, shell) are returned
    decoded as text. Everything else is base64-encoded. Anything larger than
    ``max_bytes`` is refused — fetch metadata only.

    Args:
        attachment_sys_id: sys_id of the ``sys_attachment`` record
        max_bytes: Hard cap on returned payload size in bytes (default 1 MiB).
            Raises ``ToolError`` with the actual size if exceeded.
        force_base64: Skip text-decoding even for text-like mimes.
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.

    Returns:
        ``{sys_id, content_type, size_bytes, encoding: "text"|"base64", content}``
    """
    client = _client(ctx, instance)
    try:
        body, content_type = await client.attachment_download(attachment_sys_id)
    except Exception as e:
        raise ToolError(str(e)) from e

    size = len(body)
    if size > max_bytes:
        raise ToolError(
            f"attachment {attachment_sys_id} is {size} bytes — exceeds max_bytes={max_bytes}. "
            f"Use get_attachment_metadata() instead, or pass a larger max_bytes."
        )

    if not force_base64 and _is_text_mime(content_type):
        try:
            text = body.decode("utf-8")
            return {
                "sys_id": attachment_sys_id,
                "content_type": content_type,
                "size_bytes": size,
                "encoding": "text",
                "content": text,
            }
        except UnicodeDecodeError:
            # Mime lied — fall through to base64.
            pass

    return {
        "sys_id": attachment_sys_id,
        "content_type": content_type,
        "size_bytes": size,
        "encoding": "base64",
        "content": base64.b64encode(body).decode("ascii"),
    }


@mcp.tool(annotations=_a.CREATE)
async def upload_attachment(
    table: str,
    sys_id: str,
    file_name: str,
    content: str,
    ctx: Context,
    content_type: str = "text/plain",
    encoding: str = "text",
    instance: str | None = None,
) -> dict[str, Any]:
    """Attach a file to a ServiceNow record.

    Args:
        table: Owning table name
        sys_id: sys_id of the owning record
        file_name: Display name for the attachment
        content: File body. Interpreted per ``encoding``.
        content_type: MIME type (e.g. ``text/plain``, ``application/json``,
            ``image/png``). Default ``text/plain``.
        encoding: ``"text"`` (default — ``content`` is a UTF-8 string) or
            ``"base64"`` (``content`` is base64 of arbitrary bytes).
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    if encoding == "text":
        body = content.encode("utf-8")
    elif encoding == "base64":
        try:
            body = base64.b64decode(content, validate=True)
        except (base64.binascii.Error, ValueError) as e:  # type: ignore[attr-defined]
            raise ToolError(f"content is not valid base64: {e}") from e
    else:
        raise ToolError(f"encoding must be 'text' or 'base64' (got {encoding!r})")

    client = _writable(ctx, instance, "upload_attachment")
    try:
        return await client.attachment_upload(table, sys_id, file_name, body, content_type)
    except Exception as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_a.DELETE)
async def delete_attachment(
    attachment_sys_id: str,
    ctx: Context,
    confirm: bool = False,
    instance: str | None = None,
) -> dict[str, Any]:
    """Delete an attachment. **Destructive — requires ``confirm=true``.**

    With ``confirm=False`` (default) returns a preview (file_name, size) so the
    LLM can show what would be deleted. Re-issue with ``confirm=true`` to
    actually delete.

    Args:
        attachment_sys_id: sys_id of the ``sys_attachment`` record
        confirm: Must be True to actually delete
        instance: Named instance from SN_INSTANCES_FILE; omit for the default.
    """
    client = _writable(ctx, instance, "delete_attachment")
    try:
        if not confirm:
            meta = await client.attachment_metadata(attachment_sys_id)
            return {
                "preview": True,
                "sys_id": attachment_sys_id,
                "would_delete": meta,
                "instruction": "Re-issue this call with confirm=true to actually delete.",
            }
        await client.delete_record("sys_attachment", attachment_sys_id)
        return {"deleted": True, "sys_id": attachment_sys_id}
    except Exception as e:
        raise ToolError(str(e)) from e
