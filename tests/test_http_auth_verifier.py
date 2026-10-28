"""JwtTokenVerifier: access tokens from the operator's identity provider.

A fake IdP serves discovery and JWKS through httpx.MockTransport; tokens are
signed with an RSA key generated here.
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
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


def _mint(claims: dict[str, Any], *, kid: str = "k1", key: Any = _KEY, alg: str = "RS256") -> str:
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "azp": "client-abc",
        "scope": "mcp.read mcp.write",
        "exp": EXP,
    }
    return jwt.encode({**base, **claims}, key, algorithm=alg, headers={"kid": kid})


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
