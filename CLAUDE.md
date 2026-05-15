# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## What This Is

**simple-servicenow-mcp** — a Python MCP server for ServiceNow developers. Exposes ServiceNow instance data to AI clients (Claude Desktop, Cursor, VS Code, Claude Code) via MCP. Focused on analytical intelligence — not just CRUD, but cross-record reasoning for developers and consultants:
- Audit scopes for anti-patterns and upgrade risks
- Analyze catalog items for UX quality
- CMDB blast-radius analysis (CI → relationships → recent changes)
- Update-set risk review before promotion
- Knowledge-coverage gap detection
- Cross-instance diff (multi-instance setups)
- Instance health checks and operational metrics

**OSS repo:** https://github.com/kvadis/simple-servicenow-mcp (currently private)
**Released:** v0.1.0 (commit `035f914`, CI-green on Python 3.10/3.11/3.12/3.13)

## Capability tally

- **40 tools** across 9 domain modules (24 enabled by default; rest opt-in via `SN_TOOL_PACKAGES`)
- **9 prompts** orchestrating multi-step analytical workflows
- **4 MCP resources**

## Commands

```bash
pip install -e ".[dev]"                                # Install with dev deps (ruff, pytest, mypy, pre-commit)
pre-commit install                                     # Install git hooks (format + lint + mypy on commit)
simple-servicenow-mcp                                  # Stdio (default — for Claude Desktop, Cursor, VS Code)
simple-servicenow-mcp --transport http --port 8000     # Streamable HTTP (remote/multi-client)
simple-servicenow-mcp --transport sse                  # SSE (legacy transport)
simple-servicenow-mcp --read-only                      # Refuse all mutations (gated by AppContext.ensure_writable)
python -m simple_servicenow_mcp.server                 # Run directly
mcp dev src/simple_servicenow_mcp/server.py            # MCP Inspector — interactive tool testing
pytest -q                                              # Default — skips WIP test files (CI-equivalent)
pytest -q --run-wip                                    # Include red-state TDD tests for features in flight
ruff check . && ruff format --check .                  # What CI enforces
python -m build                                        # Build wheel + sdist into dist/
```

## Environment variables (SN_ prefix)

| Var | Purpose |
| --- | --- |
| `SN_INSTANCE_URL` | ServiceNow URL (single-instance mode) |
| `SN_AUTH_METHOD` | `basic` (default) or `oauth` |
| `SN_USERNAME` / `SN_PASSWORD` | Basic auth credentials |
| `SN_CLIENT_ID` / `SN_CLIENT_SECRET` | OAuth 2.0 client credentials |
| `SN_API_TIMEOUT` | HTTP timeout in **seconds** (default 30) |
| `SN_DEFAULT_PAGE_SIZE` / `SN_MAX_PAGE_SIZE` | Pagination clamps |
| `SN_MAX_RETRIES` / `SN_RETRY_BASE_DELAY` / `SN_RETRY_MAX_DELAY` | Retry behaviour for 429/5xx/network |
| `SN_LOG_LEVEL` / `SN_LOG_FORMAT` | `INFO` / `text` (or `DEBUG`+`json`) |
| `SN_INSTANCES_FILE` | Path to `instances.json` — enables multi-instance mode |
| `SN_READ_ONLY` | `true` to refuse mutations (CLI: `--read-only`) |
| `SN_TOOL_PACKAGES` | Comma-separated subset of tool modules to load. Unset → curated default (`core,itsm,scripts,catalog,audit`, ~24 tools). `all` → every module (~40 tools). |

See `.env.example` for the canonical list. **Common gotcha:** `SN_API_TIMEOUT` is in seconds — `30000` would be 8.3 hours.

## Architecture

