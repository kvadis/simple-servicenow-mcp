"""Token store: persistence, permissions, key isolation, corruption tolerance.

The store is the only piece of the authorization-code flow that outlives the
process, so its failure modes are the ones that strand a user. In particular a
corrupt file must degrade to "no token" — never raise — because the store is
read during client construction and an exception there kills server startup for
*every* configured instance, not just the damaged one.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from simple_servicenow_mcp.token_store import (
    STORE_VERSION,
    StoredToken,
    TokenStore,
    default_store_path,
    token_key,
)


@pytest.fixture
def store(tmp_path):
    # Nested so the store has to create the directory itself — that is what
    # exercises the 0700 mkdir.
    return TokenStore(tmp_path / "cfg" / "tokens.json")


def _token(**over):
    base = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": time.time() + 1800,
        "instance_name": "acme-dev",
        "scope": "useraccount",
        "obtained_at": time.time(),
    }
    base.update(over)
    return StoredToken(**base)


# ── keys ────────────────────────────────────────────────────────────


def test_token_key_uses_host_and_client_id():
    assert token_key("https://acmedev.service-now.com", "cid") == "acmedev.service-now.com|cid"


def test_token_key_normalises_case_scheme_and_trailing_slash():
    variants = [
        "https://ACMEDEV.service-now.com",
        "https://acmedev.service-now.com/",
        "acmedev.service-now.com",
        "http://acmedev.service-now.com",
    ]
    keys = {token_key(v, "cid") for v in variants}
    assert keys == {"acmedev.service-now.com|cid"}, (
        "the same instance reached by differently-written URLs must share one grant"
    )


def test_token_key_distinguishes_clients_on_the_same_host():
    a = token_key("https://acmedev.service-now.com", "cid-a")
    b = token_key("https://acmedev.service-now.com", "cid-b")
    assert a != b


# ── round trip ──────────────────────────────────────────────────────


def test_round_trip(store):
    key = token_key("https://acmedev.service-now.com", "cid")
    tok = _token()
    store.save(key, tok)
    assert store.load(key) == tok


def test_load_returns_none_when_file_absent(store):
    assert store.load("nope|cid") is None


def test_load_returns_none_for_unknown_key(store):
    store.save(token_key("https://a.service-now.com", "cid"), _token())
    assert store.load("other.service-now.com|cid") is None


def test_save_preserves_other_entries(store):
    k1 = token_key("https://a.service-now.com", "cid")
    k2 = token_key("https://b.service-now.com", "cid")
    store.save(k1, _token(access_token="at-a"))
    store.save(k2, _token(access_token="at-b"))
    assert store.load(k1).access_token == "at-a"
    assert store.load(k2).access_token == "at-b"


def test_key_isolation_across_hosts_and_clients(store):
    combos = {}
    for host in ("a.service-now.com", "b.service-now.com"):
        for cid in ("cid-1", "cid-2"):
            key = token_key(f"https://{host}", cid)
            combos[key] = f"{host}:{cid}"
            store.save(key, _token(access_token=f"{host}:{cid}"))
    assert len(combos) == 4
    for key, expected in combos.items():
        assert store.load(key).access_token == expected


def test_delete_removes_only_that_key(store):
    k1 = token_key("https://a.service-now.com", "cid")
    k2 = token_key("https://b.service-now.com", "cid")
    store.save(k1, _token())
    store.save(k2, _token())
    assert store.delete(k1) is True
    assert store.load(k1) is None
    assert store.load(k2) is not None
    assert store.delete(k1) is False, "deleting an absent key reports False, does not raise"


def test_written_file_declares_its_version(store):
    store.save(token_key("https://a.service-now.com", "cid"), _token())
    raw = json.loads(store.path.read_text())
    assert raw["version"] == STORE_VERSION
    assert "tokens" in raw


# ── permissions ─────────────────────────────────────────────────────


def test_file_is_0600_and_dir_is_0700(store):
    store.save(token_key("https://a.service-now.com", "cid"), _token())
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


def test_permissions_survive_a_rewrite(store):
    key = token_key("https://a.service-now.com", "cid")
    store.save(key, _token())
    store.save(key, _token(access_token="at-2"))
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_no_secret_material_beyond_tokens_is_written(store):
    """code_verifier / state are CLI-only and must never reach disk."""
    store.save(token_key("https://a.service-now.com", "cid"), _token())
    text = store.path.read_text()
    assert "code_verifier" not in text
    assert "code_challenge" not in text
    assert '"state"' not in text


# ── corruption tolerance ────────────────────────────────────────────


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   ",
        "{",
        "not json at all",
        '{"version": 1, "tokens": "not-a-dict"}',
        "[]",
        '{"no_tokens_key": true}',
    ],
    ids=["empty", "blank", "truncated", "plaintext", "tokens-not-dict", "list", "missing-key"],
)
def test_corrupt_file_loads_as_no_token(store, garbage):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(garbage)
    assert store.load("a.service-now.com|cid") is None, (
        "a damaged store must degrade to 'needs login', never raise — it is read "
        "during client construction and would otherwise kill server startup"
    )


def test_corrupt_file_is_overwritten_by_save(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ broken")
    key = token_key("https://a.service-now.com", "cid")
    store.save(key, _token())
    assert store.load(key) is not None


def test_malformed_entry_is_skipped_not_fatal(store):
    """One bad record must not take out the good ones."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "version": STORE_VERSION,
                "tokens": {
                    "bad|cid": {"unexpected": "shape"},
                    "good|cid": {
                        "access_token": "at",
                        "refresh_token": "rt",
                        "expires_at": time.time() + 60,
                        "instance_name": "x",
                        "scope": "",
                        "obtained_at": 0.0,
                    },
                },
            }
        )
    )
    assert store.load("bad|cid") is None
    assert store.load("good|cid").access_token == "at"


# ── atomicity ───────────────────────────────────────────────────────


def test_write_failure_leaves_no_partial_file(store, monkeypatch):
    key = token_key("https://a.service-now.com", "cid")
    store.save(key, _token(access_token="original"))
    before = store.path.read_text()

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(key, _token(access_token="never-written"))

    assert store.path.read_text() == before, "the previous store must survive a failed write"
    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != store.path.name]
    assert leftovers == [], f"temp files left behind: {leftovers}"


# ── expiry helpers ──────────────────────────────────────────────────


def test_is_expired_reflects_the_clock():
    assert _token(expires_at=time.time() - 1).is_expired is True
    assert _token(expires_at=time.time() + 60).is_expired is False


def test_expires_in_seconds_is_never_negative():
    assert _token(expires_at=time.time() - 100).expires_in == 0
    assert 50 <= _token(expires_at=time.time() + 60).expires_in <= 60


# ── default path ────────────────────────────────────────────────────


def test_default_path_honours_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_store_path() == tmp_path / "simple-servicenow-mcp" / "tokens.json"


def test_default_path_falls_back_to_home_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    assert default_store_path() == tmp_path / ".config" / "simple-servicenow-mcp" / "tokens.json"
