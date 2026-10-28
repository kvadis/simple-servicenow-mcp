"""A fake OAuth identity provider for the HTTP-auth tests.

It serves discovery and JWKS through httpx.MockTransport; tokens are signed
with RSA keys generated per test session.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://idp.example.com"
AUDIENCE = "https://mcp.example.com/mcp"
EXP = 2_000_000_000  # 2033-05-18, fixed so expectations are literals

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    data = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**data, "kid": kid, "use": "sig", "alg": "RS256"}


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
        self.keys = keys if keys is not None else [jwk(KEY, "k1")]
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


def token_claims(overrides: dict[str, Any], drop: str | None = None) -> dict[str, Any]:
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "azp": "client-abc",
        "scope": "mcp.read mcp.write",
        "exp": EXP,
    }
    result = {**base, **overrides}
    if drop:
        del result[drop]
    return result


def mint(
    claims: dict[str, Any],
    *,
    kid: str = "k1",
    key: Any = KEY,
    alg: str = "RS256",
    drop: str | None = None,
) -> str:
    return jwt.encode(token_claims(claims, drop), key, algorithm=alg, headers={"kid": kid})


OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now
