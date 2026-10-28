"""OAuth resource-server mode for the HTTP transport.

Access tokens come from the operator's identity provider; this module checks
them. Tokens are never forwarded to ServiceNow: tool calls still use the
server's own ServiceNow credentials.
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

#: Clock skew allowed on `exp` and `nbf`, in seconds.
SKEW_S = 60


class JwtTokenVerifier:
    """The SDK's `TokenVerifier`: a valid JWT access token, or None (never raises)."""

    def __init__(
        self,
        *,
        issuer: str,
        audiences: list[str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._issuer = issuer
        self._audiences = audiences
        self._transport = transport
        self._keys: jwt.PyJWKSet | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            keys = await self._key_set()
            key = keys[jwt.get_unverified_header(token)["kid"]]
            # The algorithm comes from the IdP's key, never from the token header.
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[key.algorithm_name],
                audience=self._audiences,
                issuer=self._issuer,
                leeway=SKEW_S,
            )
        except Exception:
            return None
        return AccessToken(
            token=token,
            client_id=claims["azp"],
            scopes=claims["scope"].split(),
            # The SDK middleware compares expires_at with no skew of its own.
            expires_at=claims["exp"] + SKEW_S,
            subject=claims["sub"],
            claims=claims,
        )

    async def _key_set(self) -> jwt.PyJWKSet:
        if self._keys is None:
            async with httpx.AsyncClient(transport=self._transport, timeout=10) as http:
                discovery = f"{self._issuer}/.well-known/openid-configuration"
                metadata = (await http.get(discovery)).raise_for_status().json()
                jwks = (await http.get(metadata["jwks_uri"])).raise_for_status().json()
            self._keys = jwt.PyJWKSet.from_dict(jwks)
        return self._keys
