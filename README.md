# simple-servicenow-mcp

A Python [Model Context Protocol](https://modelcontextprotocol.io) server that wraps ServiceNow's REST APIs for AI clients (Claude Desktop, Cursor, VS Code, Claude Code).

Focused on **analytical intelligence** for developers and consultants, not just CRUD — schema-aware operations, scope audits, catalog UX analysis, update-set risk review, knowledge-coverage gap detection, and cross-instance diff. The LLM can reason across records instead of paginating one table at a time.

[![CI](https://github.com/kvadis/simple-servicenow-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kvadis/simple-servicenow-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/simple-servicenow-mcp)](https://pypi.org/project/simple-servicenow-mcp/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/simple-servicenow-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-1.x-purple)](https://modelcontextprotocol.io)

---

## Table of contents

- [Highlights](#highlights)
- [Install](#install)
- [Configure](#configure)
- [Quick example](#quick-example)
- [MCP capabilities](#mcp-capabilities)
  - [Tools — overview](#tools--overview)
  - [Tools — full reference](#tools--full-reference)
  - [Resources](#resources)
  - [Prompts](#prompts)
- [Use-case workflows](#use-case-workflows)
- [Multi-instance](#multi-instance)
- [Transports](#transports)
- [Read-only mode](#read-only-mode)
- [Tool packages](#tool-packages)
- [Architecture](#architecture)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

---

## Highlights

- **43 tools** across 9 domain modules (27 enabled by default; rest opt-in via `SN_TOOL_PACKAGES`)
- **9 prompts** orchestrating multi-step analytical workflows
- **4 resources** for instance metadata, health, schema, and scope
- **Schema-aware writes** — `create_record` / `update_record` validate field names against `sys_dictionary` (walks inheritance) and surface typos locally instead of opaque `400`s
- **Structured output** — record tools return native types, so clients see typed `structuredContent` alongside text; the analysis tools (`audit_scope`, `audit_acls`, `upgrade_readiness_review`, `analyze_catalog_item`, `triage_incident`) return a JSON report as text
- **Multi-instance** — point at prod / dev / test with a single server, switch via `instance="dev"` per call
- **Three transports** — stdio (default), Streamable HTTP, SSE
- **Safety rails** — read-only by default (`--read-write` / `SN_READ_ONLY=false` to opt in); `delete_*` returns a preview unless `confirm=true`
- **Tool-package gating** — unset loads a curated 24-tool default (`core, itsm, scripts, catalog, audit`); set `SN_TOOL_PACKAGES=all` for everything or pin a custom subset (e.g. `core,itsm,cmdb`)
- **Production hygiene** — retry-after-aware retries on 429/5xx, OAuth token caching, structured JSON logging, MCP annotations on every tool

---

## Install

### From PyPI

```bash
pip install simple-servicenow-mcp
# or, no global install:
uvx simple-servicenow-mcp
```

### From source

```bash
git clone https://github.com/kvadis/simple-servicenow-mcp.git
cd simple-servicenow-mcp
pip install -e ".[dev]"
```

### Docker

```bash
docker build -t simple-servicenow-mcp .
docker run --rm -i \
  -e SN_INSTANCE_URL=https://your.service-now.com \
  -e SN_USERNAME=admin \
  -e SN_PASSWORD=... \
  simple-servicenow-mcp
```

---

## Configure

Set environment variables (or copy `.env.example` to `.env`):

```bash
SN_INSTANCE_URL=https://your-instance.service-now.com
SN_AUTH_METHOD=basic           # "basic" | "oauth" | "oauth_authorization_code"
SN_USERNAME=admin
SN_PASSWORD=your-password

# OAuth client credentials — a service identity
# SN_AUTH_METHOD=oauth
# SN_CLIENT_ID=your-client-id
# SN_CLIENT_SECRET=your-client-secret

# OAuth authorization code + PKCE — browser login, no stored password
# SN_AUTH_METHOD=oauth_authorization_code
# SN_CLIENT_ID=your-client-id
# SN_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/callback
```

See [`.env.example`](.env.example) for the full set: pagination, retries, logging, multi-instance, read-only, tool packages.

### Browser login (`oauth_authorization_code`)

Use this when you would rather not keep a password in `.env`. You authorise once in a
browser; the server then works from a stored refresh token. **If the instance has SSO
configured, `/oauth_auth.do` redirects to your IdP** — so the login goes through SSO and
MFA. On an instance with no IdP attached you simply get the ServiceNow login form; the
benefit of not storing a password still holds, and no code changes when an IdP arrives.

**1. Register the client in ServiceNow** — *System OAuth > Application Registry > New >
"Create an OAuth API endpoint for external clients"*:

| Field | Value |
| --- | --- |
| Redirect URL | `http://127.0.0.1:8765/callback` — must match exactly. Multiple URLs can be listed one per line |
| Public Client | tick it if your release offers it, and skip the client secret entirely (PKCE secures the exchange) |

**2. Configure and log in:**

```bash
simple-servicenow-mcp login                       # single-instance
simple-servicenow-mcp login --instance acme-dev   # multi-instance
simple-servicenow-mcp auth-status                 # what is stored, and when it expires
simple-servicenow-mcp logout                      # drop the stored tokens
```

Run these **from the same directory, with the same environment, as the server**. `.env`
and a relative `SN_INSTANCES_FILE=./instances.json` resolve against the current
directory. Started from elsewhere, the CLI reads a different configuration and writes the
token under a different key — and the server keeps reporting "no stored token".

No browser on the box, or the admin would only register ServiceNow's own
`oauth_redirect.do`? Use `simple-servicenow-mcp login --paste` and paste the code (or the whole
redirect URL) back into the terminal.

Tokens live in `~/.config/simple-servicenow-mcp/tokens.json` (`0600`, in a `0700`
directory), keyed by instance host + client id. **The refresh token is stored in
plaintext** — the same trust you already place in a password sitting in `.env`, but worth
knowing. Deleting the file simply means logging in again.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "uvx",
      "args": ["simple-servicenow-mcp"],
      "env": {
        "SN_INSTANCE_URL": "https://your-instance.service-now.com",
        "SN_USERNAME": "admin",
        "SN_PASSWORD": "your-password"
      }
    }
  }
}
```

The server starts read-only. To let the client create, update or delete records, add
`"--read-write"` to the args (or `SN_READ_ONLY=false` to `env`).

### Cursor / VS Code / Claude Code

The same `command + args + env` shape works in any MCP client. Point at the `simple-servicenow-mcp` executable on `PATH`, or use `uvx` / `python -m simple_servicenow_mcp.server`.

---

## Quick example

```
User: How many P1 incidents are open right now, and who owns the oldest one?

Claude: [calls count_records("incident", query="active=true^priority=1") → 7]
        [calls list_incidents(query="active=true^priority=1^ORDERBYsys_created_on", limit=1)]

There are 7 open P1 incidents. The oldest is INC0010047 ("Database cluster
unreachable from web tier"), opened 2026-05-12 by net-ops, currently assigned
to @s.taylor in the Platform-Engineering group.
```

---

## MCP capabilities

### Tools — overview

| Module | Count | Purpose |
| --- | --- | --- |
| [`table`](#tools--full-reference) | 9 | Generic CRUD + search + describe + aggregate over any ServiceNow table |
| [`incident`](#tools--full-reference) | 4 | Incident shortcuts (list, create, comment, resolve) |
| [`script`](#tools--full-reference) | 6 | Business rules, script includes, client scripts, UI policies/actions |
| [`catalog`](#tools--full-reference) | 4 | Service catalog browsing + UX analysis |
| [`cmdb`](#tools--full-reference) | 4 | Configuration Items, relationships, class filters |
| [`knowledge`](#tools--full-reference) | 3 | KB browsing + keyword search |
| [`attachment`](#tools--full-reference) | 5 | List, metadata, download (text/base64), upload, delete |
| [`update_set`](#tools--full-reference) | 4 | Update set + change browsing + risk-aware summary |
| [`audit`](#tools--full-reference) | 1 | Scope audits |
| **Total** | **40** | |

### Tools — full reference

Every tool accepts an optional `instance: str | None = None` parameter (selects a named entry from `SN_INSTANCES_FILE`; omit for the default). Mutating tools (`create_*`, `update_*`, `delete_*`, `add_*_comment`, `resolve_*`, `upload_*`) refuse with a clear error in `--read-only` mode.

#### `table` (Generic Table API)

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_records` | `table`, `query?`, `fields?`, `limit=20`, `offset=0`, `display_value="false"` | read | `list[dict]` |
| `get_record` | `table`, `sys_id`, `fields?` | read | `dict` |
| `create_record` | `table`, `data`, `skip_validation=False` | create | `dict` |
| `update_record` | `table`, `sys_id`, `data`, `skip_validation=False` | update | `dict` |
| `delete_record` | `table`, `sys_id`, `confirm=False` | destructive | `dict` (preview unless confirmed) |
| `search_records` | `table`, `keyword`, `search_fields="short_description,description,name,number"`, `result_fields?`, `limit=20`, `offset=0` | read | `list[dict]` |
| `describe_table` | `table` | read | `list[dict]` (sys_dictionary entries) |
| `count_records` | `table`, `query?` | read | `{table, query, count}` |
| `aggregate_records` | `table`, `query?`, `sum_fields?`, `avg_fields?`, `min_fields?`, `max_fields?`, `group_by?`, `having?`, `count=True`, `display_value="false"` | read | `{table, query, group_by, result}` |

> `create_record` / `update_record` validate field names against `sys_dictionary` (walking `super_class` inheritance) and surface typos locally with a hint to use `describe_table`. Pass `skip_validation=true` to bypass for non-OOTB fields. Validation fails open if `sys_dictionary` isn't readable.

#### `incident`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_incidents` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `create_incident` | `short_description`, `description?`, `urgency?`, `impact?`, `category?`, `caller_id?`, `assignment_group?` | create | `dict` |
| `add_incident_comment` | `sys_id`, `comment`, `comment_type="work_notes"|"comments"` | append | `dict` |
| `resolve_incident` | `sys_id`, `close_code`, `close_notes`, `close_state="6"` | update | `dict` |

#### `script`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_business_rules` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_script_includes` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_client_scripts` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_ui_policies` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_ui_actions` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `get_script_body` | `table`, `sys_id` | read | `dict` |

#### `catalog`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_catalog_categories` | `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `list_catalog_items` | `category?`, `query?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `get_catalog_item_variables` | `item_sys_id` | read | `list[dict]` |
| `analyze_catalog_item` | `item_sys_id` | read | JSON with `findings[]` (missing short_description, no variables, mandatory overload) |

#### `cmdb`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_cis` | `query?`, `ci_class?`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `get_ci` | `sys_id` | read | `dict` |
| `list_ci_relationships` | `ci_sys_id`, `direction="both"|"outgoing"|"incoming"`, `limit=50` | read | `{ci_sys_id, direction, outgoing[], incoming[], total}` |
| `find_cis_by_class` | `ci_class`, `query?`, `limit=20`, `offset=0` | read | `list[dict]` |

#### `knowledge`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_knowledge_articles` | `query?`, `knowledge_base?`, `state="published"`, `limit=20`, `offset=0`, `verbose=False` | read | `list[dict]` |
| `search_knowledge` | `keyword`, `limit=10`, `offset=0`, `state="published"` | read | `list[dict]` |
| `get_knowledge_article` | `sys_id` | read | `dict` |

#### `attachment`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_attachments` | `table`, `sys_id` | read | `list[dict]` |
| `get_attachment_metadata` | `attachment_sys_id` | read | `dict` |
| `download_attachment` | `attachment_sys_id`, `max_bytes=1000000`, `force_base64=False` | read | `{sys_id, content_type, size_bytes, encoding, content}` |
| `upload_attachment` | `table`, `sys_id`, `file_name`, `content`, `content_type="text/plain"`, `encoding="text"|"base64"` | create | `dict` |
| `delete_attachment` | `attachment_sys_id`, `confirm=False` | destructive | `dict` (preview unless confirmed) |

> `download_attachment` text-decodes `text/*`, `application/json`, `application/xml`, `application/yaml`, `application/javascript`, `application/x-sh`, and `*+json/*+xml/*+yaml` mimes. Other content is returned as base64. Bytes that fail UTF-8 decoding fall back to base64 even on text mimes.

#### `update_set`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `list_update_sets` | `state="in progress"`, `application?`, `query?`, `limit=20`, `offset=0` | read | `list[dict]` |
| `get_update_set` | `sys_id` | read | `{update_set, change_count}` |
| `list_update_set_changes` | `update_set_sys_id`, `query?`, `limit=50`, `offset=0`, `verbose=False` | read | `list[dict]` (XML payload excluded unless verbose) |
| `summarize_update_set` | `update_set_sys_id` | read | `{update_set, total_changes, by_type, by_action, deletes[], high_risk_changes[], truncated}` |

> `summarize_update_set` flags changes against high-risk tables: `sys_security_acl`, `sys_user_role`, `sys_user_has_role`, `sys_script`, `sys_script_include`, `sys_db_object`, `sys_dictionary`.

#### `audit`

| Tool | Args | Annotation | Returns |
| --- | --- | --- | --- |
| `audit_scope` | `scope` | read | Scope audit findings (hardcoded sys_ids, deprecated APIs in scripts) |
| `audit_acls` | `scope` | read | Tables in a scope with no ACLs of their own — `blocking` when nothing protects them, `warning` when a parent table's ACLs apply |
| `upgrade_readiness_review` | `scope`, `target_version?` | read | Severity-graded scan (blocking / risk / info) of business rules, script includes, client scripts, scripted UI policies and UI actions, with line numbers and a red/yellow/green verdict |

---

### Resources

Resources are read-only documents the LLM can pull in for context. All return JSON strings.

| URI | Description |
| --- | --- |
| `servicenow://instance/info` | Configured instance(s). Single-instance: `{mode, url, auth_method}`. Multi-instance: `{mode, default, instances: {name → {url, auth_method}}}`. No secrets. |
| `servicenow://health` | Auth pre-flight — a 1-row `sys_user` fetch per instance. Single-instance: `{status: "ok", ...}` or `{status: "error", code, message, detail, token?}`. Multi-instance: `{status, default, instances: {name: {...}}}`. Never raises — safe to call before workflows. |
| `servicenow://schema/{table_name}` | `{table, truncated, fields: [...]}` — the table's own `sys_dictionary` entries (element, column_label, internal_type, max_length, mandatory, reference, default_value, active), paged. Use the `describe_table` tool for inherited fields. `{error}` on failure. |
| `servicenow://scope/{scope_name}` | App scope metadata from `sys_scope` (sys_id, scope, name, short_description, version, vendor, active). |

---

### Prompts

Prompts orchestrate multi-step analytical workflows. They emit a structured user message that the LLM follows step-by-step using the tools above.

| Prompt | Args | What it does |
| --- | --- | --- |
| `audit_scope` | `scope` | Pulls business rules + script includes in a scope, flags issues, summarises findings |
| `analyze_catalog` | `category?` | Catalog UX audit — missing descriptions, mandatory-field overload, dangling variables |
| `upgrade_readiness_review` | `scope`, `target_version` | Audits BR/SI/CS/UP/UA across a scope, classifies findings 🔴🟠🟡, emits migration checklist |
| `triage_incident` | `number` | Linear-style triage with explicit action enum: `request-info` / `reassign` / `escalate` / `propose-resolution` |
| `update_set_review` | `update_set` | Risk-aware audit before promotion: summary → high-risk drill-down → promotion checklist. Registered only when the `update_set` package is loaded |
| `trace_incident_impact` | `number` | End-to-end blast radius: incident → caller's CIs → relationships → recent changes → related KB. Registered only when `cmdb`, `knowledge` and `attachment` are loaded |
| `cross_instance_diff` | `table`, `query`, `instance_a`, `instance_b` | Compare same-query records across two configured instances — drift, promotion candidates, reverse-drift |
| `knowledge_coverage_report` | `time_window_days=90` | Aggregate incidents by category vs published KB count, flag gaps, propose the highest-ROI articles to write |
| `instance_health_check` | — | Active incidents, P1 count, unassigned, in-flight changes, version |

---

## Use-case workflows

**1. Help-desk triage**

`SN_TOOL_PACKAGES=core,itsm,knowledge` (16 tools) + prompts: `triage_incident`, `instance_health_check`.

**2. Pre-promotion update-set audit**

`SN_TOOL_PACKAGES=core,scripts,update_set,audit` (22 tools) + prompts: `update_set_review`, `audit_scope`, `upgrade_readiness_review`.

**3. SRE blast-radius analysis**

`SN_TOOL_PACKAGES=core,itsm,cmdb,knowledge,attachment` (26 tools) + prompts: `trace_incident_impact`, `triage_incident`.

**4. Catalog UX review**

`SN_TOOL_PACKAGES=core,catalog` (13 tools) + prompts: `analyze_catalog`.

**5. Multi-environment drift detection**

`SN_TOOL_PACKAGES=core,scripts,update_set` (19 tools) + `SN_INSTANCES_FILE=instances.json` + prompts: `cross_instance_diff`.

**6. Knowledge gap reporting**

`SN_TOOL_PACKAGES=core,itsm,knowledge` (16 tools) + prompts: `knowledge_coverage_report`.

---

## Multi-instance

Point one server at multiple ServiceNow instances by setting `SN_INSTANCES_FILE`:

```json
{
  "default": "prod",
  "instances": {
    "prod": {"instance_url": "https://acme.service-now.com",     "auth_method": "oauth", "client_id": "...", "client_secret": "..."},
    "dev":  {"instance_url": "https://acmedev.service-now.com",  "username": "admin", "password": "..."},
    "test": {"instance_url": "https://acmetest.service-now.com"}
  }
}
```

Every tool then accepts an optional `instance` parameter:

```text
list_incidents(query="active=true^priority=1", instance="dev")
```

Omit it to use the registered `default`. Per-instance entries inherit unset fields from the top-level `SN_*` env vars, so a shared OAuth client can be defined once in `.env`.

---

## Transports

| Transport | Use case | Command |
| --- | --- | --- |
| stdio (default) | Local clients (Claude Desktop, Cursor, VS Code) | `simple-servicenow-mcp` |
| Streamable HTTP | Remote / multi-client / behind a reverse proxy | `simple-servicenow-mcp --transport http --host 0.0.0.0 --port 8000` |
| SSE | Legacy streaming clients | `simple-servicenow-mcp --transport sse` |

For HTTP deployments behind a proxy, prefer authenticated proxy + `--read-only` until OAuth 2.1 PKCE for incoming auth lands (tracked on roadmap).

---

## Tool packages

A 40-tool list is overkill for narrow use cases — a help-desk assistant doesn't need CMDB or update-set tools cluttering the LLM's prompt. Pin a subset via `SN_TOOL_PACKAGES`:

```bash
SN_TOOL_PACKAGES=core,itsm                          # 13 tools
SN_TOOL_PACKAGES=core,cmdb,knowledge                # 19 tools
SN_TOOL_PACKAGES=core,scripts,audit,update_set      # 20 tools
```

| Package | Tools | Module |
| --- | --- | --- |
| `core` | 9 | `tools/table.py` (Generic Table API) |
| `itsm` | 4 | `tools/incident.py` |
| `scripts` | 6 | `tools/script.py` |
| `catalog` | 4 | `tools/catalog.py` |
| `cmdb` | 4 | `tools/cmdb.py` |
| `knowledge` | 3 | `tools/knowledge.py` |
| `attachment` | 5 | `tools/attachment.py` |
| `update_set` | 4 | `tools/update_set.py` |
| `audit` | 1 | `tools/audit.py` |
| `all` | 40 | sentinel — same as unset |

Unknown package names fail at startup with the valid list. Prompts and resources are always registered.

---

## Read-only mode

**Read-only is the default.** Every `create_*` / `update_*` / `delete_*` / `add_*_comment` /
`resolve_*` / `upload_*` tool refuses until you opt into writes, so pointing the server at a
production instance cannot change anything by accident. To allow mutations:

```bash
simple-servicenow-mcp --read-write
# or
SN_READ_ONLY=false simple-servicenow-mcp
```

`--read-only` still exists and overrides `SN_READ_ONLY=false` for one run.

Any `create_*` / `update_*` / `delete_*` / `add_*_comment` / `resolve_*` / `upload_*` tool will refuse with a clear `ToolError` before the request reaches ServiceNow. Reads keep working unchanged. Use this when sharing an MCP with an AI agent that you don't fully trust to write.

---

## Architecture

```
src/simple_servicenow_mcp/
  server.py         FastMCP instance, lifespan, CLI (--transport, --read-only)
  config.py         Pydantic Settings — env-prefix SN_
  client.py         Async httpx client, structured ServiceNowAPIError, OAuth + retries
  instances.py      Multi-instance registry
  packages.py       SN_TOOL_PACKAGES parsing (gates tool-module imports)
  logging_setup.py  stdlib logging + optional JSON formatter
  tools/            40 MCP tools in 9 domain modules
  resources.py      4 MCP resources
  prompts.py        9 MCP prompts
```

**Request flow** (read):

```
LLM ─▶ tool(instance="dev")
       │
       ├─ AppContext.client_for("dev")              ← multi-instance resolver
       │   └─ raises ToolError if "dev" not in SN_INSTANCES_FILE
       │
       └─ client.list_records(...)                  ← httpx + retries + OAuth
           └─ on error → raises ServiceNowAPIError  ← per-status hint, retryable flag
```

**Request flow** (write):

```
LLM ─▶ create_record(instance="dev")
       │
       ├─ AppContext.ensure_writable("create_record")    ← --read-only gate
       ├─ AppContext.client_for("dev")
       ├─ _validate_field_names(client, table, data)     ← walks sys_db_object.super_class
       └─ client.create_record(...)
```

**Key design choices**

- **stdlib logging** over `structlog` — one less dependency, JSON formatter is 30 lines.
- **No mocking framework** — tests use an in-process `FakeServiceNowClient` with recorded calls.
- **Validation is fail-open** — if `sys_dictionary` access is denied, the request goes through unvalidated rather than blocking workflows.
- **MCP tool annotations** on every tool: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. Clients (e.g. Claude Desktop) use these to render UI hints and gate auto-approval.

---

## Development

```bash
pip install -e ".[dev]"
pre-commit install                       # ruff format + check + mypy on commit
pytest                                   # owned tests (no network required)
ruff check . && ruff format --check .    # what CI enforces
mcp dev src/simple_servicenow_mcp/server.py   # MCP Inspector — interactive tool testing
```

Tests use an in-process `FakeServiceNowClient` (`tests/conftest.py`) — no real instance needed.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution workflow, [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community standards, and [`SECURITY.md`](SECURITY.md) for vulnerability disclosure.

---

## Roadmap

- **OAuth 2.1 + PKCE** for incoming HTTP auth (currently stdio is the recommended secure transport)
- **MID Server support** — alternative path for on-prem instances without inbound REST
- **Local-script Git sync** — pull `sys_script*` records to a local Git repo for diff/blame
- **Sentry-style instrumentation** option (tracing spans per tool call)
- **More prompts** — `release_postmortem`, `acl_audit`, `flow_designer_review`

Open an issue for requests.

---

## License

[MIT](LICENSE) © 2026 Kostya Kozachuk

---

## Acknowledgements

Built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and the [Model Context Protocol](https://modelcontextprotocol.io) spec.
