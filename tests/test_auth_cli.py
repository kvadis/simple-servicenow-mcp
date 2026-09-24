"""The login CLI and the loopback redirect receiver.

These cover the half of the flow a user actually touches. The receiver tests
drive a real socket on 127.0.0.1 — the bug they exist to catch (binding a
listener the browser cannot reach) is invisible to a mocked transport.
"""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest

from simple_servicenow_mcp import auth_cli
from simple_servicenow_mcp.oauth import parse_redirect_input, receive_code
from simple_servicenow_mcp.token_store import StoredToken, TokenStore, token_key

INSTANCE = "https://acmedev.service-now.com"
CLIENT_ID = "cid"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """An authorization-code single-instance config.

    The autouse ``_isolate_settings`` fixture in conftest already keeps the
    developer's ``.env`` and SN_* variables out; this sets the config under test.
    """
    monkeypatch.setenv("SN_INSTANCE_URL", INSTANCE)
    monkeypatch.setenv("SN_AUTH_METHOD", "oauth_authorization_code")
    monkeypatch.setenv("SN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SN_CLIENT_SECRET", "")
    monkeypatch.setenv("SN_TOKEN_STORE", str(tmp_path / "tokens.json"))
    monkeypatch.delenv("SN_INSTANCES_FILE", raising=False)
    return tmp_path / "tokens.json"


def _token(**over) -> StoredToken:
    base = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_at": time.time() + 1800,
        "instance_name": "",
        "scope": "",
        "obtained_at": time.time(),
    }
    base.update(over)
    return StoredToken(**base)


# ── parse_redirect_input ────────────────────────────────────────────


def test_paste_accepts_a_bare_code():
    assert parse_redirect_input("  abc123  ").code == "abc123"


def test_paste_accepts_a_full_redirect_url():
    r = parse_redirect_input("http://127.0.0.1:8765/callback?code=abc&state=xyz")
    assert (r.code, r.state) == ("abc", "xyz")


def test_paste_surfaces_an_error_response():
    r = parse_redirect_input("http://127.0.0.1:8765/callback?error=access_denied")
    assert r.error == "access_denied"
    assert r.code is None


def test_paste_of_nothing_yields_nothing():
    assert parse_redirect_input("   ").code is None


# ── receive_code (real loopback socket) ─────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get_when_up(url: str, tries: int = 50) -> httpx.Response:
    """Poll until the listener has bound, then issue the request."""
    last: Exception | None = None
    for _ in range(tries):
        try:
            return httpx.get(url, timeout=5)
        except httpx.ConnectError as e:  # not bound yet
            last = e
            time.sleep(0.02)
    raise AssertionError(f"listener never came up: {last}")


def test_receive_code_captures_code_and_state():
    port = _free_port()
    uri = f"http://127.0.0.1:{port}/callback"
    out: dict = {}

    t = threading.Thread(target=lambda: out.update(r=receive_code(uri, timeout=10)), daemon=True)
    t.start()
    resp = _get_when_up(f"{uri}?code=abc&state=xyz")
    t.join(timeout=10)

    assert resp.status_code == 200
    assert b"Authentication complete" in resp.content
    assert out["r"].code == "abc"
    assert out["r"].state == "xyz"


def test_receive_code_ignores_favicon_and_keeps_waiting():
    """Browsers ask for /favicon.ico; that must not consume the single wait."""
    port = _free_port()
    uri = f"http://127.0.0.1:{port}/callback"
    out: dict = {}

    t = threading.Thread(target=lambda: out.update(r=receive_code(uri, timeout=10)), daemon=True)
    t.start()
    stray = _get_when_up(f"http://127.0.0.1:{port}/favicon.ico")
    assert stray.status_code == 404
    _get_when_up(f"{uri}?code=real&state=s")
    t.join(timeout=10)

    assert out["r"].code == "real"


def test_receive_code_returns_none_on_timeout():
    port = _free_port()
    assert receive_code(f"http://127.0.0.1:{port}/callback", timeout=0.3) is None


def test_receive_code_reports_a_busy_port_clearly():
    """The port is pinned by the registered redirect URI, so this needs saying."""
    port = _free_port()
    with socket.socket() as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", port))
        blocker.listen(1)
        with pytest.raises(RuntimeError, match="Could not bind"):
            receive_code(f"http://127.0.0.1:{port}/callback", timeout=1)


# ── login ───────────────────────────────────────────────────────────


def test_login_rejects_a_non_authcode_instance(env, monkeypatch, capsys):
    monkeypatch.setenv("SN_AUTH_METHOD", "basic")
    assert auth_cli.main(["login"]) == 1
    assert "only applies to" in capsys.readouterr().err


def test_login_requires_a_client_id(env, monkeypatch, capsys):
    monkeypatch.setenv("SN_CLIENT_ID", "")
    assert auth_cli.main(["login"]) == 1
    assert "client_id" in capsys.readouterr().err


def test_login_paste_path_stores_the_token(env, monkeypatch, capsys):
    async def fake_exchange(name, settings, code, verifier):
        assert code == "the-code"
        TokenStore(settings.token_store).save(
            token_key(settings.instance_url, settings.client_id),
            _token(access_token=f"at-for-{code}"),
        )
        return _token(access_token=f"at-for-{code}")

    monkeypatch.setattr(auth_cli, "_exchange", fake_exchange)
    monkeypatch.setattr("builtins.input", lambda *a: "the-code")

    assert auth_cli.main(["login", "--paste"]) == 0
    err = capsys.readouterr().err
    assert "Authenticated" in err
    assert "refresh token stored" in err
    assert TokenStore(env).load(token_key(INSTANCE, CLIENT_ID)).access_token == "at-for-the-code"


