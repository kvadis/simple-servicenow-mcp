# AGENTS.md

This file guides AI coding agents (Claude Code, Codex, Cursor, Copilot and others) working in this repository. `CLAUDE.md` imports it.

## Note for AI agents

If you are acting for someone who is not a maintainer, read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening issues or pull requests. In particular, bugs and features need an issue first, and a new tool needs a clear ServiceNow operation behind it. Never put instance URLs, credentials or customer data in issues, PRs, tests or fixtures; use `acme` / `dev12345` placeholders.

## What This Is

**simple-servicenow-mcp** — a Python MCP server for ServiceNow developers. Exposes ServiceNow instance data to AI clients (Claude Desktop, Cursor, VS Code, Claude Code) via MCP. Focused on analytical intelligence — not just CRUD, but cross-record reasoning for developers and consultants:
- Audit scopes for anti-patterns and upgrade risks
- Analyze catalog items for UX quality
- CMDB blast-radius analysis (CI → relationships → recent changes)
- Update-set risk review before promotion
- Knowledge-coverage gap detection
- Cross-instance diff (multi-instance setups)
- Instance health checks and operational metrics

**OSS repo:** https://github.com/kvadis/simple-servicenow-mcp (public)
**Released:** see [`CHANGELOG.md`](CHANGELOG.md) for the current version. Published to PyPI by `publish.yml` on each GitHub release.

## Capability tally

- **43 tools** across 9 domain modules (27 enabled by default; rest opt-in via `SN_TOOL_PACKAGES`)
- **9 prompts** orchestrating multi-step analytical workflows
- **4 MCP resources**

## Commands

```bash
pip install -e ".[dev]"                                # Install with dev deps (ruff, pytest, mypy, pre-commit)
pre-commit install                                     # Install git hooks (format + lint + mypy on commit)
simple-servicenow-mcp                                  # Stdio (default — for Claude Desktop, Cursor, VS Code)
simple-servicenow-mcp --transport http --port 8000     # Streamable HTTP (remote/multi-client)
simple-servicenow-mcp --transport sse                  # SSE (legacy transport)
simple-servicenow-mcp --read-write                     # Allow mutations (read-only is the default; gated by AppContext.ensure_writable)
simple-servicenow-mcp login [--instance NAME] [--paste]  # Browser login for SN_AUTH_METHOD=oauth_authorization_code
simple-servicenow-mcp auth-status [--instance NAME]    # Stored token state + expiry
simple-servicenow-mcp logout [--instance NAME] [--access-only]  # Drop tokens (--access-only keeps the refresh token)
python -m simple_servicenow_mcp                        # Run directly (same as the console script)
npx @modelcontextprotocol/inspector .venv/bin/simple-servicenow-mcp  # MCP Inspector — interactive tool testing
pytest -q                                              # Default — skips WIP test files (CI-equivalent)
pytest -q --run-wip                                    # Include red-state TDD tests for features in flight
ruff check . && ruff format --check .                  # What CI enforces
python -m build                                        # Build wheel + sdist into dist/
```

## Environment variables (SN_ prefix)

