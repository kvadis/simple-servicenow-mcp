"""Environment-based configuration using pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ServiceNow MCP server configuration.

    All values are loaded from environment variables prefixed with ``SN_``
    (e.g. ``SN_INSTANCE_URL``) or from a ``.env`` file.
    """

    model_config = SettingsConfigDict(env_prefix="SN_", env_file=".env", extra="ignore")

    instance_url: str = Field(
        default="",
        description="ServiceNow instance URL (e.g. https://dev12345.service-now.com). May be empty when SN_INSTANCES_FILE is set.",
    )
    auth_method: Literal["basic", "oauth"] = Field(
        default="basic", description="Authentication method"
    )

    # Multi-instance registry
    instances_file: Path | None = Field(
        default=None,
        description="Path to instances.json — when set, the server runs in multi-instance mode and tools accept an optional `instance` parameter.",
    )

    # Safety
    read_only: bool = Field(
        default=False,
        description="When True, any tool that mutates ServiceNow (create/update/delete/comment/resolve) refuses the call with a clear error. Set via env SN_READ_ONLY=true or CLI --read-only.",
    )

    # Basic auth
    username: str = Field(default="", description="Basic auth username")
    password: str = Field(default="", description="Basic auth password")

    # OAuth 2.0 client credentials
    client_id: str = Field(default="", description="OAuth client ID")
    client_secret: str = Field(default="", description="OAuth client secret")

    # Tuning
    api_timeout: int = Field(default=30, description="HTTP timeout in seconds")
    default_page_size: int = Field(
        default=20, ge=1, le=100, description="Default page size for list queries"
    )
    max_page_size: int = Field(default=100, ge=1, le=1000, description="Maximum allowed page size")

    # Retry behaviour
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Max retry attempts for retryable failures (429, 5xx, network)",
    )
    retry_base_delay: float = Field(
        default=1.0, ge=0.0, le=30.0, description="Base delay in seconds for exponential backoff"
    )
    retry_max_delay: float = Field(
        default=60.0, ge=1.0, le=300.0, description="Cap on any single retry wait, in seconds"
    )

    # Logging
    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    log_format: Literal["text", "json"] = Field(default="text", description="Log format")