```
src/simple_servicenow_mcp/
  __init__.py
  server.py         # FastMCP instance, lifespan, CLI entry (--transport, --read-only)
                    # AppContext with client_for(instance) + ensure_writable(op)
  config.py         # Pydantic Settings (env-based, SN_ prefix)
  client.py         # ServiceNow REST client (httpx async), structured ServiceNowAPIError
                    # Auth (Basic + OAuth client credentials), retry loop with Retry-After,
                    # Table + Stats + Attachment + sys_dictionary discovery APIs
  instances.py      # Multi-instance registry (SN_INSTANCES_FILE → named clients)
                    # Per-instance overrides on top of fallback Settings
  packages.py       # SN_TOOL_PACKAGES parsing (gates which tool modules import)
  logging_setup.py  # stdlib logging + JSON formatter (no structlog dependency)
  tools/
    _annotations.py # READ/CREATE/UPDATE/DELETE/APPEND annotation presets
                    # All have openWorldHint=True (ServiceNow is external state)
    table.py        # Generic Table API (list, get, create, update, delete, count,
                    #   search, describe, aggregate). Field-name validation via sys_dictionary
    incident.py     # Incident shortcuts (list, create, comment, resolve)
    script.py       # Scripts: BR, SI, CS, UP, UA + get_script_body
    catalog.py      # Catalog: categories, items, variables, analyze_catalog_item (UX findings)
    cmdb.py         # CMDB: list_cis, get_ci, list_ci_relationships (split direction), find_cis_by_class
    knowledge.py    # KB: list, search (LIKE-OR across description+text+keywords), get
    attachment.py   # sys_attachment: list, metadata, download (text/base64 dispatch),
                    #   upload (multipart), delete (preview/confirm)
    update_set.py   # sys_update_set: list, get + count, list_changes, summarize w/ risk flags
    audit.py        # audit_scope — hardcoded sys_ids + deprecated APIs in scripts
  resources.py      # MCP resources: instance/info, health, schema/{table}, scope/{name}
  prompts.py        # MCP prompts (9 total) — see "Prompts" below
tests/
  conftest.py       # FakeServiceNowClient + WIP test gating (--run-wip flag)
  test_*.py         # Behavioural tests; 6 files are WIP (parallel TDD session)
.github/
  workflows/ci.yml  # Matrix py 3.10-3.13: ruff check + format + pytest + wheel build
  ISSUE_TEMPLATE/   # bug + feature templates
  PULL_REQUEST_TEMPLATE.md
  dependabot.yml    # Weekly grouped updates for pip + actions + docker
```

## Prompts (9)

| Prompt | Purpose |
| --- | --- |
| `audit_scope(scope)` | Anti-pattern scan of a scope's scripts |
| `analyze_catalog(category=None)` | Catalog UX audit |
| `upgrade_readiness_review(scope, target_version)` | Cross-table script review with 🔴🟠🟡 classification (red-state TDD) |
| `triage_incident(number)` | Linear-style triage with action enum (red-state TDD) |
| `update_set_review(update_set)` | Risk-aware promotion audit |
| `trace_incident_impact(number)` | Incident → CIs → relationships → recent changes → KB |
| `cross_instance_diff(table, query, instance_a, instance_b)` | Drift / promotion candidates / reverse-drift across instances |
| `knowledge_coverage_report(time_window_days=90)` | Aggregate incidents vs KB count by category |
| `instance_health_check()` | Active incidents, P1 count, unassigned, in-flight changes |

## Key Patterns

- **FastMCP** — `@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()` decorators
- **Lifespan context** — `ServiceNowClient` (or `InstanceRegistry`) created in `lifespan()`, accessed via `ctx.request_context.lifespan_context`
- **Multi-instance resolution** — every tool calls `app.client_for(instance)` (returns default when `instance=None`)
- **Read-only enforcement** — every mutating tool calls `app.ensure_writable(op)` before touching ServiceNow
- **Tool errors** — raise `ToolError` from `mcp.server.fastmcp.exceptions`, never return raw exceptions
- **Native return types** — FastMCP auto-derives `outputSchema` and emits `structuredContent`
- **Config** — `pydantic-settings` with `env_prefix="SN_"`, loads `.env` automatically
- **Auth** — Basic (base64 header) or OAuth 2.0 client credentials (cached token, auto-refresh 60s before expiry)
- **Transport** — stdio (stdout reserved for JSON-RPC, all logs to stderr)
- **Retries** — 429 honours `Retry-After` seconds, otherwise exponential backoff with `SN_RETRY_MAX_DELAY` cap
- **Schema validation** — `create_record` / `update_record` walk `sys_db_object.super_class` to assemble valid field names from `sys_dictionary`; fail-open if discovery is denied

## Adding New Tools