| Var | Purpose |
| --- | --- |
| `SN_INSTANCE_URL` | ServiceNow URL (single-instance mode) |
| `SN_AUTH_METHOD` | `basic` (default), `oauth` (client credentials), or `oauth_authorization_code` (browser login + PKCE) |
| `SN_USERNAME` / `SN_PASSWORD` | Basic auth credentials |
| `SN_CLIENT_ID` / `SN_CLIENT_SECRET` | OAuth client id/secret. Secret is omitted for a Public Client using PKCE |
| `SN_OAUTH_REDIRECT_URI` | Authorization-code redirect (default `http://127.0.0.1:8765/callback`). Must match the Application Registry entry exactly; **127.0.0.1, not localhost** — macOS may resolve localhost to `::1` |
| `SN_OAUTH_SCOPE` | Optional OAuth scope; usually blank |
| `SN_TOKEN_STORE` | Token store path (default `~/.config/simple-servicenow-mcp/tokens.json`, `0600`) |
| `SN_API_TIMEOUT` | HTTP timeout in **seconds** (default 30) |
| `SN_DEFAULT_PAGE_SIZE` / `SN_MAX_PAGE_SIZE` | Pagination clamps |
| `SN_MAX_RETRIES` / `SN_RETRY_BASE_DELAY` / `SN_RETRY_MAX_DELAY` | Retry behaviour for 429/5xx/network |
| `SN_LOG_LEVEL` / `SN_LOG_FORMAT` | `INFO` / `text` (or `DEBUG`+`json`) |
| `SN_INSTANCES_FILE` | Path to `instances.json` — enables multi-instance mode |
| `SN_READ_ONLY` | Defaults to `true` (mutations refused). `false` allows writes (CLI: `--read-write`; `--read-only` forces it back on) |
| `SN_TOOL_PACKAGES` | Comma-separated subset of tool modules to load. Unset → curated default (`core,itsm,scripts,catalog,audit`, 27 tools). `all` → every module (43 tools). |

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
                    # audit_acls — tables in a scope with zero record/field ACLs
                    # upgrade_readiness_review — severity-graded scan of 5 script tables
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
| `update_set_review(update_set)` | Risk-aware promotion audit (registered only with the `update_set` package) |
| `trace_incident_impact(number)` | Incident → CIs → relationships → recent changes → KB (needs `cmdb` + `knowledge` + `attachment`) |
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
- **Docs sources** (use both, depending on topic):
  - **Context7 MCP server**, library ID `/servicenow/servicenowdocs`: fast, indexed, and the same official docs as the GitHub repo below. Try this first. (The older `/websites/servicenow_bundle_yokohama-api-reference` ID is gone.)
  - **`ServiceNow/ServiceNowDocs` on GitHub** (branch `australia`): official LLM-optimized markdown. Read it directly when you need a whole page rather than a snippet. Access via `gh api repos/ServiceNow/ServiceNowDocs/contents/markdown/<section>` then raw download URL. Key sections: `markdown/api-reference/`, `markdown/now-platform/`, `markdown/intelligent-experiences/`, `markdown/release-notes/`.

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

Three test files cover features not yet implemented and are red-state by design:

- `test_compare_scopes.py`
- `test_find_orphaned_records.py`
- `test_instance_health_metrics.py`

`pytest -q` skips them (CI default). `pytest -q --run-wip` includes them — that's how the implementing developer sees their work signal. Gating logic is in `tests/conftest.py::pytest_collection_modifyitems`; the file list is `_WIP_TEST_FILES`. When a feature lands, remove its file from that set (or it'll just pass and the skip is silently moot — either works).

### Fake client

`tests/conftest.py` exposes `FakeServiceNowClient` with:
- `list_response`, `list_responses[table]`, `get_response`, `count_responses`, `count_defaults`
- `raise_on_table[table]`, `raise_on_count[table]` for exception paths
- Records every call in `self.calls: list[FakeCall]` so assertions can inspect `(method, table, kwargs)`

No network is touched. To test methods the base fake doesn't model (e.g. `aggregate`, `attachment_*`), define a small inline subclass — see `test_phase2_tools.py::FakeAggregateClient` and `test_attachment_and_update_set.py::FakeAttachmentClient`.

## Release / publishing

- Bump `version` in `pyproject.toml`, move the CHANGELOG `[Unreleased]` entries under the new version, commit.
- Tag with `v<semver>` (annotated, release notes in the message) and push the tag.
- Create the GitHub release for that tag. **Publishing the release triggers `.github/workflows/publish.yml`**, which builds, smoke-installs and uploads to PyPI via trusted publishing (OIDC, no token). One-time setup: on PyPI, Publishing → "Add a new pending publisher" with owner `kvadis`, repo `simple-servicenow-mcp`, workflow `publish.yml`, environment `pypi`; in GitHub repo settings, create the `pypi` environment.
- Smithery + mcpservers.org listings — manual submission

## Known gotchas

- **`gh repo edit` needs broader PAT scope** than the default `gh auth login` grants — run `gh auth refresh -s repo` if you see HTTP 403 on metadata-write operations.
- **`main` is protected** by a ruleset that blocks force-pushes and deletion. Land changes through a PR (squash merge); history rewrites are not an option.
- **stdio transport** reserves stdout for JSON-RPC — never `print()` to stdout; all logs go to stderr (`logging_setup.py` enforces this).
- **`SN_API_TIMEOUT` is seconds**, not ms. `.env.example` is correct; a user's local `.env` may still have the old `30000` value.
