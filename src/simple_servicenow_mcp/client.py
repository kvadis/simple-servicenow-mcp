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

logger = logging.getLogger(__name__)

_HINTS: dict[int, str] = {
    400: "ServiceNow rejected the payload — check field names, value types, and encoded query syntax",
    401: "auth failed — verify SN_USERNAME/SN_PASSWORD or SN_CLIENT_ID/SN_CLIENT_SECRET and that the user is active",
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
    return min(settings.retry_base_delay * (2 ** attempt), settings.retry_max_delay)


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


class ServiceNowClient:
    """Low-level ServiceNow REST client.

    Handles Basic and OAuth 2.0 authentication, pagination clamping,
    and maps to the Table API + Stats API endpoints.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.instance_url.rstrip("/")
        self._oauth_token: str | None = None
        self._token_expiry: float = 0.0
        self._field_cache: dict[str, set[str]] = {}
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.api_timeout),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

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

        # OAuth — refresh if expired
        if self._oauth_token is None or time.time() >= self._token_expiry:
            await self._refresh_oauth_token()
        return {"Authorization": f"Bearer {self._oauth_token}"}

    async def _refresh_oauth_token(self) -> None:
        request_id = uuid.uuid4().hex[:8]
        url = f"{self._base_url}/oauth_token.do"
        data = {
            "grant_type": "client_credentials",
            "client_id": self._settings.client_id,
            "client_secret": self._settings.client_secret,
        }
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
                extra={"request_id": request_id, "status": resp.status_code, "duration_ms": duration_ms},
            )
            raise _extract_error(resp)
        body = resp.json()
        self._oauth_token = body["access_token"]
        self._token_expiry = time.time() + body["expires_in"] - 60
        logger.info(
            "auth.oauth.refreshed",
            extra={"request_id": request_id, "duration_ms": duration_ms, "expires_in_s": body["expires_in"]},
        )

    # ── Internal request helper ─────────────────────────────────────

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a request with retry on 429/5xx/network errors.

        Honours Retry-After on 429 (seconds form), exponential backoff otherwise.
        Emits structured logs with a per-call request_id.
        """
        request_id = uuid.uuid4().hex[:8]
        path = self._path(url)
        max_attempts = self._settings.max_retries + 1
        for attempt in range(max_attempts):
            start = time.monotonic()
            try:
                headers = await self._auth_headers()
                resp = await self._http.request(method, url, headers=headers, **kwargs)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                if attempt + 1 < max_attempts:
                    delay = _retry_delay(None, attempt, self._settings)
                    logger.warning(
                        "api.request.network_retry",
                        extra={
                            "request_id": request_id, "method": method, "path": path,
                            "duration_ms": duration_ms, "attempt": attempt + 1,
                            "retry_in_s": round(delay, 2), "error": type(e).__name__,
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "api.request.network_failed",
                    extra={
                        "request_id": request_id, "method": method, "path": path,
                        "duration_ms": duration_ms, "attempts": attempt + 1, "error": str(e),
                    },
                )
                raise ServiceNowAPIError(
                    0, f"Network error after {attempt + 1} attempts: {type(e).__name__}", str(e), retryable=True
                ) from e

            duration_ms = int((time.monotonic() - start) * 1000)
            if resp.is_error:
                err = _extract_error(resp)
                if err.retryable and attempt + 1 < max_attempts:
                    delay = _retry_delay(resp, attempt, self._settings)
                    logger.warning(
                        "api.request.retry",
                        extra={
                            "request_id": request_id, "method": method, "path": path,
                            "status": resp.status_code, "duration_ms": duration_ms,
                            "attempt": attempt + 1, "retry_in_s": round(delay, 2),
                        },
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "api.request.error",
                    extra={
                        "request_id": request_id, "method": method, "path": path,
                        "status": resp.status_code, "duration_ms": duration_ms,
                        "attempts": attempt + 1,
                    },
                )
                raise err

            logger.info(
                "api.request.ok",
                extra={
                    "request_id": request_id, "method": method, "path": path,
                    "status": resp.status_code, "duration_ms": duration_ms,
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
        effective_limit = min(limit or self._settings.default_page_size, self._settings.max_page_size)
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

        resp = await self._request("GET", f"{self._base_url}/api/now/table/{table}/{sys_id}", params=params)
        return resp.json()["result"]

    async def create_record(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request("POST", f"{self._base_url}/api/now/table/{table}", json=data)
        return resp.json()["result"]

    async def update_record(self, table: str, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request("PATCH", f"{self._base_url}/api/now/table/{table}/{sys_id}", json=data)
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