1. Create `src/simple_servicenow_mcp/tools/my_module.py`
2. Import `mcp` from `..server` and use `@mcp.tool(annotations=_a.READ)` (or CREATE/UPDATE/DELETE/APPEND)
3. Add `ctx: Context` and `instance: str | None = None` to the signature
4. For reads: resolve via `_client(ctx, instance)` (small helper at the top of each module). For writes: use `_writable(ctx, instance, "op_name")` which calls `ensure_writable` first.
5. Return native types (`dict`, `list[dict]`, etc.) — FastMCP auto-emits `structuredContent`
6. Wrap ServiceNow calls in `try/except` and re-raise as `ToolError(str(e))`
7. If creating a new tools module: register it in `packages.py` (`PACKAGES` dict) and `server.py` (conditional import block)
8. Add behavioural tests in `tests/test_<module>.py` using the existing `FakeServiceNowClient`
9. Document in `README.md` tool reference table and `CHANGELOG.md` under `[Unreleased]`

## ServiceNow API Notes

- Primary: REST Table API (`/api/now/table/{table}`)
- Stats API for counts + aggregations (`/api/now/stats/{table}`)
- Attachment API: `/api/now/attachment/{sys_id}/file` (binary), `/api/now/attachment/file` (multipart upload)
- Encoded queries: `^` as AND, `^OR` as OR (e.g. `active=true^priority=1`)
- `sysparm_display_value=true` returns human-readable values; `false` (default in this MCP) returns raw sys_ids
- Always paginate with `limit` + `offset`, never dump all records
- For docs, use Context7 (`mcp__plugin_context7_context7`) with library ID `/websites/servicenow_bundle_yokohama-api-reference`

## Testing Conventions

### TDD Workflow
- Write failing tests BEFORE implementation
- AAA pattern: Arrange-Act-Assert
- One assertion per test when possible (use `pytest.fail()`-style guards for setup)
- Test names describe behaviour: `test_<thing>_should_<expected>` or `test_<thing>_<condition>`

### Test-First Rules
- When asked for a feature, write tests first
- Tests should FAIL initially (no implementation exists)
- Only after tests are written, implement minimal code to pass

### WIP test gating (CI-friendly TDD)

Six test files cover features being implemented in parallel sessions and are red-state by design:

- `test_audit_acls.py`
- `test_compare_scopes.py`
- `test_find_orphaned_records.py`
- `test_instance_health_metrics.py`
- `test_triage_incident.py`
- `test_upgrade_readiness_review.py`

`pytest -q` skips them (CI default). `pytest -q --run-wip` includes them — that's how the implementing developer sees their work signal. Gating logic is in `tests/conftest.py::pytest_collection_modifyitems`; the file list is `_WIP_TEST_FILES`. When a feature lands, remove its file from that set (or it'll just pass and the skip is silently moot — either works).

### Fake client

`tests/conftest.py` exposes `FakeServiceNowClient` with:
- `list_response`, `list_responses[table]`, `get_response`, `count_responses`, `count_defaults`
- `raise_on_table[table]`, `raise_on_count[table]` for exception paths
- Records every call in `self.calls: list[FakeCall]` so assertions can inspect `(method, table, kwargs)`

No network is touched. To test methods the base fake doesn't model (e.g. `aggregate`, `attachment_*`), define a small inline subclass — see `test_phase2_tools.py::FakeAggregateClient` and `test_attachment_and_update_set.py::FakeAttachmentClient`.

## Release / publishing

- Tag with `v<semver>` annotated tag including release notes
- `python -m build && twine upload dist/*` for PyPI (not done yet — needs PyPI registration)
- GitHub release: `gh release create v0.1.0 --notes-from-tag` (may need `gh auth refresh -s repo`)
- Smithery + mcpservers.org listings — manual submission

## Known gotchas

- **`gh repo edit` needs broader PAT scope** than the default `gh auth login` grants — run `gh auth refresh -s repo` if you see HTTP 403 on metadata-write operations.
- **Force-pushing main** preserves history by branching `archive/<reason>` first. See the `release: v0.1.0` history for an example.
- **stdio transport** reserves stdout for JSON-RPC — never `print()` to stdout; all logs go to stderr (`logging_setup.py` enforces this).
- **`SN_API_TIMEOUT` is seconds**, not ms. `.env.example` is correct; a user's local `.env` may still have the old `30000` value.
