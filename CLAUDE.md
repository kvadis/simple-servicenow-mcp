# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## What This Is

**simple-servicenow-mcp** — a Python MCP server for ServiceNow developers. Exposes ServiceNow instance data to AI clients (Claude Desktop, Cursor, VS Code, Claude Code) via MCP. Focused on analytical intelligence — not just CRUD, but cross-record reasoning for developers and consultants:
- Audit scopes for anti-patterns and upgrade risks
- Analyze catalog items for UX quality
- Instance health checks and operational metrics

## Commands

```bash
pip install -e .                                       # Install in dev mode
simple-servicenow-mcp                                  # Stdio (default — for Claude Desktop, Cursor, VS Code)
simple-servicenow-mcp --transport http --port 8000     # Streamable HTTP (remote/multi-client)
simple-servicenow-mcp --transport sse                  # SSE (legacy transport)
python -m simple_servicenow_mcp.server                 # Run directly
mcp dev src/simple_servicenow_mcp/server.py            # MCP Inspector (interactive testing)
pytest                                                 # Run tests
```

## Architecture

```
src/simple_servicenow_mcp/
  __init__.py
  server.py         # FastMCP instance, lifespan, CLI entry (--transport stdio|http|sse)
  config.py         # Pydantic Settings (env-based, SN_ prefix)
  client.py         # ServiceNow REST client (httpx async), structured ServiceNowAPIError
  instances.py      # Multi-instance registry (SN_INSTANCES_FILE → named clients)
  packages.py       # SN_TOOL_PACKAGES parsing (gates which tool modules import)
  logging_setup.py  # stdlib logging + JSON formatter
  tools/
    _annotations.py # READ/CREATE/UPDATE/DELETE/APPEND annotation presets
    table.py        # Generic Table API (list, get, create, update, delete, count, search, describe, aggregate)
    incident.py     # Incident shortcuts (list, create, comment, resolve)
    script.py       # Script reading (BR, SI, CS, UP, UA, get body)
    catalog.py      # Catalog browsing (categories, items, variables, analyze)
    cmdb.py         # CMDB (list_cis, get_ci, relationships, find_by_class)
    knowledge.py    # Knowledge base (list, search, get article)
    attachment.py   # Attachments (list, metadata, download, upload, delete)
    update_set.py   # Update Sets (list, get, list_changes, summarize w/ risk flags)
    audit.py        # Scope audits
  resources.py      # MCP Resources (instance info, health, table schema, app scopes)
  prompts.py        # MCP Prompts (audit-scope, analyze-catalog, health-check, upgrade-readiness, triage)
```

Every tool accepts an optional ``instance`` parameter. In single-instance mode (the default),
it must be omitted. With ``SN_INSTANCES_FILE`` set, pass an instance name (e.g.
``instance="dev"``) to target a non-default instance from the registry.

## Key Patterns

- **FastMCP** — `@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()` decorators
- **Lifespan context** — `ServiceNowClient` created in `lifespan()`, accessed via `ctx.request_context.lifespan_context`
- **Tool errors** — raise `ToolError` from `mcp.server.fastmcp.exceptions`, never return raw exceptions
- **Config** — `pydantic-settings` with `env_prefix="SN_"`, loads `.env` automatically
- **Auth** — Basic (base64 header) or OAuth 2.0 client credentials (cached token, auto-refresh 60s before expiry)
- **Transport** — stdio (stdout reserved for JSON-RPC, all logs to stderr)

## Adding New Tools

1. Create `src/simple_servicenow_mcp/tools/my_module.py`
2. Import `mcp` from `..server` and use `@mcp.tool(annotations=_a.READ)` (or CREATE/UPDATE/DELETE/APPEND)
3. Add `instance: str | None = None` to the signature and resolve with `app.client_for(instance)` (or a local `_client(ctx, instance)` helper) so multi-instance mode works
4. Return native types (`dict`, `list[dict]`, etc.) — FastMCP auto-emits `structuredContent`
5. Wrap ServiceNow calls in `try/except` and re-raise as `ToolError`
6. Import the module in `server.py` alongside other tool imports
7. Use docstrings with `Args:` for tool/parameter descriptions

## ServiceNow API Notes

- Primary: REST Table API (`/api/now/table/{table}`)
- Stats API for counts (`/api/now/stats/{table}`)
- Encoded queries use `^` as AND (e.g. `active=true^priority=1`)
- `sysparm_display_value=true` returns human-readable values
- Always paginate with `limit` + `offset`, never dump all records

## Testing Conventions

### TDD Workflow
- Always write failing tests BEFORE implementation
- Use AAA pattern: Arrange-Act-Assert
- One assertion per test when possible
- Test names describe behavior: `should_return_empty_when_no_items`

### Test-First Rules
- When I ask for a feature, write tests first
- Tests should FAIL initially (no implementation exists)
- Only after tests are written, implement minimal code to pass
