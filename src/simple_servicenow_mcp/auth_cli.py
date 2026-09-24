"""``login`` / ``logout`` / ``auth-status`` subcommands.

An MCP server launched over stdio has no way to talk to a human: stdout is the
JSON-RPC stream and there is no TTY. Authorization therefore happens in this
separate, human-run process, which writes a token the server later picks up.

Run it from the same directory, with the same environment, the server uses::

    simple-servicenow-mcp login --instance acme-dev

``.env`` and a relative ``SN_INSTANCES_FILE=./instances.json`` resolve against
the current directory. Invoked from elsewhere the CLI would read a different
configuration and write the token under a different key, and the server would
still report "no stored token".
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from datetime import datetime, timezone

from .client import ServiceNowAPIError, ServiceNowClient
from .config import Settings
from .instances import load_instance_settings
from .oauth import (
    CallbackResult,
    bind_redirect_listener,
    build_authorize_url,
    challenge_s256,
    new_state,
    new_verifier,
    parse_redirect_input,
    verify_state,
)
from .token_store import StoredToken, TokenStore, token_key

SUBCOMMANDS = ("login", "logout", "auth-status")


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _resolve(instance: str | None) -> tuple[str, Settings]:
    """Resolve (instance_name, settings) exactly the way the server does."""
    settings = Settings()
    if settings.instances_file:
        by_name, default_name = load_instance_settings(settings.instances_file, settings)
        name = instance or default_name
        if name not in by_name:
            raise KeyError(f"Unknown instance '{name}'. Available: {sorted(by_name)}")
        return name, by_name[name]
    if instance:
        raise KeyError(
            f"--instance {instance} was given but SN_INSTANCES_FILE is not set, so there "
            "is only one (unnamed) instance configured"
        )
    return "", settings


def _describe(name: str, settings: Settings) -> str:
    return f"{name or settings.instance_url}"


def _store_for(settings: Settings) -> TokenStore:
    return TokenStore(settings.token_store)


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ── login ───────────────────────────────────────────────────────────


async def _exchange(name: str, settings: Settings, code: str, verifier: str) -> StoredToken:
    client = ServiceNowClient(settings, instance_name=name)
    try:
        return await client.exchange_authorization_code(
            code=code,
            code_verifier=verifier,
            redirect_uri=settings.oauth_redirect_uri,
        )
    finally:
        await client.close()


def cmd_login(args: argparse.Namespace) -> int:
    try:
        name, settings = _resolve(args.instance)
    except (KeyError, ValueError) as e:
        return _err(str(e))

    if settings.auth_method != "oauth_authorization_code":
        return _err(
            f"instance '{_describe(name, settings)}' uses auth_method="
            f"'{settings.auth_method}'. `login` only applies to "
            "'oauth_authorization_code'."
        )
    if not settings.client_id:
        return _err("no client_id configured — set SN_CLIENT_ID or the per-instance client_id")
    if not settings.instance_url:
        return _err("no instance_url configured — set SN_INSTANCE_URL")

    verifier = new_verifier()
    state = new_state()
    url = build_authorize_url(
        instance_url=settings.instance_url,
        client_id=settings.client_id,
        redirect_uri=settings.oauth_redirect_uri,
        state=state,
        code_challenge=challenge_s256(verifier),
        scope=settings.oauth_scope,
    )

    print(f"Instance : {settings.instance_url}", file=sys.stderr)
    if name:
        print(f"Name     : {name}", file=sys.stderr)
    print(f"Redirect : {settings.oauth_redirect_uri}", file=sys.stderr)
    print(f"\nOpen this URL to authorise:\n\n{url}\n", file=sys.stderr)

    result: CallbackResult | None
    if args.paste:
        raw = input("Paste the code (or the full redirect URL) here: ")
        result = parse_redirect_input(raw)
        # A bare code carries no state; only enforce when the user pasted a URL.
        if result.state is not None and not verify_state(state, result.state):
            return _err("state mismatch — the response did not come from this login attempt")
    else:
        try:
            # Bind first: with consent already granted, ServiceNow can redirect
            # fast enough to beat a listener that only starts afterwards.
            listener = bind_redirect_listener(settings.oauth_redirect_uri)
        except RuntimeError as e:
            return _err(str(e))
        with listener:
            print(f"Waiting for the redirect (up to {args.timeout:g}s)…", file=sys.stderr)
            if not webbrowser.open(url):
                print(
                    "Could not open a browser automatically — open the URL above by hand.",
                    file=sys.stderr,
                )
            result = listener.wait(args.timeout)
        if result is None:
            return _err(
                f"timed out after {args.timeout:g}s with no redirect. If the browser could "
                "not reach the loopback address, re-run with --paste."
            )
        if not verify_state(state, result.state):
            return _err("state mismatch — the response did not come from this login attempt")

    if result.error:
        return _err(f"ServiceNow returned an error instead of a code: {result.error}")
    if not result.code:
        return _err("no authorization code in the response")

    try:
        token = asyncio.run(_exchange(name, settings, result.code, verifier))
    except ServiceNowAPIError as e:
        return _err(str(e))

    print(f"\n✓ Authenticated to {_describe(name, settings)}", file=sys.stderr)
    print(f"  access token expires {_when(token.expires_at)}", file=sys.stderr)
    if token.refresh_token:
        print("  refresh token stored — restarts will not need a new login", file=sys.stderr)
    else:
        print(
            "  WARNING: no refresh token was issued. The server will need a new "
            "login every time this access token expires — enable the refresh_token "
            "grant on the Application Registry entry.",
            file=sys.stderr,
        )
    print(f"  store: {_store_for(settings).path}", file=sys.stderr)
    return 0


# ── logout ──────────────────────────────────────────────────────────


def cmd_logout(args: argparse.Namespace) -> int:
    try:
        name, settings = _resolve(args.instance)
    except (KeyError, ValueError) as e:
        return _err(str(e))

    store = _store_for(settings)
    key = token_key(settings.instance_url, settings.client_id)

    if args.access_only:
        stored = store.load(key)
        if stored is None:
            return _err(f"no stored token for {_describe(name, settings)}")
        stored.expires_at = 0.0
        store.save(key, stored)
        print(
            f"Access token for {_describe(name, settings)} marked expired; "
            "the refresh token was kept.",
            file=sys.stderr,
        )
        return 0

    if store.delete(key):
        print(f"Removed stored tokens for {_describe(name, settings)}.", file=sys.stderr)
    else:
        print(f"No stored tokens for {_describe(name, settings)}.", file=sys.stderr)
    return 0


# ── auth-status ─────────────────────────────────────────────────────


def cmd_auth_status(args: argparse.Namespace) -> int:
    settings = Settings()
    try:
        if settings.instances_file:
            by_name, default_name = load_instance_settings(settings.instances_file, settings)
            targets = [(n, s) for n, s in sorted(by_name.items())]
            if args.instance:
                if args.instance not in by_name:
                    return _err(f"Unknown instance '{args.instance}'. Available: {sorted(by_name)}")
                targets = [(args.instance, by_name[args.instance])]
        else:
            default_name = ""
            targets = [("", settings)]
    except (KeyError, ValueError) as e:
        return _err(str(e))

    print(f"store: {_store_for(settings).path}", file=sys.stderr)
    for name, s in targets:
        label = name or s.instance_url
        marker = " (default)" if name and name == default_name else ""
        if s.auth_method != "oauth_authorization_code":
            print(
                f"  {label}{marker}: auth_method={s.auth_method} — no login needed", file=sys.stderr
            )
            continue
        stored = _store_for(s).load(token_key(s.instance_url, s.client_id))
        if stored is None:
            print(
                f"  {label}{marker}: NO TOKEN — run 'simple-servicenow-mcp login"
                f"{f' --instance {name}' if name else ''}'",
                file=sys.stderr,
            )
        elif stored.is_expired:
            has_rt = "refreshable" if stored.refresh_token else "NOT refreshable — login again"
            print(f"  {label}{marker}: access token expired ({has_rt})", file=sys.stderr)
        else:
            print(
                f"  {label}{marker}: valid, expires {_when(stored.expires_at)} "
                f"({int(stored.expires_in)}s)",
                file=sys.stderr,
            )
    return 0


# ── entry point ─────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simple-servicenow-mcp",
        description="Authenticate this MCP server to ServiceNow in a browser.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Authorise in a browser and store the tokens")
    login.add_argument("--instance", help="Named instance from instances.json")
    login.add_argument(
        "--paste",
        action="store_true",
        help="Do not listen on the loopback port; paste the code (or redirect URL) "
        "instead. Use when only ServiceNow's own oauth_redirect.do is registered, "
        "or when there is no browser on this machine.",
    )
    login.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the redirect (default: 300). The first "
        "authorisation also shows a consent page, so allow time for it.",
    )
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="Remove stored tokens")
    logout.add_argument("--instance", help="Named instance from instances.json")
    logout.add_argument(
        "--access-only",
        action="store_true",
        help="Expire the access token but keep the refresh token — exercises the "
        "refresh path without a new browser login.",
    )
    logout.set_defaults(func=cmd_logout)

    status = sub.add_parser("auth-status", help="Show stored token state per instance")
    status.add_argument("--instance", help="Named instance from instances.json")
    status.set_defaults(func=cmd_auth_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
