"""Failing tests driving the not-yet-implemented `upgrade_readiness_review` tool.

`upgrade_readiness_review` is the programmatic counterpart to the
`upgrade_readiness_review` *prompt* (`prompts.py:74-149`) and a strict
superset of `audit_scope`:

- audits FIVE script tables instead of two (business rules, script
  includes, client scripts, UI policies, UI actions)
- grades every finding with severity = blocking | risk | info
- echoes `target_version` for cross-referencing release deprecations

Implementation should live in `simple_servicenow_mcp.tools.audit`
alongside the in-flight `audit_scope`.

Behaviour-style names per Testing Conventions in AGENTS.md.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ── Sample data ─────────────────────────────────────────────────────────

_SCOPE_RECORD = {"sys_id": "scope000000000000000000000000abcd", "scope": "x_co_app"}

_BR_WITH_GET_XML_WAIT = {
    "sys_id": "br00000000000000000000000000001",
    "name": "LegacySyncFetch",
    "script": (
        "var gr = new GlideRecord('incident');\n"
        "gr.addQuery('active', true);\n"
        "gr.query();\n"
        "gr.next();\n"
        "var resp = new GlideAjax('CheckSomething');\n"
        "resp.getXMLWait();  // BLOCKING: synchronous AJAX\n"
    ),
}
_CS_WITH_DOM_ACCESS = {
    "sys_id": "cs00000000000000000000000000001",
    "name": "FieldHider",
    "script": (
        "function onLoad() {\n"
        "  document.getElementById('cost_center_label').style.display = 'none';\n"
        "}\n"
    ),
}
_SI_WITH_GS_LOG = {
    "sys_id": "si00000000000000000000000000001",
    "name": "ChattyLogger",
    "script": "gs.log('legacy log call — should be gs.info()');",
}


def _stub_scope_with_artefacts(fake_client, **artefacts):
    """Helper: seed sys_scope + the five script tables."""
    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.list_responses["sys_script"] = artefacts.get("sys_script", [])
    fake_client.list_responses["sys_script_include"] = artefacts.get("sys_script_include", [])
    fake_client.list_responses["sys_script_client"] = artefacts.get("sys_script_client", [])
    fake_client.list_responses["sys_ui_policy"] = artefacts.get("sys_ui_policy", [])
    fake_client.list_responses["sys_ui_action"] = artefacts.get("sys_ui_action", [])


# ── 1. Tool existence ───────────────────────────────────────────────────


def test_upgrade_readiness_review_tool_should_exist_in_audit_module() -> None:
    """upgrade_readiness_review must be importable from tools.audit — not implemented yet."""
    # Arrange / Act
    from simple_servicenow_mcp.tools import audit as audit_module

    # Assert
    assert hasattr(audit_module, "upgrade_readiness_review"), (
        "upgrade_readiness_review is not yet implemented in simple_servicenow_mcp.tools.audit"
    )


# ── 2-3. Scope resolution ───────────────────────────────────────────────


async def test_upgrade_readiness_review_should_resolve_scope_via_sys_scope_first(
    fake_client,
    fake_ctx,
) -> None:
    """First REST call resolves the scope by name (mirrors audit_scope contract)."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client)

    # Act
    await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    assert fake_client.calls, "upgrade_readiness_review made no REST calls"
    first = fake_client.calls[0]
    assert first.table == "sys_scope"
    assert "scope=x_co_app" in (first.kwargs.get("query") or "")


