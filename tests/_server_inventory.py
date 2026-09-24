"""Start the real server over stdio and list what it exposes.

Tool modules and some prompts register at import time depending on
``SN_TOOL_PACKAGES``, so an in-process registry only ever shows one package
set. A subprocess shows exactly what a client would see. Dummy credentials
are enough: nothing contacts ServiceNow until a tool is called.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REFERENCE_ARGS = ["-c", "from simple_servicenow_mcp.server import main; main()"]


@dataclass(frozen=True)
class Inventory:
    tools: tuple[str, ...]
    prompts: tuple[str, ...]
    resources: tuple[str, ...]  # fixed URIs and URI templates


async def server_inventory(
    args: list[str] = REFERENCE_ARGS, *, packages: str = "all", cwd: Path | None = None
) -> Inventory:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SN_")}
    env.update(
        {
            "SN_INSTANCE_URL": "https://acme.service-now.com",
            "SN_USERNAME": "u",
            "SN_PASSWORD": "p",
            "SN_TOOL_PACKAGES": packages,
            "SN_LOG_LEVEL": "WARNING",
        }
    )
    params = StdioServerParameters(
        command=sys.executable, args=args, env=env, cwd=str(cwd) if cwd else None
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = sorted(t.name for t in (await session.list_tools()).tools)
        prompts = sorted(p.name for p in (await session.list_prompts()).prompts)
        fixed = [str(r.uri) for r in (await session.list_resources()).resources]
        templates = [
            t.uriTemplate for t in (await session.list_resource_templates()).resourceTemplates
        ]
        return Inventory(tuple(tools), tuple(prompts), tuple(sorted(fixed + templates)))
