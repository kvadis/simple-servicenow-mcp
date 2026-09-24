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