async def test_upgrade_readiness_review_should_return_error_json_when_scope_not_found(
    fake_client,
    fake_ctx,
) -> None:
    """Unknown scope returns a JSON error payload — must not raise."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_scope"] = []

    # Act
    result = await upgrade_readiness_review("x_does_not_exist", fake_ctx)

    # Assert
    parsed = json.loads(result)
    assert "error" in parsed
    assert "x_does_not_exist" in parsed["error"]


# ── 4. Querying all five script tables ──────────────────────────────────


async def test_upgrade_readiness_review_should_query_all_five_script_tables(
    fake_client,
    fake_ctx,
) -> None:
    """Discovery phase must hit BR, SI, client scripts, UI policies, UI actions
    (per prompts.py:108-112). All five filter by the resolved scope sys_id."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client)

    # Act
    await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    queried_tables = {c.table for c in fake_client.calls if c.method == "list"}
    expected = {
        "sys_scope",
        "sys_script",
        "sys_script_include",
        "sys_script_client",
        "sys_ui_policy",
        "sys_ui_action",
    }
    missing = expected - queried_tables
    assert not missing, f"missing list_records calls for: {sorted(missing)}"

    # Every script-table query must filter by the resolved scope sys_id.
    for table in expected - {"sys_scope"}:
        calls_for = [c for c in fake_client.calls if c.table == table]
        assert calls_for, f"no list call for {table}"
        query = calls_for[0].kwargs.get("query") or ""
        assert _SCOPE_RECORD["sys_id"] in query, (
            f"{table} query must filter by the resolved scope sys_id, got: {query!r}"
        )


# ── 5. scriptISNOTEMPTY filter for UI policies / UI actions ─────────────


async def test_upgrade_readiness_review_should_skip_ui_policies_without_script(
    fake_client,
    fake_ctx,
) -> None:
    """UI policies and UI actions only audit records that actually carry script.
    Otherwise we audit thousands of OOTB rule-only records with nothing to grep.

    sys_ui_action has a ``script`` column, so ``scriptISNOTEMPTY`` works there.
    sys_ui_policy does not: its scripts live in ``script_true`` / ``script_false``,
    and both hold ``function onCondition() {}`` boilerplate even when scripting
    is off — so the discriminating filter is ``run_scripts=true``.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client)

    # Act
    await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    expected_filter = {"sys_ui_policy": "run_scripts=true", "sys_ui_action": "scriptISNOTEMPTY"}
    for table, needle in expected_filter.items():
        calls = [c for c in fake_client.calls if c.table == table]
        assert calls
        query = calls[0].kwargs.get("query") or ""
        assert needle in query, f"{table} query must include {needle}, got: {query!r}"


# ── 6-8. Severity-graded detectors ──────────────────────────────────────


async def test_upgrade_readiness_review_should_flag_get_xml_wait_as_blocking(
    fake_client,
    fake_ctx,
) -> None:
    """Synchronous AJAX (`getXMLWait()`) is a hard upgrade blocker.

    Severity ladder from prompts.py:118-135: blocking → risk → info.
    """
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client, sys_script=[_BR_WITH_GET_XML_WAIT])

    # Act
    result = await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f
        for f in findings
        if f.get("sys_id") == _BR_WITH_GET_XML_WAIT["sys_id"] and f.get("severity") == "blocking"
    ]
    assert matching, (
        f"expected a blocking-severity finding for getXMLWait in "
        f"{_BR_WITH_GET_XML_WAIT['sys_id']}, got findings: {findings!r}"
    )
    assert "getXMLWait" in (matching[0].get("evidence") or "")


async def test_upgrade_readiness_review_should_flag_dom_access_in_client_scripts_as_blocking(
    fake_client,
    fake_ctx,
) -> None:
    """Direct DOM access in client scripts breaks on Next Experience — blocking."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client, sys_script_client=[_CS_WITH_DOM_ACCESS])

    # Act
    result = await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f
        for f in findings
        if f.get("sys_id") == _CS_WITH_DOM_ACCESS["sys_id"] and f.get("severity") == "blocking"
    ]
    assert matching, (
        f"expected a blocking-severity finding for document.* DOM access in "
        f"{_CS_WITH_DOM_ACCESS['sys_id']}, got findings: {findings!r}"
    )


