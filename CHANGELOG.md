# Changelog

All notable changes to this project will be documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`upgrade_readiness_review` tool** (`tools/audit.py`) — the programmatic counterpart of the prompt. Pulls every active business rule, script include, client script, scripted UI policy (`run_scripts=true`, scanning `script_true`/`script_false`) and scripted UI action in a scope and grades findings `blocking` (`getXMLWait()`, DOM access in client-side code), `risk` (hardcoded sys_ids, `current.update()` in before/after rules) or `info` (`gs.log()`, `gs.print()`), each with field, line number, evidence and a recommendation. Reports `severity_counts`, a red/yellow/green `verdict`, and an explicit `out_of_scope` list; only detectors that cannot misfire are included.
- **`audit_acls` tool** (`tools/audit.py`) — lists a scope's tables from `sys_db_object` and counts active record-type ACLs named `<table>` or `<table>.<field>` for each. Zero ACLs is reported as `no_acls`: `blocking` when the table extends nothing (only the wildcard `*` rules apply), `warning` when it inherits a parent table's ACLs (`inherits_from`). Pages past the 100-row clamp and reports `truncated`.
- **OAuth 2.0 Authorization Code + PKCE** (`SN_AUTH_METHOD=oauth_authorization_code`) — browser-delegated login, so no password is stored and access is tied to a real user identity. Where the instance has SSO configured, `/oauth_auth.do` redirects to the IdP, making this the SSO path; where it does not, it is still a browser login rather than a credential in `.env`. Existing `basic` and `oauth` (client credentials) methods are unchanged — `oauth` continues to mean client credentials.
- **`login` / `logout` / `auth-status` subcommands** (`auth_cli.py`) — an MCP server on stdio has no channel to a human, so authorisation runs as a separate process that writes a token the server picks up. `login` binds a loopback listener and opens a browser; `--paste` covers headless boxes and instances where only `oauth_redirect.do` may be registered. Dispatched before the server's own argparse, so the bare stdio invocation is untouched.
- **Persistent token store** (`token_store.py`) — `~/.config/simple-servicenow-mcp/tokens.json`, `0600` in a `0700` directory, written atomically, keyed by instance host + client id so tokens survive restarts, renames and single↔multi-instance switches. A damaged file degrades to "no token" rather than raising, since it is read during client construction and would otherwise break startup for every configured instance.
- **`refresh_token` grant with rotation support**, a refresh lock so concurrent tool calls spend one grant rather than N, and cross-process awareness — a token another process already refreshed is adopted instead of spending the grant again.
- **`401 → refresh → retry once`** in the request loop, with its own attempt budget (`max_retries=0` is legal and previously would have starved the re-auth). 401 stays out of the retryable set, so genuine permission errors still fail fast, and basic auth is excluded entirely.
- **Token status in diagnostics** — `servicenow://instance/info` and `servicenow://health` now report `token: {present, expires_in_s, refreshable, needs_login}`, so an expiring grant is visible before every tool starts failing at once.
- **First tests for the auth layer** — `_auth_headers` and `_refresh_oauth_token` previously had no coverage. 98 new tests across `test_token_store.py`, `test_oauth_pkce.py` (S256 known-answer from RFC 7636 Appendix B), `test_client_auth.py` (via `httpx.MockTransport`) and `test_auth_cli.py` (real loopback sockets).

