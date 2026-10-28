"""OAuth resource-server mode for the HTTP transport.

Access tokens come from the operator's identity provider; this module checks
them. Tokens are never forwarded to ServiceNow: tool calls still use the
server's own ServiceNow credentials.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

#: Clock skew allowed on `exp` and `nbf`, in seconds.
SKEW_S = 60
#: Larger tokens are refused before any parsing.
MAX_TOKEN_BYTES = 16 * 1024
#: Asymmetric signatures only: no `none`, no HMAC.
ALGORITHMS = frozenset(
    f"{family}{bits}" for family in ("RS", "PS", "ES") for bits in (256, 384, 512)
)
#: How long fetched signing keys are trusted before a refresh.
KEYS_TTL_S = 600
#: Floor between refetches caused by an unknown `kid`, or after a failed fetch,
#: so random tokens can't make the server hammer the identity provider.
REFETCH_INTERVAL_S = 60


class JwtTokenVerifier:
    """The SDK's `TokenVerifier`: a valid JWT access token, or None (never raises)."""

    def __init__(
        self,
        *,
        issuer: str,
        audiences: list[str],
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._audiences = audiences
        self._transport = transport
        self._clock = clock
        self._lock = asyncio.Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._refresh_at = -math.inf  # keys expire, or a failed fetch is retried, at this time
        self._miss_refetch_at = -math.inf  # earliest refetch for an unknown kid

    async def verify_token(self, token: str) -> AccessToken | None:
        if len(token) > MAX_TOKEN_BYTES:
            return None
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ALGORITHMS or "kid" not in header:
                return None
            key = await self._key(header["kid"])
            if key is None:
                return None
            # The algorithm comes from the IdP's key, never from the token header.
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[key.algorithm_name],
                audience=self._audiences,
                issuer=self._issuer,
                leeway=SKEW_S,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            # An ID token can carry the right iss and aud (when aud is a client ID);
            # access tokens carry scopes and never a nonce.
            if "nonce" in claims or ("scope" not in claims and "scp" not in claims):
                return None
            return AccessToken(
                token=token,
                client_id=str(
                    claims.get("azp") or claims.get("client_id") or claims.get("appid") or "unknown"
                ),
                scopes=_scopes(claims),
                # The SDK middleware compares expires_at with no skew of its own.
                expires_at=int(claims["exp"]) + SKEW_S,
                subject=str(claims["sub"]),
                claims=claims,
            )
        except Exception:
            return None

    async def _key(self, kid: str) -> jwt.PyJWK | None:
        if self._clock() >= self._refresh_at:
            async with self._lock:
                if self._clock() >= self._refresh_at:  # another task may have just refreshed
                    await self._fetch_keys()
        if kid not in self._keys and self._clock() >= self._miss_refetch_at:
            # Claim the slot before awaiting, so concurrent misses share one refetch.
            self._miss_refetch_at = self._clock() + REFETCH_INTERVAL_S
            async with self._lock:
                await self._fetch_keys()
        return self._keys.get(kid)

    async def _fetch_keys(self) -> None:
        """Replace the key set; on any failure keep the old keys and retry later."""
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10) as http:
                jwks_uri = await self._discover(http)
                jwks = (await http.get(jwks_uri)).raise_for_status().json()
            keys = {}
            for data in jwks.get("keys", []):
                if data.get("use", "sig") != "sig" or data.get("kty") not in ("RSA", "EC"):
                    continue
                key = jwt.PyJWK(data)
                if key.key_id and key.algorithm_name in ALGORITHMS:
                    keys[key.key_id] = key
        except Exception as e:
            logger.warning("http_auth.keys_unavailable", extra={"error": type(e).__name__})
            self._refresh_at = self._clock() + REFETCH_INTERVAL_S
            return
        self._keys = keys
        self._refresh_at = self._clock() + KEYS_TTL_S

    async def _discover(self, http: httpx.AsyncClient) -> str:
        """`jwks_uri` from OIDC discovery, falling back to RFC 8414 metadata."""
        base = self._issuer.rstrip("/")
        parts = urlsplit(base)
        candidates = (
            f"{base}/.well-known/openid-configuration",
            # RFC 8414 inserts the well-known segment before the issuer's path.
            f"{parts.scheme}://{parts.netloc}/.well-known/oauth-authorization-server{parts.path}",
        )
        for url in candidates:
            response = await http.get(url)
            if response.status_code != 200:
                continue
            metadata = response.json()
            if metadata.get("issuer") != self._issuer:
                raise ValueError("discovery document names a different issuer")
            return str(metadata["jwks_uri"])
        raise ValueError("no discovery document")


def _scopes(claims: dict[str, Any]) -> list[str]:
    """`scope` (space-separated) and `scp` (Entra: string; Okta: list), merged in order."""
    found: list[str] = []
    for claim in ("scope", "scp"):
        value = claims.get(claim)
        items = (
            value.split() if isinstance(value, str) else value if isinstance(value, list) else []
        )
        for item in items:
            if isinstance(item, str) and item not in found:
                found.append(item)
    return found