async def test_upgrade_readiness_review_should_flag_gs_log_as_info(
    fake_client,
    fake_ctx,
) -> None:
    """Legacy `gs.log(` is an info-level modernisation hint, not a blocker."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client, sys_script_include=[_SI_WITH_GS_LOG])

    # Act
    result = await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    findings = parsed.get("findings", [])
    matching = [
        f
        for f in findings
        if f.get("sys_id") == _SI_WITH_GS_LOG["sys_id"] and f.get("severity") == "info"
    ]
    assert matching, (
        f"expected an info-severity finding for gs.log( in "
        f"{_SI_WITH_GS_LOG['sys_id']}, got findings: {findings!r}"
    )


# ── 9. Severity summary ─────────────────────────────────────────────────


async def test_upgrade_readiness_review_should_include_severity_counts_in_summary(
    fake_client,
    fake_ctx,
) -> None:
    """Top-level `severity_counts` aggregates findings by severity for the
    executive summary (per prompts.py:142-144)."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(
        fake_client,
        sys_script=[_BR_WITH_GET_XML_WAIT],  # 1 blocking
        sys_script_client=[_CS_WITH_DOM_ACCESS],  # 1 blocking
        sys_script_include=[_SI_WITH_GS_LOG],  # 1 info
    )

    # Act
    result = await upgrade_readiness_review("x_co_app", fake_ctx)

    # Assert
    parsed = json.loads(result)
    counts = parsed.get("severity_counts") or {}
    assert counts.get("blocking", 0) >= 2, f"expected ≥2 blocking findings, got: {counts!r}"
    assert counts.get("info", 0) >= 1, f"expected ≥1 info finding, got: {counts!r}"


# ── 10. Target version echo ─────────────────────────────────────────────


async def test_upgrade_readiness_review_should_echo_target_version_when_provided(
    fake_client,
    fake_ctx,
) -> None:
    """A `target_version` argument is surfaced in the output so reviewers know
    which release the report was scoped to (per prompts.py:78,138-99)."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    _stub_scope_with_artefacts(fake_client)

    # Act
    result = await upgrade_readiness_review("x_co_app", fake_ctx, target_version="Yokohama")

    # Assert
    parsed = json.loads(result)
    assert parsed.get("target_version") == "Yokohama"


# ── 11. Error wrapping ──────────────────────────────────────────────────


async def test_upgrade_readiness_review_should_wrap_client_errors_in_tool_error(
    fake_client,
    fake_ctx,
) -> None:
    """Unexpected client errors mid-audit must be re-raised as ToolError."""
    # Arrange
    from simple_servicenow_mcp.tools.audit import (
        upgrade_readiness_review,  # type: ignore[attr-defined]
    )

    fake_client.list_responses["sys_scope"] = [_SCOPE_RECORD]
    fake_client.raise_on_table["sys_ui_action"] = RuntimeError("network down")

    # Act / Assert
    with pytest.raises(ToolError):
        await upgrade_readiness_review("x_co_app", fake_ctx)


# ── 12+. Detector precision ─────────────────────────────────────────────

_SYS_ID_A = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
_SYS_ID_B = "0f9e8d7c6b5a4938271605f4e3d2c1b0"


async def _review(fake_client, fake_ctx, **artefacts) -> dict:
    from simple_servicenow_mcp.tools.audit import upgrade_readiness_review

    _stub_scope_with_artefacts(fake_client, **artefacts)
    return json.loads(await upgrade_readiness_review("x_co_app", fake_ctx))


async def test_upgrade_readiness_review_should_report_field_and_line_of_each_finding(
    fake_client, fake_ctx
) -> None:
    """The prompt's findings table has a Line column; getXMLWait is on line 6 of the sample."""
    parsed = await _review(fake_client, fake_ctx, sys_script=[_BR_WITH_GET_XML_WAIT])

    [finding] = [f for f in parsed["findings"] if f["type"] == "sync_ajax"]
    assert (finding["field"], finding["line"]) == ("script", 6)
    assert finding["name"] == "LegacySyncFetch"
    assert finding["recommendation"]


