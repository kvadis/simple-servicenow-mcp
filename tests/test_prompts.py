"""Prompts: registration follows the loaded tool packages, and every prompt renders."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from mcp.server.fastmcp.prompts import base

from simple_servicenow_mcp import prompts
from simple_servicenow_mcp.server import mcp

_ALWAYS = {
    "audit_scope",
    "analyze_catalog",
    "upgrade_readiness_review",
    "triage_incident",
    "cross_instance_diff",
    "knowledge_coverage_report",
    "instance_health_check",
}
# These drive tools from opt-in packages (update_set; cmdb + knowledge + attachment).
_GATED = {"update_set_review", "trace_incident_impact"}


async def test_default_packages_register_only_prompts_whose_tools_are_loaded() -> None:
    """The test process imported the server with SN_TOOL_PACKAGES unset."""
    names = {p.name for p in await mcp.list_prompts()}

    assert names >= _ALWAYS
    assert not (_GATED & names), f"gated prompts leaked without their packages: {_GATED & names}"


def test_all_packages_register_every_prompt() -> None:
    """Module selection happens at import, so check the ``all`` case in a fresh process."""
    code = (
        "import asyncio, json; from simple_servicenow_mcp.server import mcp; "
        "print(json.dumps(sorted(p.name for p in asyncio.run(mcp.list_prompts()))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "SN_TOOL_PACKAGES": "all"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert set(json.loads(out.strip().splitlines()[-1])) >= _ALWAYS | _GATED


@pytest.mark.parametrize(
    "render",
    [
        lambda: prompts.audit_scope("x_co_app"),
        lambda: prompts.analyze_catalog(),
        lambda: prompts.analyze_catalog("Hardware"),
        lambda: prompts.upgrade_readiness_review("x_co_app"),
        lambda: prompts.upgrade_readiness_review("x_co_app", target_version="Zurich"),
        lambda: prompts.triage_incident("INC0010001"),
        lambda: prompts.update_set_review("Release 42"),
        lambda: prompts.trace_incident_impact("INC0010001"),
        lambda: prompts.cross_instance_diff("sys_script", "active=true", "dev", "prod"),
        lambda: prompts.knowledge_coverage_report(),
        lambda: prompts.instance_health_check(),
    ],
)
def test_every_prompt_renders_a_user_message(render) -> None:
    messages = render()

    assert messages and all(isinstance(m, base.UserMessage) for m in messages)
    assert all(m.content.text.strip() for m in messages)  # type: ignore[union-attr]


def test_upgrade_readiness_prompt_points_at_the_tool_and_the_right_ui_policy_filter() -> None:
    [message] = prompts.upgrade_readiness_review("x_co_app", target_version="Zurich")
    text = message.content.text  # type: ignore[union-attr]

    assert "`upgrade_readiness_review` tool" in text
    assert "run_scripts=true" in text
    assert "scriptISNOTEMPTY" not in text.split("list_ui_policies")[1].split("\n")[0]
    assert "Zurich" in text
