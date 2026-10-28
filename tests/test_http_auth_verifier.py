"""JwtTokenVerifier: access tokens from the operator's identity provider.

A fake IdP serves discovery and JWKS through httpx.MockTransport; tokens are
signed with an RSA key generated here.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from _fake_idp import AUDIENCE, ISSUER, KEY, OTHER_KEY, Clock, FakeIdP, b64, jwk, mint, token_claims


def _verifier(idp: FakeIdP, issuer: str = ISSUER, **kwargs: Any):
    from simple_servicenow_mcp.http_auth import JwtTokenVerifier

    return JwtTokenVerifier(issuer=issuer, audiences=[AUDIENCE], transport=idp.transport, **kwargs)


async def test_valid_token_becomes_an_access_token() -> None:
    idp = FakeIdP()

    token = mint({"preferred_username": "ada@example.com"})
    access = await _verifier(idp).verify_token(token)

    assert access is not None
    assert access.token == token
    assert access.subject == "user-1"
    assert access.client_id == "client-abc"
    assert access.scopes == ["mcp.read", "mcp.write"]
    assert access.expires_at == 2_000_000_060  # exp + 60 s skew, or the SDK middleware undoes it
    assert access.claims is not None
    assert access.claims["preferred_username"] == "ada@example.com"
    assert idp.paths == ["/.well-known/openid-configuration", "/keys"]


# ── Rejections: each returns None, none raises ────────────────────────────

_NOW = int(time.time())


@pytest.mark.parametrize(
    ("overrides", "drop"),
    [
        pytest.param({"iss": "https://evil.example.com"}, None, id="wrong-issuer"),
        pytest.param({"aud": "https://other.example.com/mcp"}, None, id="wrong-audience"),
        pytest.param({"exp": _NOW - 120}, None, id="expired-beyond-skew"),
        pytest.param({"nbf": _NOW + 3600}, None, id="not-yet-valid"),
        pytest.param({}, "exp", id="no-exp"),
        pytest.param({}, "sub", id="no-sub"),
        pytest.param({"nonce": "n-123"}, None, id="id-token-with-nonce"),
        pytest.param({}, "scope", id="no-scope-no-scp"),
        pytest.param({"pad": "x" * 20_000}, None, id="over-16kb"),
    ],
)
async def test_bad_claims_are_rejected(overrides: dict[str, Any], drop: str | None) -> None:
    token = mint(overrides, drop=drop)

    assert await _verifier(FakeIdP()).verify_token(token) is None


async def test_signature_from_another_key_is_rejected() -> None:
    assert await _verifier(FakeIdP()).verify_token(mint({}, key=OTHER_KEY)) is None


async def test_unsigned_token_is_rejected() -> None:
    token = jwt.encode(token_claims({}), None, algorithm="none", headers={"kid": "k1"})

    assert await _verifier(FakeIdP()).verify_token(token) is None


async def test_hs256_signed_with_the_public_key_is_rejected() -> None:
    """Key confusion: the RSA public key used as an HMAC secret."""
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = {"alg": "HS256", "typ": "JWT", "kid": "k1"}
    signing_input = (
        f"{b64(json.dumps(header).encode())}.{b64(json.dumps(token_claims({})).encode())}"
    )
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()

    assert await _verifier(FakeIdP()).verify_token(f"{signing_input}.{b64(signature)}") is None


async def test_token_without_kid_is_rejected() -> None:
    token = jwt.encode(token_claims({}), KEY, algorithm="RS256")

    assert await _verifier(FakeIdP()).verify_token(token) is None


async def test_header_alg_that_differs_from_the_key_is_rejected() -> None:
    assert await _verifier(FakeIdP()).verify_token(mint({}, alg="RS384")) is None


async def test_malformed_token_is_rejected() -> None:
    assert await _verifier(FakeIdP()).verify_token("not-a-jwt") is None


async def test_discovery_with_a_different_issuer_is_not_trusted() -> None:
    idp = FakeIdP(issuer="https://evil.example.com")

    assert await _verifier(idp).verify_token(mint({})) is None


async def test_encryption_keys_are_not_used_for_signatures() -> None:
    idp = FakeIdP(keys=[{**jwk(KEY, "k1"), "use": "enc"}])

    assert await _verifier(idp).verify_token(mint({})) is None


async def test_symmetric_keys_in_the_jwks_are_not_used() -> None:
    secret = b"s" * 32
    idp = FakeIdP(keys=[{"kty": "oct", "k": b64(secret), "kid": "h1", "alg": "HS256"}])
    token = jwt.encode(token_claims({}), secret, algorithm="HS256", headers={"kid": "h1"})

    assert await _verifier(idp).verify_token(token) is None


# ── Scopes and client id across identity providers ───────────────────────


@pytest.mark.parametrize(
    ("overrides", "drop", "scopes"),
    [
        pytest.param(
            {"scp": "mcp.read mcp.write"}, "scope", ["mcp.read", "mcp.write"], id="entra-scp-string"
        ),
        pytest.param({"scp": ["mcp.read"]}, "scope", ["mcp.read"], id="okta-scp-list"),
        pytest.param({"scope": "a b", "scp": ["b", "c"]}, None, ["a", "b", "c"], id="both-merged"),
    ],
)
async def test_scopes_are_read_from_scope_and_scp(
    overrides: dict[str, Any], drop: str | None, scopes: list[str]
) -> None:
    access = await _verifier(FakeIdP()).verify_token(mint(overrides, drop=drop))

    assert access is not None
    assert access.scopes == scopes


@pytest.mark.parametrize(
    ("claims", "client_id"),
    [
        pytest.param({"client_id": "okta-app"}, "okta-app", id="client_id-claim"),
        pytest.param({"appid": "entra-v1-app"}, "entra-v1-app", id="entra-v1-appid"),
        pytest.param({}, "unknown", id="none"),
    ],
)
async def test_client_id_falls_back_when_azp_is_absent(
    claims: dict[str, Any], client_id: str
) -> None:
    access = await _verifier(FakeIdP()).verify_token(mint(claims, drop="azp"))

    assert access is not None
    assert access.client_id == client_id


# ── Keys: rotation, rate limit, cache lifetime, outages ──────────────────


async def test_a_new_kid_triggers_one_refetch() -> None:
    idp = FakeIdP()
    verifier = _verifier(idp, clock=Clock())
    assert await verifier.verify_token(mint({})) is not None

    idp.keys = [jwk(KEY, "k1"), jwk(OTHER_KEY, "k2")]  # the IdP rotates in a new key
    access = await verifier.verify_token(mint({}, kid="k2", key=OTHER_KEY))

    assert access is not None
    assert idp.key_fetches == 2


async def test_unknown_kids_refetch_at_most_once_a_minute() -> None:
    idp, clock = FakeIdP(), Clock()
    verifier = _verifier(idp, clock=clock)
    await verifier.verify_token(mint({}))

    await verifier.verify_token(mint({}, kid="nope-1"))
    await verifier.verify_token(mint({}, kid="nope-2"))
    assert idp.key_fetches == 2  # the first unknown kid refetched, the second did not

    clock.now += 61
    await verifier.verify_token(mint({}, kid="nope-3"))
    assert idp.key_fetches == 3


async def test_keys_are_refreshed_after_ten_minutes() -> None:
    idp, clock = FakeIdP(), Clock()
    verifier = _verifier(idp, clock=clock)
    await verifier.verify_token(mint({}))

    clock.now += 599
    await verifier.verify_token(mint({}))
    assert idp.key_fetches == 1

    clock.now += 2
    await verifier.verify_token(mint({}))
    assert idp.key_fetches == 2


async def test_a_failed_refetch_keeps_the_known_keys() -> None:
    idp, clock = FakeIdP(), Clock()
    verifier = _verifier(idp, clock=clock)
    await verifier.verify_token(mint({}))

    idp.keys_status = 503
    clock.now += 601
    assert await verifier.verify_token(mint({})) is not None


async def test_concurrent_unknown_kids_share_one_refetch() -> None:
    idp = FakeIdP()
    verifier = _verifier(idp, clock=Clock())
    await verifier.verify_token(mint({}))

    await asyncio.gather(*(verifier.verify_token(mint({}, kid=f"nope-{i}")) for i in range(5)))

    assert idp.key_fetches == 2


# ── Discovery URL shapes ──────────────────────────────────────────────────


async def test_issuer_with_a_trailing_slash() -> None:
    """Auth0 issuers end in '/'; iss in the token keeps it, the URL must not double it."""
    issuer = f"{ISSUER}/"
    idp = FakeIdP(issuer=issuer)

    access = await _verifier(idp, issuer=issuer).verify_token(mint({"iss": issuer}))

    assert access is not None
    assert idp.paths[0] == "/.well-known/openid-configuration"


async def test_rfc8414_discovery_inserts_well_known_before_the_issuer_path() -> None:
    issuer = f"{ISSUER}/tenant1"
    idp = FakeIdP(issuer=issuer, discovery_path="/.well-known/oauth-authorization-server/tenant1")

    access = await _verifier(idp, issuer=issuer).verify_token(mint({"iss": issuer}))

    assert access is not None
    assert idp.paths[:2] == [
        "/tenant1/.well-known/openid-configuration",
        "/.well-known/oauth-authorization-server/tenant1",
    ]
