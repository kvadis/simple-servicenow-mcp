# simple-servicenow-mcp

An MCP server that lets Claude, Cursor or any MCP client **review** a ServiceNow
instance: upgrade readiness, missing ACLs, risky scripts and update sets. It is
read-only by default, and it can also read and write records like any other
ServiceNow MCP.

[![CI](https://github.com/kvadis/simple-servicenow-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kvadis/simple-servicenow-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/simple-servicenow-mcp)](https://pypi.org/project/simple-servicenow-mcp/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/simple-servicenow-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/kvadis/simple-servicenow-mcp/blob/main/LICENSE)

## What it finds

Run against ServiceNow's own `sn_publications` plugin, as shipped on a developer
instance, `upgrade_readiness_review` returns:

| Severity | Finding | Where |
| --- | --- | --- |
| 🔴 blocking | DOM access: `document.URL.parseQuery()` | Client script *Hide Accounts Type*, line 2 |
| 🟠 risk | `current.update()` inside a business rule | Business rule *Update State for File Upload*, line 5 |
| 🟠 risk | Hardcoded sys_id | Script include *RecipientsListApi*, line 168 |

**Verdict: red**, 40 scripts scanned. Each finding also carries the evidence and
a recommended fix. `audit_acls` does the same for tables that no ACL protects,
and `summarize_update_set` flags deletes and changes to security-sensitive tables
before you promote.

## Quick start

1. No instance to try it on? Get a free
   [Personal Developer Instance](https://developer.servicenow.com).
2. Add the server to your MCP client. For Claude Desktop or Cursor:

   ```json
   {
     "mcpServers": {
       "servicenow": {
         "command": "uvx",
         "args": ["simple-servicenow-mcp"],
         "env": {
           "SN_INSTANCE_URL": "https://your-instance.service-now.com",
           "SN_USERNAME": "your-user",
           "SN_PASSWORD": "your-password"
         }
       }
     }
   }
   ```

   For Claude Code:

   ```bash
   claude mcp add servicenow \
     -e SN_INSTANCE_URL=https://your-instance.service-now.com \
     -e SN_USERNAME=your-user -e SN_PASSWORD=your-password \
     -- uvx simple-servicenow-mcp
   ```

3. Restart the client and ask a question.

The server starts **read-only**; add `--read-write` to `args` to let it change
records. Beyond a PDI, use a dedicated low-privilege user or OAuth browser login
(SSO and MFA work): see
[Authentication](https://github.com/kvadis/simple-servicenow-mcp/blob/main/docs/authentication.md).
VS Code, Docker and other setups are in
[Configuration](https://github.com/kvadis/simple-servicenow-mcp/blob/main/docs/configuration.md).

## Ask things like

- *"Is the x_acme_hr scope ready for the next upgrade? What would block it?"*
- *"Which tables in x_acme_hr have no ACLs of their own?"*
- *"Review the 'Sprint 42' update set before we promote it."*
- *"Triage INC0010047 and suggest the next step."*
- *"Audit the Hardware catalog items for UX problems."*

## What's included

| Package | Covers | Loaded by default |
| --- | --- | --- |
| `audit` | Upgrade readiness, missing ACLs, scope audits | ✓ |
| `core` | Any table: read, search, count, aggregate, describe schema, write | ✓ |
| `itsm` | Incidents, including a one-call triage bundle | ✓ |
| `scripts` | Business rules, script includes, client scripts, UI policies and actions | ✓ |
| `catalog` | Catalog items, variables, UX analysis | ✓ |
| `cmdb` | Configuration items and relationships | |
| `knowledge` | Knowledge articles | |
| `attachment` | Attachments on any record | |
| `update_set` | Update sets, their changes, and a risk summary | |

Set `SN_TOOL_PACKAGES=all` for everything. There are also **prompts** for
multi-step reviews (upgrade readiness, update-set promotion, incident blast
radius, cross-instance drift, knowledge gaps) and **resources** for instance
info, health, table schema and app scope. Details are in the
[Tool reference](https://github.com/kvadis/simple-servicenow-mcp/blob/main/docs/tools.md).

## Safety

- **Read-only by default.** Tools that change data refuse until you start the
  server with `--read-write`.
- **Deletes need confirmation.** Without `confirm=true`, `delete_record` and
  `delete_attachment` only return a preview.
- **Writes are checked.** Field names are validated against the table's schema,
  including inherited fields, before anything is sent.
- **Every tool is annotated** as read, write or destructive, so clients can ask
  before running the risky ones.

## Documentation

- [Configuration](https://github.com/kvadis/simple-servicenow-mcp/blob/main/docs/configuration.md): install, client setup, environment variables, tool packages, multiple instances, transports
- [Authentication](https://github.com/kvadis/simple-servicenow-mcp/blob/main/docs/authentication.md): basic, OAuth client credentials, browser login with SSO
- [Tool reference](https://github.com/kvadis/simple-servicenow-mcp/blob/main/docs/tools.md): every tool, prompt and resource
- [Contributing](https://github.com/kvadis/simple-servicenow-mcp/blob/main/CONTRIBUTING.md) · [Security policy](https://github.com/kvadis/simple-servicenow-mcp/blob/main/SECURITY.md) · [Changelog](https://github.com/kvadis/simple-servicenow-mcp/blob/main/CHANGELOG.md)

## License

[MIT](https://github.com/kvadis/simple-servicenow-mcp/blob/main/LICENSE). Built on
the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
