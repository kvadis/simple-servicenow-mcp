"""PKCE helpers and authorize-URL construction.

The S256 test is a known-answer test taken from RFC 7636 Appendix B, not a
re-derivation of the same formula — a test that recomputes what the
implementation computes would pass even if both were wrong.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from simple_servicenow_mcp.oauth import (
    build_authorize_url,
    challenge_s256,
    new_state,
    new_verifier,
    verify_state,
)

# RFC 7636 Appendix B — "Example for the S256 code_challenge_method".
RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

UNRESERVED = re.compile(r"^[A-Za-z0-9\-._~]+$")


# ── verifier ────────────────────────────────────────────────────────


def test_verifier_length_is_within_rfc_bounds():
    assert 43 <= len(new_verifier()) <= 128


def test_verifier_uses_only_unreserved_characters():
    for _ in range(50):
        assert UNRESERVED.match(new_verifier())


def test_verifier_is_unpadded():
    assert "=" not in new_verifier()


def test_verifier_is_random():
    assert len({new_verifier() for _ in range(20)}) == 20


# ── challenge ───────────────────────────────────────────────────────


def test_challenge_matches_rfc7636_known_answer():
    assert challenge_s256(RFC7636_VERIFIER) == RFC7636_CHALLENGE


def test_challenge_is_unpadded_base64url():
    c = challenge_s256(new_verifier())
    assert "=" not in c
    assert "+" not in c and "/" not in c
    assert len(c) == 43  # sha256 -> 32 bytes -> 43 base64url chars unpadded


def test_challenge_is_deterministic():
    v = new_verifier()
    assert challenge_s256(v) == challenge_s256(v)


def test_different_verifiers_give_different_challenges():
    assert challenge_s256(new_verifier()) != challenge_s256(new_verifier())


# ── state ───────────────────────────────────────────────────────────


def test_state_is_random_and_unreserved():
    states = {new_state() for _ in range(20)}
    assert len(states) == 20
    for s in states:
        assert UNRESERVED.match(s)
        assert len(s) >= 22


def test_verify_state_accepts_a_match():
    s = new_state()
    assert verify_state(s, s) is True


def test_verify_state_rejects_a_mismatch_or_absence():
    s = new_state()
    assert verify_state(s, new_state()) is False
    assert verify_state(s, "") is False
    assert verify_state(s, None) is False


# ── authorize URL ───────────────────────────────────────────────────


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def _url(**over) -> str:
    kwargs = {
        "instance_url": "https://acmedev.service-now.com",
        "client_id": "cid",
        "redirect_uri": "http://127.0.0.1:8765/callback",
        "state": "st-123",
        "code_challenge": RFC7636_CHALLENGE,
    }
    kwargs.update(over)
    return build_authorize_url(**kwargs)


def test_authorize_url_targets_oauth_auth_do():
    parts = urlsplit(_url())
    assert parts.scheme == "https"
    assert parts.netloc == "acmedev.service-now.com"
    assert parts.path == "/oauth_auth.do"


def test_authorize_url_carries_the_required_pkce_params():
    p = _params(_url())
    assert p["response_type"] == "code"
    assert p["client_id"] == "cid"
    assert p["redirect_uri"] == "http://127.0.0.1:8765/callback"
    assert p["state"] == "st-123"
    assert p["code_challenge"] == RFC7636_CHALLENGE
    assert p["code_challenge_method"] == "S256"


def test_authorize_url_omits_scope_when_empty():
    assert "scope" not in _params(_url())
    assert "scope" not in _params(_url(scope=""))


def test_authorize_url_includes_scope_when_set():
    assert _params(_url(scope="useraccount"))["scope"] == "useraccount"


def test_authorize_url_tolerates_a_trailing_slash_on_the_instance():
    parts = urlsplit(_url(instance_url="https://acmedev.service-now.com/"))
    assert parts.path == "/oauth_auth.do", "no double slash"


def test_authorize_url_never_carries_the_verifier():
    """The whole point of PKCE: only the challenge travels in the front channel."""
    v = new_verifier()
    url = _url(code_challenge=challenge_s256(v))
    assert v not in url


def test_authorize_url_percent_encodes_the_redirect():
    assert "127.0.0.1%3A8765" in _url() or "http%3A%2F%2F127.0.0.1%3A8765" in _url()
