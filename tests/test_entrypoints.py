"""Every documented way to start the server must expose the full inventory.

``python -m simple_servicenow_mcp.server`` runs ``server.py`` as ``__main__``, a
second copy of the module: the tool, prompt and resource modules register on
the importable ``simple_servicenow_mcp.server.mcp``, so the ``__main__`` copy
served an empty server without any error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _server_inventory import server_inventory


@pytest.mark.parametrize(
    "args",
    [["-m", "simple_servicenow_mcp"], ["-m", "simple_servicenow_mcp.server"]],
    ids=["python -m simple_servicenow_mcp", "python -m simple_servicenow_mcp.server"],
)
async def test_module_entrypoints_serve_the_full_inventory(args: list[str], tmp_path: Path) -> None:
    expected = await server_inventory(cwd=tmp_path)
    assert expected.tools, "reference server exposed no tools"

    assert await server_inventory(args, cwd=tmp_path) == expected
