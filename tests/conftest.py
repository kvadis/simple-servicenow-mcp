"""Shared test fixtures.

A FakeServiceNowClient is injected via a SimpleNamespace Context so tools
execute without touching a real instance. Each test records the issued REST
calls on the fake so assertions can verify the LLM's intent was translated
correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simple_servicenow_mcp.config import Settings
from simple_servicenow_mcp.server import AppContext

# ── WIP test gating ───────────────────────────────────────────────────
# Some test files are pure TDD red-state — they assert features that the
# parallel session hasn't implemented yet. They're useful locally (the
# developer sees them fail and uses that as the work signal) but they break
# CI, so we auto-skip them unless ``--run-wip`` is passed.

_WIP_TEST_FILES = {
    "test_compare_scopes.py",
    "test_find_orphaned_records.py",
    "test_instance_health_metrics.py",
    "test_upgrade_readiness_review.py",
}


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own configuration out of every test.

    Deleting an SN_* variable does not override ``.env`` — pydantic-settings
    falls back to the dotenv file in the cwd — so a repo-root ``.env`` or
    ``instances.json`` would otherwise leak into tests that run fine in CI.
    """
    import os

    for name in list(os.environ):
        if name.startswith("SN_"):
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-wip",
        action="store_true",
        default=False,
        help="Also collect tests for not-yet-implemented features (the parallel "
        "TDD session's red-state files). Default: skipped so CI stays green.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-wip"):
        return
    skip = pytest.mark.skip(
        reason="WIP test file (run with --run-wip to include). "
        "These cover features the parallel TDD session hasn't implemented yet."
    )
    for item in items:
        if Path(str(item.fspath)).name in _WIP_TEST_FILES:
            item.add_marker(skip)


@dataclass
class FakeCall:
    method: str
    table: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class FakeServiceNowClient:
    """In-memory stand-in for ServiceNowClient — no network.

    Tests that touch a single table set ``list_response``. Tests that span
    multiple tables (e.g. audit_scope hits sys_scope, sys_script,
    sys_script_include in one run) populate ``list_responses[table]``; the
    per-table mapping wins when both are set.
    """

    def __init__(self) -> None:
        self.calls: list[FakeCall] = []
        self.list_response: list[dict[str, Any]] = []
        self.list_responses: dict[str, list[dict[str, Any]]] = {}
        self.get_response: dict[str, Any] = {}
        self.count_responses: dict[tuple[str, str | None], int] = {}
        self.count_defaults: dict[str, int] = {}
        self.raise_on_table: dict[str, Exception] = {}
        self.raise_on_count: dict[str, Exception] = {}

    async def list_records(self, table: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(FakeCall("list", table, kwargs))
        if table in self.raise_on_table:
            raise self.raise_on_table[table]
        if table in self.list_responses:
            return self.list_responses[table]
        return self.list_response

    async def get_record(self, table: str, sys_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(FakeCall("get", table, {"sys_id": sys_id, **kwargs}))
        return self.get_response

    async def get_count(self, table: str, query: str | None = None) -> int:
        self.calls.append(FakeCall("count", table, {"query": query}))
        if table in self.raise_on_count:
            raise self.raise_on_count[table]
        if (table, query) in self.count_responses:
            return self.count_responses[(table, query)]
        return self.count_defaults.get(table, 0)

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_client() -> FakeServiceNowClient:
    return FakeServiceNowClient()


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        instance_url="https://test.service-now.com",
        auth_method="basic",
        username="u",
        password="p",
    )


@pytest.fixture
def fake_ctx(fake_client: FakeServiceNowClient, fake_settings: Settings) -> Any:
    """Minimal Context stand-in — only `request_context.lifespan_context` is touched."""
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=AppContext(client=fake_client, settings=fake_settings)  # type: ignore[arg-type]
        )
    )
