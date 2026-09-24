"""Persistent OAuth token storage for the authorization-code flow.

Client-credentials tokens are cheap to re-mint, so they live in memory. An
authorization-code grant is different: it costs a browser round-trip and a human,
so the refresh token has to survive a restart. This module is that persistence.

The file is shaped::

    {
      "version": 1,
      "tokens": {
        "acmedev.service-now.com|<client_id>": {
          "instance_name": "acme-dev",
          "access_token": "...",
          "refresh_token": "...",
          "expires_at": 1772345678.0,
          "scope": "useraccount",
          "obtained_at": 1772343878.0
        }
      }
    }

Three decisions worth knowing:

**Keyed by** ``host|client_id``, not by instance name. Single-instance mode has no
name at all; renaming an ``instances.json`` entry would orphan a perfectly good
token; and two entries pointing at the same instance with the same client should
share one grant. The instance name is kept *inside* the record, as metadata.

**Located under the user's home** (``$XDG_CONFIG_HOME`` or ``~/.config``), never
relative to the working directory. The ``login`` CLI is run from a terminal while
the server is spawned by an MCP client with a different cwd — a relative path
would give them two different stores and the login would appear to do nothing.

**Never raises on a damaged file.** ``load`` is called during client construction,
so an exception there would take down startup for every configured instance
rather than just the one with the bad record. A corrupt store degrades to
"no token", which surfaces as an actionable "run login" error at call time.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

STORE_VERSION = 1

_TMP_SUFFIX = ".tmp"


def default_store_path() -> Path:
    """``$XDG_CONFIG_HOME``/simple-servicenow-mcp/tokens.json, else ``~/.config/…``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "simple-servicenow-mcp" / "tokens.json"


def token_key(instance_url: str, client_id: str) -> str:
    """Stable store key for an instance + OAuth client pair.

    Normalises scheme, case and trailing slash so the same instance written three
    different ways in config still resolves to one grant.
    """
    parts = urlsplit(instance_url if "//" in instance_url else f"//{instance_url}")
    host = (parts.netloc or parts.path).strip("/").lower()
    return f"{host}|{client_id}"


@dataclass
class StoredToken:
    """One instance's tokens. ``expires_at`` is absolute epoch seconds.

    Absolute rather than a duration so a restart doesn't reset the clock, and
    already skew-adjusted by the caller the same way the in-memory cache is.
    """

    access_token: str
    refresh_token: str | None
    expires_at: float
    instance_name: str = ""
    scope: str = ""
    obtained_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def expires_in(self) -> float:
        """Seconds until expiry, floored at zero."""
        return max(0.0, self.expires_at - time.time())


class TokenStore:
    """Reads and writes :class:`StoredToken` records, atomically and privately."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()

    # ── public API ──────────────────────────────────────────────────

    def load(self, key: str) -> StoredToken | None:
        """Return the token for ``key``, or None if absent, damaged or malformed."""
        record = self._read_all().get(key)
        if not isinstance(record, dict):
            return None
        known = {f.name for f in fields(StoredToken)}
        try:
            return StoredToken(**{k: v for k, v in record.items() if k in known})
        except TypeError:
            # Missing a required field — treat as absent rather than fatal so one
            # bad record cannot take out the others.
            logger.warning("auth.token.record_malformed", extra={"key": key})
            return None

    def save(self, key: str, token: StoredToken) -> None:
        data = self._read_all()
        data[key] = asdict(token)
        self._write_all(data)

    def delete(self, key: str) -> bool:
        """Drop ``key``. Returns True if something was removed."""
        data = self._read_all()
        if key not in data:
            return False
        del data[key]
        self._write_all(data)
        return True

    # ── file handling ───────────────────────────────────────────────

    def _read_all(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as e:
            logger.warning(
                "auth.token.store_unreadable",
                extra={"path": str(self.path), "error": str(e)},
            )
            return {}
        if not isinstance(raw, dict):
            logger.warning("auth.token.store_malformed", extra={"path": str(self.path)})
            return {}
        tokens = raw.get("tokens")
        if not isinstance(tokens, dict):
            logger.warning("auth.token.store_malformed", extra={"path": str(self.path)})
            return {}
        return tokens

    def _write_all(self, tokens: dict[str, Any]) -> None:
        """Write the whole store atomically, 0600, into a 0700 directory."""
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir's mode is ignored when the directory already exists, and umask can
        # trim it when it doesn't — so set it explicitly either way.
        os.chmod(directory, 0o700)

        payload = json.dumps({"version": STORE_VERSION, "tokens": tokens}, indent=2)
        tmp = directory / f"{self.path.name}{_TMP_SUFFIX}"
        # O_EXCL + an explicit mode: created private, never briefly world-readable.
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_TRUNC, 0o600)
        except FileExistsError:
            os.unlink(tmp)
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            # Leave the previous store intact and take the temp file with us.
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
