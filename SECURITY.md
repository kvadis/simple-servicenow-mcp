# Security policy

## Supported versions

The most recent minor release is supported. Older versions may not receive security fixes.

| Version | Supported |
| --- | --- |
| 0.1.x | ✅ |

## Reporting a vulnerability

**Please don't open a public issue for security reports.**

Email **kostya.kozachuk@outlook.com** with:

- A description of the issue (what's vulnerable, how to exploit)
- The affected version (run `simple-servicenow-mcp --help` and check `pip show simple-servicenow-mcp` if unsure)
- Steps to reproduce, or a minimal proof of concept
- Your assessment of severity / impact

You should hear back within 72 hours. If the issue is confirmed:

1. A fix will be drafted privately.
2. We'll coordinate a release window with you.
3. A CVE will be requested for material vulnerabilities.
4. Credit will be given in the release notes (unless you'd prefer anonymity).

## Scope

In scope:

- Authentication bypass or credential leakage (especially around stored OAuth tokens or the multi-instance registry)
- Injection vulnerabilities in tool arguments reaching ServiceNow (encoded-query construction, multipart upload)
- Path traversal or arbitrary file read in the attachment download tool
- Denial-of-service via crafted tool inputs (e.g. infinite retry loops)
- Anything that lets a low-privilege ServiceNow user escalate via this MCP

Out of scope:

- Vulnerabilities in dependencies — please report to the upstream maintainer (e.g. `httpx`, `pydantic`, `mcp`). We'll bump promptly once a fix is released.
- Issues in ServiceNow itself — those go to [ServiceNow's PSIRT](https://www.servicenow.com/company/trust/security-advisory.html).
- Misconfiguration on the operator's side (e.g. running the MCP unauthenticated on a public network without `--read-only`). The README documents recommended hardening; please read it first.

## Hardening checklist for operators

- Use a **dedicated ServiceNow user** with the minimum role set your workflows need (don't use `admin`).
- Prefer **OAuth** over Basic auth so no password sits in env vars: authorization code + PKCE (`simple-servicenow-mcp login`) ties access to a real user; client credentials suits a service identity.
- The server is **read-only by default**. Only pass `--read-write` / `SN_READ_ONLY=false` for the specific client that needs to write, never for one shared with an agent you don't fully trust.
- For HTTP transport, put it **behind an authenticating proxy** (Cloudflare Access, oauth2-proxy, etc.). The MCP doesn't yet do incoming OAuth 2.1 PKCE.
- Store `instances.json` outside the repo, with restricted file permissions (`chmod 600`).
- Pin the version in production (`pip install simple-servicenow-mcp==0.1.0`) rather than tracking latest.
