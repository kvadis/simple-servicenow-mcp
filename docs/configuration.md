# Configuration

## Install

```bash
uvx simple-servicenow-mcp          # run without installing (what the client configs below use)
pip install simple-servicenow-mcp  # or install the `simple-servicenow-mcp` command
```

From source:

```bash
git clone https://github.com/kvadis/simple-servicenow-mcp.git
cd simple-servicenow-mcp
pip install -e ".[dev]"
```

Docker (build from the repo):

```bash
docker build -t simple-servicenow-mcp .
docker run --rm -i \
  -e SN_INSTANCE_URL=https://your-instance.service-now.com \
  -e SN_USERNAME=mcp.reader \
  -e SN_PASSWORD=... \
  simple-servicenow-mcp
```

`python -m simple_servicenow_mcp` also starts the server.

## Client setup

Every client runs the same command with the same environment variables; only the
file and its top-level key differ. Credentials here are basic auth; see
[Authentication](authentication.md) for OAuth.

**Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). **Cursor:**
`~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project.

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "uvx",
      "args": ["simple-servicenow-mcp"],
      "env": {
        "SN_INSTANCE_URL": "https://your-instance.service-now.com",
        "SN_USERNAME": "mcp.reader",
        "SN_PASSWORD": "..."
      }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add servicenow \
  -e SN_INSTANCE_URL=https://your-instance.service-now.com \
  -e SN_USERNAME=mcp.reader -e SN_PASSWORD=... \
  -- uvx simple-servicenow-mcp
```

**VS Code:** `.vscode/mcp.json` uses a top-level `servers` key, and can prompt for
the password instead of storing it:

```json
{
  "inputs": [
    { "type": "promptString", "id": "sn-password", "description": "ServiceNow password", "password": true }
  ],
  "servers": {
    "servicenow": {
      "type": "stdio",
      "command": "uvx",
      "args": ["simple-servicenow-mcp"],
      "env": {
        "SN_INSTANCE_URL": "https://your-instance.service-now.com",
        "SN_USERNAME": "mcp.reader",
        "SN_PASSWORD": "${input:sn-password}"
      }
    }
  }
}
```

To allow writes, add `"--read-write"` after `"simple-servicenow-mcp"` in `args`
(see [Read-only mode](#read-only-mode)).

## Environment variables

All settings use the `SN_` prefix and can also live in a `.env` file in the
directory the server starts from. The main ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SN_INSTANCE_URL` | none | `https://<instance>.service-now.com` |
| `SN_AUTH_METHOD` | `basic` | `basic`, `oauth` or `oauth_authorization_code` ([Authentication](authentication.md)) |
| `SN_USERNAME` / `SN_PASSWORD` | none | Basic auth |
| `SN_CLIENT_ID` / `SN_CLIENT_SECRET` | none | OAuth |
| `SN_READ_ONLY` | `true` | `false` allows writes, like `--read-write` |
| `SN_TOOL_PACKAGES` | default set | Which tool packages to load ([Tool packages](#tool-packages)) |
| `SN_INSTANCES_FILE` | none | Enables [multi-instance](#multi-instance) mode |
| `SN_API_TIMEOUT` | `30` | Request timeout, in seconds |
| `SN_LOG_LEVEL` / `SN_LOG_FORMAT` | `INFO` / `text` | Logging goes to stderr; `json` for structured logs |

[`.env.example`](../.env.example) lists every variable, including paging,
retries and token storage.

## Tool packages

A long tool list costs the model context and makes it pick worse, so tools come
in packages. Unset loads the default set (27 tools); `all` loads everything; or
list the packages you want:

```bash
SN_TOOL_PACKAGES=core,itsm              # 14 tools
SN_TOOL_PACKAGES=all
```

| Package | Tools | Default |
| --- | --- | --- |
| `audit` | 3 | ✓ |
| `core` | 9 | ✓ |
| `itsm` | 5 | ✓ |
| `scripts` | 6 | ✓ |
| `catalog` | 4 | ✓ |
| `cmdb` | 4 | |
| `knowledge` | 3 | |
| `attachment` | 5 | |
| `update_set` | 4 | |
| `all` | 43 | |

Unknown package names stop the server at startup with the list of valid ones.
Prompts and resources are always available, except the two prompts that need an
optional package ([Tool reference](tools.md#prompts)). For package sets that fit
common jobs, see [Recipes](tools.md#recipes).

## Read-only mode

**Read-only is the default.** Every tool that changes data (`create_*`,
`update_*`, `delete_*`, `add_*_comment`, `resolve_*`, `upload_*`) refuses before
the request reaches ServiceNow, so pointing the server at production can't
change anything by accident. To allow writes:

```bash
simple-servicenow-mcp --read-write
# or
SN_READ_ONLY=false simple-servicenow-mcp
```

`--read-only` overrides `SN_READ_ONLY=false` for one run. Even with writes on,
`delete_record` and `delete_attachment` return a preview unless called with
`confirm=true`.

## Multi-instance

Point one server at several instances with `SN_INSTANCES_FILE`:

```json
{
  "default": "prod",
  "instances": {
    "prod": {"instance_url": "https://acme.service-now.com", "auth_method": "oauth", "client_id": "...", "client_secret": "..."},
    "dev":  {"instance_url": "https://acmedev.service-now.com", "username": "mcp.reader", "password": "..."},
    "test": {"instance_url": "https://acmetest.service-now.com"}
  }
}
```

Every tool then takes an optional `instance` argument, such as
`list_incidents(query="active=true^priority=1", instance="dev")`. Leave it out to
use `default`. Entries inherit anything they don't set from the `SN_*`
variables, so a shared OAuth client can be defined once.
[`instances.example.json`](../instances.example.json) has a fuller example,
including a browser-login entry.

## Transports

| Transport | Use it for | Command |
| --- | --- | --- |
| stdio (default) | Local clients: Claude Desktop, Cursor, VS Code, Claude Code | `simple-servicenow-mcp` |
| Streamable HTTP | Remote or shared use, behind a reverse proxy | `simple-servicenow-mcp --transport http --host 0.0.0.0 --port 8000` |
| SSE | Older streaming clients | `simple-servicenow-mcp --transport sse` |

The HTTP transports have no authentication of their own yet. Put them behind an
authenticating proxy and keep the server read-only.