async def test_upgrade_readiness_review_should_flag_current_update_only_in_before_after_rules(
    fake_client, fake_ctx
) -> None:
    """current.update() is a recursion hazard in before/after rules; in a display rule it is fine."""
    before = {
        "sys_id": "br_before",
        "name": "Before",
        "when": "before",
        "script": "current.update();",
    }
    display = {
        "sys_id": "br_display",
        "name": "Display",
        "when": "display",
        "script": "current.update();",
    }

    parsed = await _review(fake_client, fake_ctx, sys_script=[before, display])

    flagged = {f["sys_id"] for f in parsed["findings"] if f["type"] == "current_update_in_rule"}
    assert flagged == {"br_before"}


async def test_upgrade_readiness_review_should_report_each_hardcoded_sys_id_once_and_skip_own(
    fake_client, fake_ctx
) -> None:
    """Repeated ids collapse to one finding; a record referencing its own sys_id is not a finding."""
    record = {
        "sys_id": _SYS_ID_A,
        "name": "SelfAware",
        "script": f"var me = '{_SYS_ID_A}';\nvar x = '{_SYS_ID_B}';\nvar y = '{_SYS_ID_B}';",
    }

    parsed = await _review(fake_client, fake_ctx, sys_script_include=[record])

    ids = [f for f in parsed["findings"] if f["type"] == "hardcoded_sys_id"]
    assert [f["evidence"] for f in ids] == [f"var x = '{_SYS_ID_B}';"]
    assert ids[0]["severity"] == "risk"
    assert ids[0]["line"] == 2


async def test_upgrade_readiness_review_should_not_apply_dom_rule_to_server_scripts(
    fake_client, fake_ctx
) -> None:
    """``document.`` in a script include is not browser DOM access."""
    record = {"sys_id": "si_doc", "name": "DocBuilder", "script": "var d = document.title;"}

    parsed = await _review(fake_client, fake_ctx, sys_script_include=[record])

    assert [f for f in parsed["findings"] if f["type"] == "dom_access"] == []


async def test_upgrade_readiness_review_should_scan_both_ui_policy_script_fields(
    fake_client, fake_ctx
) -> None:
    """UI policies carry script_true and script_false; either can hold the blocker."""
    policy = {
        "sys_id": "up_1",
        "short_description": "Hide cost centre",
        "script_true": "function onCondition() {}",
        "script_false": "function onCondition() {\n  ga.getXMLWait();\n}",
    }

    parsed = await _review(fake_client, fake_ctx, sys_ui_policy=[policy])

    [finding] = [f for f in parsed["findings"] if f["type"] == "sync_ajax"]
    assert (finding["table"], finding["field"], finding["line"]) == (
        "sys_ui_policy",
        "script_false",
        2,
    )
    assert finding["name"] == "Hide cost centre"


async def test_upgrade_readiness_review_should_grade_verdict_from_worst_severity(
    fake_client, fake_ctx
) -> None:
    """red if anything blocks, yellow if only risks, green when clean."""
    risk_only = {"sys_id": "si_r", "name": "R", "script": f"var x = '{_SYS_ID_B}';"}

    clean = await _review(fake_client, fake_ctx)
    yellow = await _review(fake_client, fake_ctx, sys_script_include=[risk_only])
    red = await _review(fake_client, fake_ctx, sys_script=[_BR_WITH_GET_XML_WAIT])

    assert (clean["verdict"], yellow["verdict"], red["verdict"]) == ("green", "yellow", "red")
    assert clean["severity_counts"] == {"blocking": 0, "risk": 0, "info": 0}


async def test_upgrade_readiness_review_should_report_per_table_counts_and_out_of_scope(
    fake_client, fake_ctx
) -> None:
    """The report says what was looked at and, explicitly, what was not."""
    parsed = await _review(
        fake_client,
        fake_ctx,
        sys_script=[_BR_WITH_GET_XML_WAIT],
        sys_script_include=[_SI_WITH_GS_LOG],
    )

    assert parsed["per_table"] == {
        "sys_script": 1,
        "sys_script_include": 1,
        "sys_script_client": 0,
        "sys_ui_policy": 0,
        "sys_ui_action": 0,
    }
    assert parsed["records_audited"] == 2
    assert parsed["truncated"] is False
    assert parsed["out_of_scope"] and all(isinstance(s, str) for s in parsed["out_of_scope"])
