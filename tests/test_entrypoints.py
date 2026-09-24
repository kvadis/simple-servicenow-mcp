"""Every documented way to start the server must expose the full inventory.

``python -m simple_servicenow_mcp.server`` runs ``server.py`` as ``__main__``, a
second copy of the module: the tool, prompt and resource modules register on
the importable ``simple_servicenow_mcp.server.mcp``, so the ``__main__`` copy
served an empty server without any error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_REFERENCE = ["-c", "from simple_servicenow_mcp.server import main; main()"]


async def _inventory(args: list[str], cwd: Path) -> tuple[list[str], list[str], list[str]]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SN_")}
    env.update(
        {
            "SN_INSTANCE_URL": "https://acme.service-now.com",
            "SN_USERNAME": "u",
            "SN_PASSWORD": "p",
            "SN_TOOL_PACKAGES": "all",
            "SN_LOG_LEVEL": "WARNING",
        }
    )
    params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(cwd))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = sorted(t.name for t in (await session.list_tools()).tools)
        prompts = sorted(p.name for p in (await session.list_prompts()).prompts)
        resources = sorted(str(r.uri) for r in (await session.list_resources()).resources)
        return tools, prompts, resources


@pytest.mark.parametrize(
    "args",
    [["-m", "simple_servicenow_mcp"], ["-m", "simple_servicenow_mcp.server"]],
    ids=["python -m simple_servicenow_mcp", "python -m simple_servicenow_mcp.server"],
)
async def test_module_entrypoints_serve_the_full_inventory(args: list[str], tmp_path: Path) -> None:
    expected = await _inventory(_REFERENCE, tmp_path)
    assert expected[0], "reference server exposed no tools"

    assert await _inventory(args, tmp_path) == expected
