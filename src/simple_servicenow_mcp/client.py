"""Async ServiceNow REST Table API client using httpx."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .token_store import StoredToken, TokenStore, token_key

logger = logging.getLogger(__name__)

_HINTS: dict[int, str] = {
    400: "ServiceNow rejected the payload — check field names, value types, and encoded query syntax",
    401: (
        "auth failed — basic: check SN_USERNAME/SN_PASSWORD; oauth: check "
        "SN_CLIENT_ID/SN_CLIENT_SECRET; oauth_authorization_code: the stored token is "
        "missing or expired, re-run 'simple-servicenow-mcp login' (add --instance <name> in "
        "multi-instance mode). Also confirm the user is active"
    ),
    403: "auth succeeded but the user/role lacks permission for this table or operation",
    404: "record or table not found — verify the sys_id and table name",
    409: "conflict — the record may have been modified concurrently, or a uniqueness constraint failed",
    429: "rate limit hit — back off and retry, optionally raise instance API quota",
}


class ServiceNowAPIError(Exception):
    """Structured ServiceNow REST API error with code, body detail, and a recovery hint.

    `str(err)` returns a single-line, LLM-friendly message; the attributes are
    available for programmatic handling (e.g. retry decisions).
    """

    def __init__(
        self,
        status: int,
        message: str,
        detail: str = "",
        retryable: bool = False,
    ) -> None:
        self.status = status
        self.message = message
        self.detail = detail
        self.retryable = retryable
        super().__init__(self._format())

    def _format(self) -> str:
        parts = [f"ServiceNow API error {self.status}: {self.message}"]
        if self.detail:
            parts.append(f"detail: {self.detail}")
        hint = _HINTS.get(self.status)
        if hint:
            parts.append(f"hint: {hint}")
        if self.retryable:
            parts.append("(retryable)")
        return " | ".join(parts)


def _retry_delay(resp: httpx.Response | None, attempt: int, settings: Settings) -> float:
    """Compute retry wait. Honours Retry-After (seconds form) when present.

    Falls back to exponential backoff ``base * 2^attempt``, capped at
    ``retry_max_delay``. HTTP-date Retry-After is ignored for simplicity.
    """
    if resp is not None:
        ra = resp.headers.get("retry-after")
        if ra:
            try:
                return min(float(ra), settings.retry_max_delay)
            except ValueError:
                pass
    return min(settings.retry_base_delay * (2**attempt), settings.retry_max_delay)


def _extract_error(resp: httpx.Response) -> ServiceNowAPIError:
    """Map a non-2xx response to a structured ServiceNowAPIError.

    ServiceNow returns ``{"error": {"message": ..., "detail": ...}}`` on most
    failures; fall back to body text or reason phrase otherwise.
    """
    message = resp.reason_phrase or "Request failed"
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            message = err.get("message") or message
            detail = err.get("detail") or ""
        elif isinstance(body, dict) and body.get("error_description"):
            # OAuth-style error response
            message = body.get("error", message)
            detail = body.get("error_description", "")
    except ValueError:
        text = resp.text.strip()
        if text:
            detail = text[:500]
    retryable = resp.status_code in (429, 500, 502, 503, 504)
    return ServiceNowAPIError(resp.status_code, message, detail, retryable)


def _is_invalid_grant(resp: httpx.Response) -> bool:
    """True when a token response says the grant itself is dead.

    Checks ``error`` and ``error_description`` together: this instance answers a
    bad grant with ``{"error_description": "access_denied", "error":
    "server_error"}``, which puts the meaningful half in the field RFC 6749 says
    is human-readable. Field placement is not trustworthy, so match on both.
    """
    try:
        body = resp.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    blob = f"{body.get('error', '')} {body.get('error_description', '')}".lower()
    return "invalid_grant" in blob


class ServiceNowClient:
    """Low-level ServiceNow REST client.

    Handles Basic and OAuth 2.0 authentication, pagination clamping,
    and maps to the Table API + Stats API endpoints.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        instance_name: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._instance_name = instance_name
        self._base_url = settings.instance_url.rstrip("/")
        self._oauth_token: str | None = None
        self._token_expiry: float = 0.0
        self._field_cache: dict[str, set[str]] = {}
        # Serialises refresh so concurrent tool calls spend one grant, not N.
        self._refresh_lock = asyncio.Lock()
        # Set by the 401 handler to force a real refresh rather than re-adopting
        # the token the server just rejected.
        self._force_refresh = False
        self._token_store: TokenStore | None = None
        self._token_key = ""
        if settings.auth_method == "oauth_authorization_code":
            self._token_store = TokenStore(settings.token_store)
            self._token_key = token_key(settings.instance_url, settings.client_id)
        # transport=None is httpx's own default, so the production call sites are
        # unaffected; tests pass a MockTransport.
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.api_timeout),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def token_status(self) -> dict[str, Any] | None:
        """Stored-token state, for diagnostics. None unless browser-delegated auth.

        Exposed so the model can tell a user "instance X needs a login" instead of
        only meeting a 401 at call time — a refresh token quietly reaching its
        (default 100-day) expiry is otherwise invisible until everything fails.
        """
        if self._token_store is None:
            return None
        stored = self._token_store.load(self._token_key)
        if stored is None:
            return {"present": False, "needs_login": True}
        return {
            "present": True,
            "expires_in_s": int(stored.expires_in),
            "refreshable": bool(stored.refresh_token),
            "needs_login": stored.is_expired and not stored.refresh_token,
        }

    @property
    def _login_hint(self) -> str:
        cmd = "simple-servicenow-mcp login"
        if self._instance_name:
            cmd += f" --instance {self._instance_name}"
        return f"run: {cmd}"

    @property
    def settings(self) -> Settings:
        """Read-only view of the client's configuration (used by resources/diagnostics)."""
        return self._settings

    async def close(self) -> None:
        await self._http.aclose()

    def _path(self, url: str) -> str:
        """Strip the instance base URL for log brevity."""
        return url.removeprefix(self._base_url) or url

    # ── Auth ────────────────────────────────────────────────────────

    async def _auth_headers(self) -> dict[str, str]:
        if self._settings.auth_method == "basic":
            creds = base64.b64encode(
                f"{self._settings.username}:{self._settings.password}".encode()
            ).decode()
            return {"Authorization": f"Basic {creds}"}

        # OAuth — mint or refresh when missing, stale, or explicitly invalidated.
        if self._oauth_token is None or self._force_refresh or time.time() >= self._token_expiry:
            await self._refresh_oauth_token()
        return {"Authorization": f"Bearer {self._oauth_token}"}

    async def _refresh_oauth_token(self) -> None:
        """Obtain a usable access token, serialised across concurrent callers."""
        async with self._refresh_lock:
            force = self._force_refresh
            self._force_refresh = False
            # Someone else may have refreshed while we waited on the lock.
            if not force and self._oauth_token is not None and time.time() < self._token_expiry:
                return
            if self._settings.auth_method == "oauth_authorization_code":
                await self._refresh_authorization_code_token(force=force)
            else:
                await self._refresh_client_credentials_token()

    def _adopt(self, token: StoredToken) -> None:
        self._oauth_token = token.access_token
        self._token_expiry = token.expires_at

    async def _refresh_client_credentials_token(self) -> None:
        body = await self._token_request(
            {
                "grant_type": "client_credentials",
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
            },
            kind="client_credentials",
        )
        self._oauth_token = body["access_token"]
        self._token_expiry = time.time() + body["expires_in"] - 60

    async def _refresh_authorization_code_token(self, *, force: bool = False) -> None:
        """Load the stored grant, adopting or refreshing it as needed.

        The store is re-read on every call rather than cached, so a `login` run in
        a terminal takes effect in an already-running server without a restart.
        """
        assert self._token_store is not None  # set whenever this method is reachable
        stored = self._token_store.load(self._token_key)
        if stored is None:
            raise ServiceNowAPIError(
                401,
                f"No stored OAuth token for instance '{self._instance_name or self._base_url}'",
                self._login_hint,
            )

        if not stored.is_expired and (not force or stored.access_token != self._oauth_token):
            # Either still valid, or another process already replaced the token the
            # server just rejected — adopt it instead of spending the grant.
            logger.info(
                "auth.token.loaded_from_store",
                extra={"instance": self._instance_name, "expires_in_s": int(stored.expires_in)},
            )
            self._adopt(stored)
            return

        if not stored.refresh_token:
            raise ServiceNowAPIError(
                401,
                "Stored OAuth access token expired and no refresh token is available",
                self._login_hint,
            )

        data = {
            "grant_type": "refresh_token",
            "refresh_token": stored.refresh_token,
            "client_id": self._settings.client_id,
        }
        # A confidential client must authenticate on refresh too; a Public Client
        # (PKCE, no secret) must not send one.
        if self._settings.client_secret:
            data["client_secret"] = self._settings.client_secret

        body = await self._token_request(data, kind="refresh_token")
        refreshed = StoredToken(
            access_token=body["access_token"],
            # ServiceNow may or may not rotate the refresh token depending on
            # release and config — keep the old one when none comes back.
            refresh_token=body.get("refresh_token") or stored.refresh_token,
            expires_at=time.time() + body["expires_in"] - 60,
            instance_name=stored.instance_name or self._instance_name,
            scope=body.get("scope", stored.scope),
            obtained_at=time.time(),
        )
        self._token_store.save(self._token_key, refreshed)
        self._adopt(refreshed)

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> StoredToken:
        """Trade an authorization code for tokens and persist them.

        Called by the ``login`` CLI, not during normal serving. Shares
        :meth:`_token_request` with the refresh path so both get the same error
        mapping and logging.
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            # ServiceNow validates redirect_uri again at exchange time; it has to
            # be byte-identical to the one in the authorize request.
            "redirect_uri": redirect_uri,
            "client_id": self._settings.client_id,
            "code_verifier": code_verifier,
        }
        if self._settings.client_secret:
            data["client_secret"] = self._settings.client_secret

        body = await self._token_request(data, kind="authorization_code")
        token = StoredToken(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=time.time() + body["expires_in"] - 60,
            instance_name=self._instance_name,
            scope=body.get("scope", ""),
            obtained_at=time.time(),
        )
        if self._token_store is not None:
            self._token_store.save(self._token_key, token)
        self._adopt(token)
        return token

    async def _token_request(self, data: dict[str, str], *, kind: str) -> dict[str, Any]:
        """POST to /oauth_token.do. Deliberately outside the retry loop — a bad
        grant should surface at once rather than after three backoffs."""
        request_id = uuid.uuid4().hex[:8]
        url = f"{self._base_url}/oauth_token.do"
        start = time.monotonic()
        resp = await self._http.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        if resp.is_error:
            logger.warning(
                "auth.oauth.refresh_failed",
                extra={
                    "request_id": request_id,
                    "grant": kind,
                    "status": resp.status_code,
                    "duration_ms": duration_ms,
                },
            )
            if _is_invalid_grant(resp):
                raise ServiceNowAPIError(
                    401,
                    "ServiceNow rejected the refresh token (invalid_grant) — it has "
                    "expired or been revoked",
                    self._login_hint,
                )
            raise _extract_error(resp)
        body: dict[str, Any] = resp.json()
        logger.info(
            "auth.oauth.refreshed",
            extra={
                "request_id": request_id,
                "grant": kind,
                "duration_ms": duration_ms,
                "expires_in_s": body["expires_in"],
            },
        )
        return body

    # ── Internal request helper ─────────────────────────────────────

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a request with retry on 429/5xx/network errors.

        Honours Retry-After on 429 (seconds form), exponential backoff otherwise.
        Emits structured logs with a per-call request_id.
        """
        request_id = uuid.uuid4().hex[:8]
        path = self._path(url)
        base_attempts = self._settings.max_retries + 1
        # One extra iteration is reserved for the retry that carries a *refreshed*
        # token — max_retries=0 is legal, and without the reservation a 401 would
        # eat the only slot and the re-auth would never run. It is reserved for
        # the loop bound but only becomes spendable once a 401 has actually
        # fired: ordinary 429/5xx retries must keep exactly the budget they had
        # before, whatever the auth method.
        total_attempts = base_attempts + (0 if self._settings.auth_method == "basic" else 1)
        auth_retried = False
        for attempt in range(total_attempts):
            max_attempts = base_attempts + (1 if auth_retried else 0)
            start = time.monotonic()
            try:
                headers = await self._auth_headers()
                resp = await self._http.request(method, url, headers=headers, **kwargs)
            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.RemoteProtocolError,
            ) as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                if attempt + 1 < max_attempts:
                    delay = _retry_delay(None, attempt, self._settings)
                    logger.warning(
                        "api.request.network_retry",
                        extra={
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "duration_ms": duration_ms,
                            "attempt": attempt + 1,
                            "retry_in_s": round(delay, 2),
                            "error": type(e).__name__,
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "api.request.network_failed",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "duration_ms": duration_ms,
                        "attempts": attempt + 1,
                        "error": str(e),
                    },
                )
                raise ServiceNowAPIError(
                    0,
                    f"Network error after {attempt + 1} attempts: {type(e).__name__}",
                    str(e),
                    retryable=True,
                ) from e

            duration_ms = int((time.monotonic() - start) * 1000)
            if resp.is_error:
                if (
                    resp.status_code == 401
                    and self._settings.auth_method != "basic"
                    and not auth_retried
                    and attempt + 1 < total_attempts
                ):
                    # Once only, and never for basic auth — a bad password should
                    # fail fast rather than loop. 401 stays out of the retryable
                    # set so genuine permission errors are not retried either.
                    auth_retried = True
                    self._force_refresh = True
                    logger.warning(
                        "api.request.auth_retry",
                        extra={
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "duration_ms": duration_ms,
                            "attempt": attempt + 1,
                        },
                    )
                    continue
                err = _extract_error(resp)
                if err.retryable and attempt + 1 < max_attempts:
                    delay = _retry_delay(resp, attempt, self._settings)
                    logger.warning(
                        "api.request.retry",
                        extra={
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "status": resp.status_code,
                            "duration_ms": duration_ms,
                            "attempt": attempt + 1,
                            "retry_in_s": round(delay, 2),
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "api.request.error",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "status": resp.status_code,
                        "duration_ms": duration_ms,
                        "attempts": attempt + 1,
                    },
                )
                raise err

            logger.info(
                "api.request.ok",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status": resp.status_code,
                    "duration_ms": duration_ms,
                    "attempts": attempt + 1,
                },
            )
            return resp

        # Unreachable: loop always exits via return or raise.
        raise ServiceNowAPIError(0, "Retry loop exited without resolution")

    # ── Table API ───────────────────────────────────────────────────

    async def list_records(
        self,
        table: str,
        *,
        query: str | None = None,
        fields: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        display_value: str = "false",
    ) -> list[dict[str, Any]]:
        effective_limit = min(
            limit or self._settings.default_page_size, self._settings.max_page_size
        )
        params: dict[str, str] = {
            "sysparm_limit": str(effective_limit),
            "sysparm_offset": str(offset),
            "sysparm_display_value": display_value,
            "sysparm_exclude_reference_link": "true",
        }
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = fields

        resp = await self._request("GET", f"{self._base_url}/api/now/table/{table}", params=params)
        return resp.json()["result"]

    async def get_record(
        self,
        table: str,
        sys_id: str,
        *,
        fields: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {
            "sysparm_display_value": "false",
            "sysparm_exclude_reference_link": "true",
        }
        if fields:
            params["sysparm_fields"] = fields

        resp = await self._request(
            "GET", f"{self._base_url}/api/now/table/{table}/{sys_id}", params=params
        )
        return resp.json()["result"]

    async def create_record(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request("POST", f"{self._base_url}/api/now/table/{table}", json=data)
        return resp.json()["result"]

    async def update_record(self, table: str, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request(
            "PATCH", f"{self._base_url}/api/now/table/{table}/{sys_id}", json=data
        )
        return resp.json()["result"]

    async def delete_record(self, table: str, sys_id: str) -> None:
        await self._request("DELETE", f"{self._base_url}/api/now/table/{table}/{sys_id}")

    async def get_count(self, table: str, query: str | None = None) -> int:
        params: dict[str, str] = {"sysparm_count": "true"}
        if query:
            params["sysparm_query"] = query

        resp = await self._request("GET", f"{self._base_url}/api/now/stats/{table}", params=params)
        return int(resp.json()["result"]["stats"]["count"])

    # ── Attachment API ──────────────────────────────────────────────

    async def attachment_metadata(self, sys_id: str) -> dict[str, Any]:
        """Fetch the metadata for one attachment from ``sys_attachment``."""
        resp = await self._request("GET", f"{self._base_url}/api/now/attachment/{sys_id}")
        return resp.json()["result"]

    async def attachment_download(self, sys_id: str) -> tuple[bytes, str]:
        """Download an attachment's binary payload.

        Returns ``(body_bytes, content_type)``. Callers decide whether to decode
        as text or base64-encode based on ``content_type`` and ``size``.
        """
        resp = await self._request(
            "GET",
            f"{self._base_url}/api/now/attachment/{sys_id}/file",
            headers={"Accept": "*/*"},
        )
        return resp.content, resp.headers.get("Content-Type", "application/octet-stream")

    async def attachment_upload(
        self,
        table_name: str,
        table_sys_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """Upload a file as an attachment to ``table_name/table_sys_id``.

        Uses the multipart endpoint at ``/api/now/attachment/file`` (preferred
        over ``/upload`` — fewer required form fields).
        """
        params = {
            "table_name": table_name,
            "table_sys_id": table_sys_id,
            "file_name": file_name,
        }
        resp = await self._request(
            "POST",
            f"{self._base_url}/api/now/attachment/file",
            params=params,
            content=content,
            headers={"Content-Type": content_type, "Accept": "application/json"},
        )
        return resp.json()["result"]

    async def aggregate(
        self,
        table: str,
        query: str | None = None,
        count: bool = True,
        sum_fields: str | None = None,
        avg_fields: str | None = None,
        min_fields: str | None = None,
        max_fields: str | None = None,
        group_by: str | None = None,
        having: str | None = None,
        display_value: str = "false",
    ) -> Any:
        """Stats API wrapper supporting count + sum/avg/min/max + GROUP BY.

        Returns the raw ``result`` payload — when ``group_by`` is set this is a
        list of group buckets, otherwise it's a single object with ``stats``.
        """
        params: dict[str, str] = {"sysparm_display_value": display_value}
        if count:
            params["sysparm_count"] = "true"
        if query:
            params["sysparm_query"] = query
        if sum_fields:
            params["sysparm_sum_fields"] = sum_fields
        if avg_fields:
            params["sysparm_avg_fields"] = avg_fields
        if min_fields:
            params["sysparm_min_fields"] = min_fields
        if max_fields:
            params["sysparm_max_fields"] = max_fields
        if group_by:
            params["sysparm_group_by"] = group_by
        if having:
            params["sysparm_having"] = having

        resp = await self._request("GET", f"{self._base_url}/api/now/stats/{table}", params=params)
        return resp.json()["result"]

    # ── Schema discovery (cached) ───────────────────────────────────

    async def get_table_field_names(self, table: str) -> set[str] | None:
        """Return all valid field names for ``table`` (incl. inherited).

        Walks ``sys_db_object.super_class`` to assemble the full inheritance
        chain, then bulk-fetches ``sys_dictionary`` entries for every level.
        Results are cached per-instance.

        Returns ``None`` when discovery fails (e.g. the credential lacks read
        access on ``sys_dictionary``); callers should treat ``None`` as
        "validation unavailable, send the request anyway".
        """
        if table in self._field_cache:
            return self._field_cache[table]
        try:
            chain = await self._collect_table_chain(table)
            if not chain:
                return None
            entries = await self.list_records(
                "sys_dictionary",
                query=f"nameIN{','.join(chain)}^elementISNOTEMPTY",
                fields="element",
                limit=2000,
            )
            fields = {e["element"] for e in entries if e.get("element")}
            self._field_cache[table] = fields
            logger.info(
                "schema.discovered",
                extra={"table": table, "chain": chain, "field_count": len(fields)},
            )
            return fields
        except ServiceNowAPIError as e:
            logger.warning(
                "schema.discover_failed",
                extra={"table": table, "status": e.status, "message": e.message},
            )
            return None

    async def _collect_table_chain(self, table: str) -> list[str]:
        """Walk sys_db_object.super_class upward; returns [table, parent, ...]."""
        chain: list[str] = []
        visited: set[str] = set()
        current: str | None = table
        while current and current not in visited:
            chain.append(current)
            visited.add(current)
            rec = await self.list_records(
                "sys_db_object",
                query=f"name={current}",
                fields="super_class.name",
                limit=1,
            )
            if not rec:
                break
            parent = rec[0].get("super_class.name")
            if isinstance(parent, dict):  # defensive for display_value variants
                parent = parent.get("value") or parent.get("display_value")
            current = parent or None
        return chain
