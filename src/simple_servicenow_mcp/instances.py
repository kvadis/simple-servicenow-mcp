"""Multi-instance registry.

When ``SN_INSTANCES_FILE`` points at a JSON file shaped like::

    {
      "default": "prod",
      "instances": {
        "prod":  {"instance_url": "https://acme.service-now.com",     "auth_method": "oauth", "client_id": "...", "client_secret": "..."},
        "dev":   {"instance_url": "https://acmedev.service-now.com",  "auth_method": "basic", "username": "admin", "password": "..."},
        "test":  {"instance_url": "https://acmetest.service-now.com", "auth_method": "basic", "username": "admin", "password": "..."}
      }
    }

…the server starts in multi-instance mode. Tools accept an optional ``instance``
parameter naming one of the keys; omitting it falls back to ``default``. Per-instance
fields override the top-level ``SN_*`` env values, so a single shared credential
can be reused across instances by leaving fields out.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import ServiceNowClient
from .config import Settings

logger = logging.getLogger(__name__)


_OVERRIDABLE_FIELDS = (
    "instance_url",
    "auth_method",
    "username",
    "password",
    "client_id",
    "client_secret",
    "oauth_redirect_uri",
    "oauth_scope",
    "api_timeout",
    "default_page_size",
    "max_page_size",
    "max_retries",
    "retry_base_delay",
    "retry_max_delay",
    "log_level",
    "log_format",
)


@dataclass
class InstanceRegistry:
    """A lazy registry of named ``ServiceNowClient`` instances.

    Clients are created up-front (so misconfiguration surfaces at startup, not
    on first request) and closed together via :meth:`aclose`.
    """

    clients: dict[str, ServiceNowClient]
    default_name: str

    def client_for(self, name: str) -> ServiceNowClient:
        try:
            return self.clients[name]
        except KeyError as e:
            raise KeyError(f"Unknown instance '{name}'. Available: {sorted(self.clients)}") from e

    def names(self) -> list[str]:
        return sorted(self.clients)

    async def aclose(self) -> None:
        for name, client in self.clients.items():
            try:
                await client.close()
            except Exception:
                logger.warning("instance.close.failed", extra={"instance": name})


def _merge(fallback: Settings, overrides: dict[str, Any]) -> Settings:
    """Build a new Settings with per-instance overrides applied to ``fallback``."""
    base = fallback.model_dump()
    for key, value in overrides.items():
        if key not in _OVERRIDABLE_FIELDS:
            raise ValueError(
                f"Unknown instance field '{key}'. Allowed: {list(_OVERRIDABLE_FIELDS)}"
            )
        base[key] = value
    base.pop("instances_file", None)
    return Settings(**base)


def load_instance_settings(path: Path, fallback: Settings) -> tuple[dict[str, Settings], str]:
    """Parse and validate ``instances.json`` into Settings per entry.

    Split out from :func:`load_instances` so the ``login`` CLI can resolve the
    same configuration — with the same validation — without constructing HTTP
    clients it would only throw away.

    Returns ``(settings_by_name, default_name)``.
    """
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or "instances" not in raw:
        raise ValueError(f"{path}: must be a JSON object with an 'instances' map")
    entries: dict[str, dict[str, Any]] = raw["instances"]
    if not entries:
        raise ValueError(f"{path}: 'instances' must not be empty")

    default_name = raw.get("default") or next(iter(entries))
    if default_name not in entries:
        raise ValueError(f"{path}: default '{default_name}' is not in instances {list(entries)}")

    resolved: dict[str, Settings] = {}
    for name, overrides in entries.items():
        if not isinstance(overrides, dict):
            raise ValueError(f"{path}: instance '{name}' must be an object")
        settings = _merge(fallback, overrides)
        if not settings.instance_url:
            raise ValueError(
                f"{path}: instance '{name}' has no instance_url (set it per-entry or via SN_INSTANCE_URL)"
            )
        if settings.auth_method == "oauth_authorization_code" and not settings.client_id:
            # A missing client_id is a config error and cannot be recovered from at
            # runtime, so fail at startup like instance_url does. A missing *token*
            # is different — that is fixed by running `login`, so it stays a warning.
            raise ValueError(
                f"{path}: instance '{name}' uses oauth_authorization_code but has no "
                "client_id (set it per-entry or via SN_CLIENT_ID)"
            )
        resolved[name] = settings

    return resolved, default_name


def load_instances(path: Path, fallback: Settings) -> InstanceRegistry:
    """Load ``instances.json`` and build a client per entry.

    ``fallback`` supplies defaults for any field a per-instance entry omits, so
    shared credentials (e.g. an org-wide OAuth client) can stay in env vars
    while only ``instance_url`` varies per entry.
    """
    resolved, default_name = load_instance_settings(path, fallback)
    clients = {
        name: ServiceNowClient(settings, instance_name=name) for name, settings in resolved.items()
    }
    return InstanceRegistry(clients=clients, default_name=default_name)