- **CMDB tools** (`tools/cmdb.py`): `list_cis`, `get_ci`, `list_ci_relationships` (splits outgoing vs. incoming on `cmdb_rel_ci`), `find_cis_by_class`. All default to `display_value=true` since CMDB is reference-heavy.
- **Knowledge tools** (`tools/knowledge.py`): `list_knowledge_articles` (defaults to `workflow_state=published`), `search_knowledge` (LIKE-OR across short_description + text + keywords), `get_knowledge_article`.
- **`aggregate_records` tool** — Stats API wrapper with `sum` / `avg` / `min` / `max` / `count` + `group_by` + `having` for cross-record analysis without paginating raw rows. Backed by new `ServiceNowClient.aggregate()`.
- **Attachment tools** (`tools/attachment.py`): `list_attachments`, `get_attachment_metadata`, `download_attachment` (text/base64 dispatch, 1 MiB cap), `upload_attachment` (text or base64 content), `delete_attachment` (preview/confirm safety rail). Backed by new `ServiceNowClient.attachment_metadata/download/upload` methods using the `/api/now/attachment/file` multipart endpoint.
- **Update Set tools** (`tools/update_set.py`): `list_update_sets` (defaults to `state=in_progress`), `get_update_set` (record + change count), `list_update_set_changes` (`sys_update_xml`; excludes heavy XML payload unless `verbose=true`), `summarize_update_set` (aggregate by type/action, surface DELETE rows and high-risk-table touches: `sys_security_acl`, `sys_user_role`, `sys_script`, `sys_dictionary`, …).
- **`update_set_review` prompt** — opinionated three-step audit: summarise → drill into high-risk + deletes → produce a promotion checklist with 🔴/🟠/🟡 risk classification.
- **Tool-package gating** (`SN_TOOL_PACKAGES=core,itsm,cmdb,…`) — opt into a subset of tool modules so the LLM's tool list stays compact for narrow use cases. Packages: `core` / `itsm` / `scripts` / `catalog` / `cmdb` / `knowledge` / `attachment` / `update_set` / `audit` / `all` (default). Unknown names fail-fast at startup. Lifespan logs the resolved package set for diagnostics.
- **`trace_incident_impact` prompt** — end-to-end blast-radius analysis: incident → caller's CIs → upstream/downstream relationships → recent changes on those CIs → related KB articles → ranked root-cause hypothesis.
- **`cross_instance_diff` prompt** — compare a query across two configured instances. Surfaces drift, promotion candidates, and reverse-drift (hot-fixes that haven't been back-ported). Exploits multi-instance.
- **`knowledge_coverage_report` prompt** — aggregate incidents by category over a configurable window vs published KB count per category. Flags gaps where high-volume incident categories have thin documentation, suggests the highest-ROI articles to write.

Total tool count: **40** across 9 domains; **9** prompts; **4** resources.

---

### Fixed
- **Resources were invisible to clients.** `servicenow://instance/info` and `servicenow://health` declared a `ctx` parameter, which FastMCP takes as a sign of a URI template — so `resources/list` came back empty and only the two real templates were advertised. They now fetch the request context from the server and appear as plain resources. `health` checks every instance in multi-instance mode and reports the worst status; `schema/{table}` and `scope/{name}` return `{"error": ...}` instead of raising.
- **Prompts no longer reference tools that are not loaded.** `update_set_review` registers only when the `update_set` package is loaded; `trace_incident_impact` only with `cmdb` + `knowledge` + `attachment`. Default installs advertise 7 prompts, `SN_TOOL_PACKAGES=all` advertises 9.
- **Silent 100-row truncation.** `list_records` clamps every call at `SN_MAX_PAGE_SIZE`, so `describe_table` (limit 200), `summarize_update_set` (limit 500, whose `truncated` flag could therefore never be true), `audit_scope` (limit 200), `schema/{table}` and the schema discovery behind write validation (limit 2000) all quietly returned the first 100 rows. Each now pages explicitly (`tools/_paging.py`) and reports `truncated` where it matters; write validation no longer rejects valid inherited fields on wide tables.
- **`describe_table` includes inherited fields.** It walks `sys_db_object.super_class` (new public `ServiceNowClient.table_chain`), so `incident` now lists the `task` fields the write-validation error told users to look for; each entry's `name` says which table defines it.
- CONTRIBUTING cloned from the wrong GitHub account; README no longer claims every tool returns native types (the five analysis tools return a JSON report as text); duplicate `### Added` heading merged.

### Changed
- **Read-only by default.** `SN_READ_ONLY` now defaults to `true`; the 7 mutating tools refuse until the server is started with `--read-write` or `SN_READ_ONLY=false`. `--read-only` remains and overrides an env opt-in for one run. Pointing a fresh install at production can no longer change anything by accident.
- **`login` / `logout` / `auth-status` are quiet.** The CLI installs the package log handler (default level WARNING, `SN_LOG_LEVEL` to change), so FastMCP's Rich root handler no longer prints every httpx request under the login prompt.
- **`SN_TOOL_PACKAGES` default narrowed** from `all` (43 tools) to a curated subset — `core, itsm, scripts, catalog, audit` (27 tools, the "developer analytical" loop). Keeps the LLM's tool list compact out of the box; pass `SN_TOOL_PACKAGES=all` to opt back into every module, or pin a custom set. Domain-specific modules (`cmdb`, `knowledge`, `attachment`, `update_set`) are now opt-in.

## [0.1.0] — 2026-05-15

First public release.

### Added

**Core**
- FastMCP server with stdio, Streamable HTTP, and SSE transports (`--transport`, `--host`, `--port`).
- Async `ServiceNowClient` with Basic and OAuth 2.0 client-credentials auth.
- Structured `ServiceNowAPIError` exposing status / message / detail / retryable, plus per-status hints (400/401/403/404/409/429).
- Retry loop on 429 / 5xx / network errors with `Retry-After`-aware exponential backoff (`SN_MAX_RETRIES`, `SN_RETRY_BASE_DELAY`, `SN_RETRY_MAX_DELAY`).
- OAuth token caching with 60s refresh margin.
- Pagination clamping via `SN_DEFAULT_PAGE_SIZE` / `SN_MAX_PAGE_SIZE`.
- Structured logging (`SN_LOG_LEVEL`, `SN_LOG_FORMAT=text|json`) using stdlib `logging`.

**Tools** — 23 across 5 modules:
- `table.py` (8): `list_records`, `get_record`, `create_record`, `update_record`, `delete_record`, `search_records`, `describe_table`, `count_records`. Native return types → FastMCP emits `structuredContent`. `create_record` / `update_record` validate field names against `sys_dictionary` (walking `super_class` inheritance) and surface typos locally; `skip_validation` opt-out. `delete_record(confirm=False)` returns a preview without deleting.
- `incident.py` (4): `list_incidents`, `create_incident`, `add_incident_comment`, `resolve_incident`. `resolve_incident` accepts custom `close_state` for non-OOTB workflows.
- `script.py` (6): `list_business_rules`, `list_script_includes`, `list_client_scripts`, `list_ui_policies`, `list_ui_actions`, `get_script_body`.
- `catalog.py` (4): `list_catalog_categories`, `list_catalog_items`, `get_catalog_item_variables`, `analyze_catalog_item` (UX-quality findings).
- `audit.py` (1): `audit_scope` — scans BR + SI in a scope for hardcoded sys_ids and deprecated APIs.

**Resources** (4): `servicenow://instance/info`, `servicenow://health` (auth pre-flight), `servicenow://schema/{table}`, `servicenow://scope/{name}`.

**Prompts** (5): `audit_scope`, `analyze_catalog`, `upgrade_readiness_review`, `triage_incident`, `instance_health_check`.

**Safety**
- `--read-only` CLI flag / `SN_READ_ONLY=true` — refuses create / update / delete / comment / resolve before the call reaches ServiceNow.
- MCP annotations on every tool: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.

**Multi-instance**
- `SN_INSTANCES_FILE=instances.json` — per-instance overrides on top of fallback env vars.
- All tools accept optional `instance: str | None` parameter.
- `servicenow://instance/info` resource exposes the full registry in multi-instance mode.

**Packaging / OSS plumbing**
- MIT license, full PyPI metadata (authors, classifiers, urls, dev extras).
- `Dockerfile` (multi-stage, py3.12-slim, non-root UID 10001) + `.dockerignore`.
- `.pre-commit-config.yaml` (ruff format + check + mypy).
- GitHub Actions CI matrix (py 3.10 / 3.11 / 3.12 / 3.13) — lint, format check, pytest, wheel build, smoke install.

### Notes
- Stdio remains the recommended transport for local clients (Claude Desktop, Cursor, VS Code).
- OAuth 2.1 + PKCE for incoming HTTP auth is on the roadmap but not in this release; behind a proxy or with `--read-only`, HTTP is safe for trusted networks today.