def test_login_paste_rejects_a_mismatched_state(env, monkeypatch, capsys):
    monkeypatch.setattr(
        "builtins.input", lambda *a: "http://127.0.0.1:8765/callback?code=c&state=not-ours"
    )
    assert auth_cli.main(["login", "--paste"]) == 1
    assert "state mismatch" in capsys.readouterr().err


def test_login_warns_when_no_refresh_token_comes_back(env, monkeypatch, capsys):
    """Without one, every expiry means another browser round-trip."""

    async def fake_exchange(name, settings, code, verifier):
        return _token(refresh_token=None)

    monkeypatch.setattr(auth_cli, "_exchange", fake_exchange)
    monkeypatch.setattr("builtins.input", lambda *a: "c")
    assert auth_cli.main(["login", "--paste"]) == 0
    assert "no refresh token was issued" in capsys.readouterr().err


def test_login_reports_an_error_returned_by_servicenow(env, monkeypatch, capsys):
    monkeypatch.setattr(
        "builtins.input", lambda *a: "http://127.0.0.1:8765/callback?error=access_denied"
    )
    assert auth_cli.main(["login", "--paste"]) == 1
    assert "access_denied" in capsys.readouterr().err


# ── logout / auth-status ────────────────────────────────────────────


def test_logout_removes_the_token(env, capsys):
    store = TokenStore(env)
    store.save(token_key(INSTANCE, CLIENT_ID), _token())
    assert auth_cli.main(["logout"]) == 0
    assert store.load(token_key(INSTANCE, CLIENT_ID)) is None
    assert "Removed stored tokens" in capsys.readouterr().err


def test_logout_access_only_keeps_the_refresh_token(env, capsys):
    store = TokenStore(env)
    store.save(token_key(INSTANCE, CLIENT_ID), _token())
    assert auth_cli.main(["logout", "--access-only"]) == 0
    left = store.load(token_key(INSTANCE, CLIENT_ID))
    assert left.refresh_token == "rt"
    assert left.is_expired


def test_logout_is_quiet_when_there_is_nothing_to_remove(env, capsys):
    assert auth_cli.main(["logout"]) == 0
    assert "No stored tokens" in capsys.readouterr().err


def test_auth_status_reports_a_missing_token_with_the_command_to_run(env, capsys):
    assert auth_cli.main(["auth-status"]) == 0
    assert "NO TOKEN" in capsys.readouterr().err


def test_auth_status_reports_a_valid_token(env, capsys):
    TokenStore(env).save(token_key(INSTANCE, CLIENT_ID), _token())
    assert auth_cli.main(["auth-status"]) == 0
    assert "valid, expires" in capsys.readouterr().err


def test_auth_status_flags_an_expired_but_refreshable_token(env, capsys):
    TokenStore(env).save(token_key(INSTANCE, CLIENT_ID), _token(expires_at=0))
    assert auth_cli.main(["auth-status"]) == 0
    out = capsys.readouterr().err
    assert "expired" in out and "refreshable" in out


def test_instance_flag_without_a_registry_is_an_error(env, capsys):
    assert auth_cli.main(["logout", "--instance", "nope"]) == 1
    assert "SN_INSTANCES_FILE" in capsys.readouterr().err


# ── server dispatch ─────────────────────────────────────────────────


def test_server_main_routes_auth_subcommands(env, monkeypatch):
    """`login` must reach auth_cli — the server's own parser has no subparsers."""
    from simple_servicenow_mcp import server

    seen: dict = {}
    monkeypatch.setattr(server, "AUTH_SUBCOMMANDS", ("login", "logout", "auth-status"))
    monkeypatch.setattr(
        "simple_servicenow_mcp.auth_cli.main", lambda argv: seen.setdefault("argv", argv) and 0
    )
    with pytest.raises(SystemExit):
        server.main(["login", "--paste"])
    assert seen["argv"] == ["login", "--paste"]


def test_server_main_still_parses_its_own_flags(monkeypatch):
    from simple_servicenow_mcp import server

    args = server._parse_args(["--transport", "http", "--port", "9001", "--read-only"])
    assert (args.transport, args.port, args.read_only) == ("http", 9001, True)


def test_login_binds_the_listener_before_opening_the_browser(env, monkeypatch, capsys):
    """With consent already granted, the redirect can arrive almost instantly.

    So the port must already be held when the browser is launched — not after.
    """
    import webbrowser

    port = _free_port()
    monkeypatch.setenv("SN_OAUTH_REDIRECT_URI", f"http://127.0.0.1:{port}/callback")
    seen: dict = {}

    def fake_open(url: str) -> bool:
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
            seen["listening"] = False  # we got the port, so nothing was holding it
        except OSError:
            seen["listening"] = True  # port already taken — the listener is up
        finally:
            probe.close()
        return True

    monkeypatch.setattr(webbrowser, "open", fake_open)
    assert auth_cli.main(["login", "--timeout", "0.2"]) == 1  # times out, as intended
    assert seen["listening"] is True
    assert "timed out" in capsys.readouterr().err
