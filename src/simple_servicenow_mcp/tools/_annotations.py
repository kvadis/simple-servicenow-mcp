"""Reusable ToolAnnotations presets for ServiceNow operations.

MCP clients use these hints for UX (e.g. confirming before destructive ops);
they are advisory, not enforced. Every ServiceNow operation is openWorld
because the target instance is an external mutable system.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
APPEND = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
UPDATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
DELETE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)
