"""The docs must describe what the server actually exposes.

Tool counts were hand-maintained in several files and drifted (0.2.0 said 40
tools and a 24-tool default; `triage_incident` was missing from the reference).
These tests compare the docs with the real server inventory.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import re
from pathlib import Path

import pytest

from _server_inventory import Inventory, server_inventory
from simple_servicenow_mcp.packages import DEFAULT_PACKAGES, PACKAGES

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TOOLS_DIR = ROOT / "src" / "simple_servicenow_mcp" / "tools"

pytestmark = pytest.mark.skipif(not DOCS.is_dir(), reason="docs/ not present")


@functools.cache
def _inventory(packages: str) -> Inventory:
    return asyncio.run(server_inventory(packages=packages))


def _tools_per_module() -> dict[str, int]:
    """Count ``@mcp.tool``-decorated functions in each tools module."""
    counts: dict[str, int] = {}
    for path in TOOLS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        counts[path.stem] = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "tool"
                for d in node.decorator_list
            )
        )
    return counts


def _package_counts() -> dict[str, int]:
    per_module = _tools_per_module()
    return {package: per_module[module] for package, module in PACKAGES.items()}


def _documented_rows(section: str) -> set[str]:
    """First-column names of the table rows in one part of docs/tools.md."""
    return set(re.findall(r"^\| `([^`]+)` \|", section, re.M))


def test_every_tool_prompt_and_resource_is_in_the_tool_reference() -> None:
    # Some prompts share a tool's name, so each kind is looked up in its own part.
    reference = (DOCS / "tools.md").read_text()
    tools_part, rest = reference.split("\n## Prompts\n", 1)
    prompts_part, resources_part = rest.split("\n## Resources\n", 1)
    inventory = _inventory("all")

    assert set(inventory.tools) <= _documented_rows(tools_part), "tools missing from docs/tools.md"
    assert set(inventory.prompts) <= _documented_rows(prompts_part), "prompts missing"
    assert set(inventory.resources) <= _documented_rows(resources_part), "resources missing"


def test_package_table_matches_the_server() -> None:
    config = (DOCS / "configuration.md").read_text()
    table = {m[1]: int(m[2]) for m in re.finditer(r"^\| `(\w+)` \| (\d+) \|", config, re.M)}
    counts = _package_counts()
    total = len(_inventory("all").tools)

    assert sum(counts.values()) == total, "AST count and served tools disagree"
    assert table == {**counts, "all": total}


def test_stated_totals_match_the_server() -> None:
    counts = _package_counts()
    default = len(_inventory("").tools)
    total = len(_inventory("all").tools)
    config = (DOCS / "configuration.md").read_text()
    env_example = (ROOT / ".env.example").read_text()
    agents = (ROOT / "AGENTS.md").read_text()

    assert default == sum(counts[p] for p in DEFAULT_PACKAGES)
    assert f"default set ({default} tools)" in config
    assert f"core,itsm              # {counts['core'] + counts['itsm']} tools" in config
    assert f"audit ({default} tools)" in env_example
    assert f"every module ({total} tools" in env_example
    assert f"**{total} tools**" in agents
    assert f"({default} enabled by default" in agents


def test_readme_links_are_absolute_so_they_work_on_pypi() -> None:
    readme = (ROOT / "README.md").read_text()

    relative = re.findall(r"\]\((?!https?://|#)([^)]+)\)", readme)

    assert not relative, f"PyPI can't resolve relative links: {relative}"
