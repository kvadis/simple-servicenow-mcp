"""JwtTokenVerifier: access tokens from the operator's identity provider.

A fake IdP serves discovery and JWKS through httpx.MockTransport; tokens are
signed with an RSA key generated here.
"""

from __future__ import annotations

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
    """Discovery + JWKS endpoints; records every request path."""

    def __init__(self, keys: list[dict[str, Any]] | None = None, issuer: str = ISSUER) -> None:
        self.keys = keys if keys is not None else [_jwk(_KEY, "k1")]
        self.issuer = issuer
        self.paths: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": self.issuer, "jwks_uri": f"{ISSUER}/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": self.keys})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


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


def _verifier(idp: FakeIdP, **kwargs: Any):
    from simple_servicenow_mcp.http_auth import JwtTokenVerifier

    return JwtTokenVerifier(issuer=ISSUER, audiences=[AUDIENCE], transport=idp.transport, **kwargs)


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
