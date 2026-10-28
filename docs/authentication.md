# Authentication

Three ways to authenticate. Pick one with `SN_AUTH_METHOD`.

| Method | `SN_AUTH_METHOD` | Good for | You store |
| --- | --- | --- | --- |
| Basic | `basic` (default) | A PDI, or a quick trial | Username and password |
| OAuth client credentials | `oauth` | A service identity, e.g. a shared deployment | Client ID and secret |
| OAuth authorization code + PKCE | `oauth_authorization_code` | A real person, including SSO and MFA | A refresh token, after one browser login |

Whichever you choose, use a **dedicated ServiceNow user with the smallest role set
your work needs**, not `admin`. The server is read-only by default, but the
account's roles are still what limit what it can read. See also the
[hardening checklist](../SECURITY.md#hardening-checklist-for-operators).

## Basic

```bash
SN_INSTANCE_URL=https://your-instance.service-now.com
SN_AUTH_METHOD=basic
SN_USERNAME=mcp.reader
SN_PASSWORD=...
```

## OAuth client credentials

Register an OAuth API endpoint in the instance (*System OAuth > Application
Registry > New > Create an OAuth API endpoint for external clients*), then:

```bash
SN_AUTH_METHOD=oauth
SN_CLIENT_ID=your-client-id
SN_CLIENT_SECRET=your-client-secret
```

The server fetches and caches the token itself; there is nothing to log in to.

## Browser login (authorization code + PKCE)

Use this when you'd rather not keep a password in `.env`. You authorise once in a
browser, and the server then works from a stored refresh token. **If the instance
has SSO, `/oauth_auth.do` redirects to your IdP**, so the login goes through SSO
and MFA. Without an IdP you get the normal ServiceNow login form; you still don't
store a password, and nothing changes when an IdP is added later.

**1. Register the client in ServiceNow:** *System OAuth > Application Registry >
New > Create an OAuth API endpoint for external clients*.

| Field | Value |
| --- | --- |
| Redirect URL | `http://127.0.0.1:8765/callback`, matching `SN_OAUTH_REDIRECT_URI` exactly. List several URLs one per line if you need them |
| Public Client | Tick it if your release offers it, and leave out the client secret; PKCE protects the exchange |

**2. Configure:**

```bash
SN_AUTH_METHOD=oauth_authorization_code
SN_CLIENT_ID=your-client-id
SN_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/callback
```

Use `127.0.0.1`, not `localhost`: on macOS `localhost` can resolve to `::1`, which
the login listener never sees.

**3. Log in:**

```bash
simple-servicenow-mcp login                       # single instance
simple-servicenow-mcp login --instance acme-dev   # one entry from SN_INSTANCES_FILE
simple-servicenow-mcp auth-status                 # what is stored, and when it expires
simple-servicenow-mcp logout                      # drop the stored tokens
```

Run these **from the same directory, with the same environment, as the server**.
`.env` and a relative `SN_INSTANCES_FILE=./instances.json` resolve against the
current directory. Started from elsewhere, the command reads a different
configuration and stores the token under a different key, and the server keeps
reporting "no stored token".

**No browser on the machine**, or the admin only registered ServiceNow's own
`oauth_redirect.do`? Run `simple-servicenow-mcp login --paste` and paste the code
(or the whole redirect URL) back into the terminal.

### Where tokens live

`~/.config/simple-servicenow-mcp/tokens.json` (`0600`, in a `0700` directory),
keyed by instance host and client ID. Set `SN_TOKEN_STORE` to move it. **The
refresh token is stored in plaintext.** That's the same trust you already place
in a password sitting in `.env`, but worth knowing. Deleting the file just means
logging in again.

`auth-status` reads only this file, so it can show a refresh token the instance
has already revoked (a PDI revokes them when it restarts). If a tool call fails
with a hint to log in again, run `login` again.

## Protecting the HTTP transport

Everything above is about how the server signs in to **ServiceNow**. This section
is about the other side: who may use the server when it runs with
`--transport http`. By default, anyone who can reach the port can.

With `SN_HTTP_AUTH=oauth`, every request needs an access token from an identity
provider you already run. MCP clients find the provider through the server's
protected-resource metadata and sign the user in through the browser. The server
checks each token and **never forwards it to ServiceNow**: tool calls still use
the ServiceNow login configured above. So:

- **Every signed-in user acts as that one ServiceNow account.** Give it read-only
  roles and keep the server read-only.
- **The request log records who made each call:** `user` (the token's `sub`) and
  `username` (`preferred_username`). It never records the token.

The SSE transport has no sign-in; `SN_HTTP_AUTH=oauth` with `--transport sse`
stops the server at startup.

### Settings

| Variable | Example | Meaning |
| --- | --- | --- |
| `SN_HTTP_AUTH` | `oauth` | Turns sign-in on |
| `SN_HTTP_AUTH_ISSUER` | `https://login.example.com/oauth2/default` | Your provider's issuer. It must match the tokens' `iss` exactly, trailing `/` included. HTTPS only (`http://127.0.0.1` is allowed for local testing) |
| `SN_HTTP_AUTH_RESOURCE_URL` | `https://mcp.example.com/mcp` | The URL clients connect to. Its host is accepted automatically by the [host allow-list](configuration.md#transports) |
| `SN_HTTP_AUTH_AUDIENCE` | `api://simple-servicenow-mcp` | Accepted `aud` values, comma-separated. Defaults to the resource URL |
| `SN_HTTP_AUTH_REQUIRED_SCOPES` | `mcp.access` | Scopes every token must carry. Clients learn them from the metadata and ask for them |
| `SN_HTTP_AUTH_JWKS_URL` | | Only for a provider with no discovery document |

**Use an API identifier as the audience, never a client ID.** If the audience is
the client ID of the app that signs users in, that app's ID tokens carry the same
`aud`. The server also refuses anything that looks like an ID token (a `nonce`, or
no `scope`/`scp` claim), but the audience is the first line of defence.

### What the server checks

A token is accepted only if it:
- is at most 16 KB;
- is signed with RS, PS or ES 256/384/512 by a key the provider publishes, under the algorithm that key declares;
- has `iss` equal to the issuer, `aud` in the audience list, and `exp` and `sub` present, allowing 60 seconds of clock skew;
- carries the required scopes, read from `scope` or `scp`.

Signing keys are cached for 10 minutes. An unknown key ID causes at most one
refetch a minute, and the known keys are kept if the provider is unreachable.

| Problem | Response |
| --- | --- |
| No token, or an invalid one | `401` with `WWW-Authenticate: Bearer … resource_metadata="…"` |
| Missing required scope | `403 insufficient_scope` |

The metadata lives at `/.well-known/oauth-protected-resource/mcp`, for a resource
URL ending in `/mcp`.

### Signing in from a client

**Claude Code.** Register a **public** client (no secret) in your provider, with
redirect URI `http://localhost:<port>/callback`, then:

```bash
claude mcp add --transport http servicenow https://mcp.example.com/mcp \
  --client-id <client-id> --callback-port <port>
```

Run `/mcp` in Claude Code to sign in. Without `--client-id`, Claude Code needs a
provider that supports Dynamic Client Registration or Client ID Metadata
Documents, and fails with "does not support dynamic client registration"
otherwise.

**Other clients** follow the same discovery. Most need either a pre-registered
client or Dynamic Client Registration; see your client's documentation.

### Provider notes

**Microsoft Entra ID**
- **Issuer and audience:**
  - v2 tokens: issuer `https://login.microsoftonline.com/<tenant-id>/v2.0`; `aud` is the API app's client ID.
  - v1 tokens: issuer `https://sts.windows.net/<tenant-id>/` (note the trailing `/`); `aud` is usually the Application ID URI (`api://…`), but list both it and the client ID.
- **Which version you get** is set by the API app's `requestedAccessTokenVersion` (`2` for v2; unset or `1` for v1).
- Register the API as its own app ("Expose an API") and use its identifiers as the audience.
- `scp` is a space-separated string.
- **App-only (client-credentials) tokens** carry `roles` instead of `scp`, so the server refuses them. Sign-in here is for users.
- **No Dynamic Client Registration:** pre-register the client.

**Okta** (custom authorization server)
- Issuer `https://<your-okta-domain>/oauth2/<server-id>`, for example `/oauth2/default`.
- The default audience is `api://default`; give your API its own.
- `scp` is a JSON array, and the client ID is in `cid`.
- Use a custom server, not the org server (`https://<your-okta-domain>`), whose tokens aren't meant for your own APIs.

**Auth0**
- Issuer `https://<tenant>.auth0.com/`, **with** the trailing `/`.
- Create an API and use its identifier as the audience. Without an `audience`, Auth0 issues opaque tokens the server can't check. Set the tenant's Default Audience, or turn on the Resource Parameter Compatibility Profile (Settings → Advanced) for clients that send `resource` instead.
- `scope` is a space-separated string.
- Dynamic Client Registration is off by default; enable it under Settings → Advanced if clients need it.

**Keycloak**
- Issuer `https://<host>/realms/<realm>`.
- Access tokens don't name your API in `aud` by default. Add an **Audience** mapper to a client scope, either default or requested with `scope=`.
- `azp` is the client ID.
- From version 25, `sub` comes from the `basic` scope's Subject mapper: keep it, or tokens are refused.
