"""OAuth 2.0 authorization-code + PKCE primitives.

Pure, side-effect-free helpers for the front-channel half of the flow: generate a
verifier, derive its S256 challenge, mint and compare a CSRF ``state``, and build
the ServiceNow authorize URL. The back-channel half (token exchange and refresh)
lives on the client, which already owns HTTP, retries and error mapping.

Stdlib only — ``secrets``, ``hashlib``, ``base64``, ``urllib`` — so adding
browser-delegated login costs the project no new dependency.

The verifier is the secret and never leaves this process: only ``code_challenge``
travels in the URL, and only ``code_verifier`` goes back over the (TLS) back
channel at exchange time. That is the property PKCE buys — an attacker who
intercepts the redirect gets a code they cannot spend.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

#: RFC 7636 allows 43-128 chars; 32 random bytes encodes to exactly 43.
_VERIFIER_BYTES = 32


def _b64url(raw: bytes) -> str:
    """base64url without padding, per RFC 7636 §4.2."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_verifier() -> str:
    """A fresh ``code_verifier``.

    43 characters drawn from the unreserved set by construction, so it needs no
    escaping and cannot be mangled in transit.
    """
    return _b64url(secrets.token_bytes(_VERIFIER_BYTES))


def challenge_s256(verifier: str) -> str:
    """Derive the ``code_challenge`` for ``verifier`` using S256.

    Known-answer checked against RFC 7636 Appendix B in the test suite.
    """
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state() -> str:
    """A fresh CSRF ``state`` value for the authorization request."""
    return secrets.token_urlsafe(24)


def verify_state(expected: str, received: str | None) -> bool:
    """Constant-time ``state`` comparison. Absent or mismatched both fail."""
    if not received:
        return False
    return secrets.compare_digest(expected, received)


def build_authorize_url(
    *,
    instance_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str = "",
) -> str:
    """Build the ServiceNow ``/oauth_auth.do`` URL that opens in the browser.

    ``redirect_uri`` must match the Application Registry entry byte for byte, and
    the same string has to be echoed back at token-exchange time — ServiceNow
    checks it twice.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    return f"{instance_url.rstrip('/')}/oauth_auth.do?{urlencode(params)}"


# ── receiving the redirect ──────────────────────────────────────────


@dataclass
class CallbackResult:
    """What came back on the redirect: a code, or an error, plus the state."""

    code: str | None = None
    state: str | None = None
    error: str | None = None


_SUCCESS_HTML = b"""<!doctype html><meta charset="utf-8">
<title>Authentication complete</title>
<body style="font-family:system-ui;padding:3rem;max-width:34rem">
<h2>Authentication complete</h2>
<p>simple-servicenow-mcp has the token. You can close this tab and return to the terminal.</p>
</body>"""

_FAILURE_HTML = b"""<!doctype html><meta charset="utf-8">
<title>Authentication failed</title>
<body style="font-family:system-ui;padding:3rem;max-width:34rem">
<h2>Authentication failed</h2>
<p>ServiceNow returned an error instead of an authorization code. The terminal has the detail.</p>
</body>"""


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def parse_redirect_input(raw: str) -> CallbackResult:
    """Parse what a user pasted: a full redirect URL, or a bare code.

    A bare code carries no ``state``, so CSRF cannot be checked in that case —
    the caller decides whether to accept that.
    """
    raw = raw.strip()
    if not raw:
        return CallbackResult()
    if "?" in raw or raw.lower().startswith("http"):
        query = parse_qs(urlsplit(raw).query)
        return CallbackResult(
            code=_first(query, "code"),
            state=_first(query, "state"),
            error=_first(query, "error") or _first(query, "error_description"),
        )
    return CallbackResult(code=raw)


def bind_redirect_listener(redirect_uri: str) -> RedirectListener:
    """Bind the loopback port named by ``redirect_uri``.

    Bind first, then open the browser: the redirect cannot then beat the listener
    to the port. Raises RuntimeError if the port is unavailable — it is pinned by
    the URI registered in ServiceNow, so it cannot be reassigned on the fly.

    Requests for other paths (browsers like to ask for /favicon.ico) are answered
    404 and do not consume the wait — the listener keeps going until the real
    callback arrives or the clock runs out.
    """
    parts = urlsplit(redirect_uri)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    expected_path = parts.path or "/"
    holder: dict[str, CallbackResult] = {}

    class Handler(BaseHTTPRequestHandler):
        # Name is fixed by BaseHTTPRequestHandler's dispatch.
        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path != expected_path:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            query = parse_qs(url.query)
            result = CallbackResult(
                code=_first(query, "code"),
                state=_first(query, "state"),
                error=_first(query, "error") or _first(query, "error_description"),
            )
            holder["result"] = result
            body = _SUCCESS_HTML if result.code else _FAILURE_HTML
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            # BaseHTTPRequestHandler logs to stderr by default; stay quiet so the
            # CLI's own output is the only thing the user reads.
            return

    try:
        httpd = HTTPServer((host, port), Handler)
    except OSError as e:
        raise RuntimeError(
            f"Could not bind {host}:{port} to receive the OAuth redirect ({e}). "
            "The port is fixed by the redirect URI registered in ServiceNow, so it "
            "cannot simply be changed here — free the port, or re-run with --paste."
        ) from e

    return RedirectListener(httpd, holder)


class RedirectListener:
    """A bound loopback listener, waiting for the OAuth redirect.

    Binding is separated from waiting so the caller can bind *before* opening the
    browser. With consent already granted and a live session, ServiceNow redirects
    immediately — fast enough to beat a listener that only binds afterwards.
    """

    def __init__(self, httpd: HTTPServer, holder: dict[str, CallbackResult]) -> None:
        self._httpd = httpd
        self._holder = holder

    def wait(self, timeout: float = 300.0) -> CallbackResult | None:
        """Serve until the redirect arrives. None if it never does."""
        deadline = time.monotonic() + timeout
        while "result" not in self._holder:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._httpd.timeout = remaining
            self._httpd.handle_request()
        return self._holder["result"]

    def close(self) -> None:
        self._httpd.server_close()

    def __enter__(self) -> RedirectListener:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def receive_code(redirect_uri: str, *, timeout: float = 300.0) -> CallbackResult | None:
    """Bind, wait for one redirect, and tear down. Convenience wrapper."""
    with bind_redirect_listener(redirect_uri) as listener:
        return listener.wait(timeout)
