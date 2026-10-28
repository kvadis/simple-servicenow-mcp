"""JwtTokenVerifier: access tokens from the operator's identity provider.

A fake IdP serves discovery and JWKS through httpx.MockTransport; tokens are
signed with an RSA key generated here.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://idp.example.com"
AUDIENCE = "https://mcp.example.com/mcp"
EXP = 2_000_000_000  # 2033-05-18, fixed so expectations are literals

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**jwk, "kid": kid, "use": "sig", "alg": "RS256"}


class FakeIdP:
    """Discovery + JWKS endpoints; records every request path.

    `discovery_path` is where the metadata lives; `keys_status` lets a test make
    the JWKS endpoint fail.
    """

    def __init__(
        self,
        keys: list[dict[str, Any]] | None = None,
        issuer: str = ISSUER,
        discovery_path: str = "/.well-known/openid-configuration",
    ) -> None:
        self.keys = keys if keys is not None else [_jwk(_KEY, "k1")]
        self.issuer = issuer
        self.discovery_path = discovery_path
        self.keys_status = 200
        self.paths: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == self.discovery_path:
            return httpx.Response(200, json={"issuer": self.issuer, "jwks_uri": f"{ISSUER}/keys"})
        if request.url.path == "/keys":
            return httpx.Response(self.keys_status, json={"keys": self.keys})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @property
    def key_fetches(self) -> int:
        return self.paths.count("/keys")


def _claims(overrides: dict[str, Any], drop: str | None = None) -> dict[str, Any]:
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "azp": "client-abc",
        "scope": "mcp.read mcp.write",
        "exp": EXP,
    }
    claims = {**base, **overrides}
    if drop:
        del claims[drop]
    return claims


def _mint(
    claims: dict[str, Any],
    *,
    kid: str = "k1",
    key: Any = _KEY,
    alg: str = "RS256",
    drop: str | None = None,
) -> str:
    return jwt.encode(_claims(claims, drop), key, algorithm=alg, headers={"kid": kid})


def _verifier(idp: FakeIdP, issuer: str = ISSUER, **kwargs: Any):
    from simple_servicenow_mcp.http_auth import JwtTokenVerifier

    return JwtTokenVerifier(issuer=issuer, audiences=[AUDIENCE], transport=idp.transport, **kwargs)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_valid_token_becomes_an_access_token() -> None:
    idp = FakeIdP()

    token = _mint({"preferred_username": "ada@example.com"})
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

_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_NOW = int(time.time())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


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
    token = _mint(overrides, drop=drop)

    assert await _verifier(FakeIdP()).verify_token(token) is None


async def test_signature_from_another_key_is_rejected() -> None:
    assert await _verifier(FakeIdP()).verify_token(_mint({}, key=_OTHER_KEY)) is None


async def test_unsigned_token_is_rejected() -> None:
    token = jwt.encode(_claims({}), None, algorithm="none", headers={"kid": "k1"})

    assert await _verifier(FakeIdP()).verify_token(token) is None


async def test_hs256_signed_with_the_public_key_is_rejected() -> None:
    """Key confusion: the RSA public key used as an HMAC secret."""
    public_pem = _KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = {"alg": "HS256", "typ": "JWT", "kid": "k1"}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(_claims({})).encode())}"
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()

    assert await _verifier(FakeIdP()).verify_token(f"{signing_input}.{_b64(signature)}") is None


async def test_token_without_kid_is_rejected() -> None:
    token = jwt.encode(_claims({}), _KEY, algorithm="RS256")

    assert await _verifier(FakeIdP()).verify_token(token) is None


async def test_header_alg_that_differs_from_the_key_is_rejected() -> None:
    assert await _verifier(FakeIdP()).verify_token(_mint({}, alg="RS384")) is None


async def test_malformed_token_is_rejected() -> None:
    assert await _verifier(FakeIdP()).verify_token("not-a-jwt") is None


async def test_discovery_with_a_different_issuer_is_not_trusted() -> None:
    idp = FakeIdP(issuer="https://evil.example.com")

    assert await _verifier(idp).verify_token(_mint({})) is None


async def test_encryption_keys_are_not_used_for_signatures() -> None:
    idp = FakeIdP(keys=[{**_jwk(_KEY, "k1"), "use": "enc"}])

    assert await _verifier(idp).verify_token(_mint({})) is None


async def test_symmetric_keys_in_the_jwks_are_not_used() -> None:
    secret = b"s" * 32
    idp = FakeIdP(keys=[{"kty": "oct", "k": _b64(secret), "kid": "h1", "alg": "HS256"}])
    token = jwt.encode(_claims({}), secret, algorithm="HS256", headers={"kid": "h1"})

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
    access = await _verifier(FakeIdP()).verify_token(_mint(overrides, drop=drop))

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
    access = await _verifier(FakeIdP()).verify_token(_mint(claims, drop="azp"))

    assert access is not None
    assert access.client_id == client_id


# ── Keys: rotation, rate limit, cache lifetime, outages ──────────────────


async def test_a_new_kid_triggers_one_refetch() -> None:
    idp = FakeIdP()
    verifier = _verifier(idp, clock=Clock())
    assert await verifier.verify_token(_mint({})) is not None

    idp.keys = [_jwk(_KEY, "k1"), _jwk(_OTHER_KEY, "k2")]  # the IdP rotates in a new key
    access = await verifier.verify_token(_mint({}, kid="k2", key=_OTHER_KEY))

    assert access is not None
    assert idp.key_fetches == 2


async def test_unknown_kids_refetch_at_most_once_a_minute() -> None:
    idp, clock = FakeIdP(), Clock()
    verifier = _verifier(idp, clock=clock)
    await verifier.verify_token(_mint({}))

    await verifier.verify_token(_mint({}, kid="nope-1"))
    await verifier.verify_token(_mint({}, kid="nope-2"))
    assert idp.key_fetches == 2  # the first unknown kid refetched, the second did not

    clock.now += 61
    await verifier.verify_token(_mint({}, kid="nope-3"))
    assert idp.key_fetches == 3


async def test_keys_are_refreshed_after_ten_minutes() -> None:
    idp, clock = FakeIdP(), Clock()
    verifier = _verifier(idp, clock=clock)
    await verifier.verify_token(_mint({}))

    clock.now += 599
    await verifier.verify_token(_mint({}))
    assert idp.key_fetches == 1

    clock.now += 2
    await verifier.verify_token(_mint({}))
    assert idp.key_fetches == 2


async def test_a_failed_refetch_keeps_the_known_keys() -> None:
    idp, clock = FakeIdP(), Clock()
    verifier = _verifier(idp, clock=clock)
    await verifier.verify_token(_mint({}))

    idp.keys_status = 503
    clock.now += 601
    assert await verifier.verify_token(_mint({})) is not None


async def test_concurrent_unknown_kids_share_one_refetch() -> None:
    idp = FakeIdP()
    verifier = _verifier(idp, clock=Clock())
    await verifier.verify_token(_mint({}))

    await asyncio.gather(*(verifier.verify_token(_mint({}, kid=f"nope-{i}")) for i in range(5)))

    assert idp.key_fetches == 2


# ── Discovery URL shapes ──────────────────────────────────────────────────


async def test_issuer_with_a_trailing_slash() -> None:
    """Auth0 issuers end in '/'; iss in the token keeps it, the URL must not double it."""
    issuer = f"{ISSUER}/"
    idp = FakeIdP(issuer=issuer)

    access = await _verifier(idp, issuer=issuer).verify_token(_mint({"iss": issuer}))

    assert access is not None
    assert idp.paths[0] == "/.well-known/openid-configuration"


async def test_rfc8414_discovery_inserts_well_known_before_the_issuer_path() -> None:
    issuer = f"{ISSUER}/tenant1"
    idp = FakeIdP(issuer=issuer, discovery_path="/.well-known/oauth-authorization-server/tenant1")

    access = await _verifier(idp, issuer=issuer).verify_token(_mint({"iss": issuer}))

    assert access is not None
    assert idp.paths[:2] == [
        "/tenant1/.well-known/openid-configuration",
        "/.well-known/oauth-authorization-server/tenant1",
    ]
