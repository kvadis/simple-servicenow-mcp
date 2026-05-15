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


def load_instances(path: Path, fallback: Settings) -> InstanceRegistry:
    """Load ``instances.json`` and build a client per entry.

    ``fallback`` supplies defaults for any field a per-instance entry omits, so
    shared credentials (e.g. an org-wide OAuth client) can stay in env vars
    while only ``instance_url`` varies per entry.
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

    clients: dict[str, ServiceNowClient] = {}
    for name, overrides in entries.items():
        if not isinstance(overrides, dict):
            raise ValueError(f"{path}: instance '{name}' must be an object")
        settings = _merge(fallback, overrides)
        if not settings.instance_url:
            raise ValueError(
                f"{path}: instance '{name}' has no instance_url (set it per-entry or via SN_INSTANCE_URL)"
            )
        clients[name] = ServiceNowClient(settings)

    return InstanceRegistry(clients=clients, default_name=default_name)
