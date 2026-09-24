"""FastMCP server entry point with lifespan-managed ServiceNow client."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .client import ServiceNowClient
from .config import Settings
from .instances import InstanceRegistry, load_instances
from .logging_setup import setup_logging
from .packages import selected_modules_from_env


@dataclass
class AppContext:
    client: ServiceNowClient
    settings: Settings
    registry: InstanceRegistry | None = None
    _extra_clients: list[ServiceNowClient] = field(default_factory=list)

    def client_for(self, instance: str | None) -> ServiceNowClient:
        """Return the client for a named instance, or the default if ``instance`` is None.

        Raises ``ToolError`` when ``instance`` is provided but no registry is configured,
        or the name is not in the registry.
        """
        if instance is None:
            return self.client
        if self.registry is None:
            raise ToolError(
                f"instance='{instance}' requested but multi-instance is not configured. "
                "Set SN_INSTANCES_FILE to enable named instances."
            )
        try:
            return self.registry.client_for(instance)
        except KeyError as e:
            raise ToolError(str(e)) from e

    def ensure_writable(self, op: str) -> None:
        """Raise ``ToolError`` if the server is running in read-only mode.

        ``op`` is the human-readable name of the operation (used in the error).
        """
        if self.settings.read_only:
            raise ToolError(
                f"refused: server is running in read-only mode (operation: {op}). "
                "Read-only is the default; restart with --read-write or "
                "SN_READ_ONLY=false to allow mutations."
            )


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialise the ServiceNow client on startup, close on shutdown."""
    settings = Settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    log = logging.getLogger(__name__)

    registry: InstanceRegistry | None = None
    if settings.instances_file:
        registry = load_instances(settings.instances_file, fallback=settings)
        log.info(
            "server.startup",
            extra={
                "mode": "multi-instance",
                "instances": registry.names(),
                "default": registry.default_name,
                "tool_packages": sorted(_selected_tool_modules),
            },
        )
        default_client = registry.client_for(registry.default_name)
    else:
        log.info(
            "server.startup",
            extra={
                "mode": "single-instance",
                "instance": settings.instance_url,
                "auth_method": settings.auth_method,
                "tool_packages": sorted(_selected_tool_modules),
            },
        )
        default_client = ServiceNowClient(settings)

    try:
        yield AppContext(client=default_client, settings=settings, registry=registry)
    finally:
        log.info("server.shutdown")
        if registry is not None:
            await registry.aclose()
        else:
            await default_client.close()


mcp = FastMCP(
    "simple-servicenow-mcp",
    lifespan=lifespan,
)

# Import tool/resource/prompt modules so their decorators register on `mcp`.
# Resources and prompts are always available; tool modules are gated by
# SN_TOOL_PACKAGES (default: all). See packages.py for the package map.
from . import prompts as _prompts  # noqa: E402, F401
from . import resources as _resources  # noqa: E402, F401

_selected_tool_modules = selected_modules_from_env()

if "table" in _selected_tool_modules:
    from .tools import table as _table  # noqa: F401
if "incident" in _selected_tool_modules:
    from .tools import incident as _incident  # noqa: F401
if "script" in _selected_tool_modules:
    from .tools import script as _script  # noqa: F401
if "catalog" in _selected_tool_modules:
    from .tools import catalog as _catalog  # noqa: F401
if "cmdb" in _selected_tool_modules:
    from .tools import cmdb as _cmdb  # noqa: F401
if "knowledge" in _selected_tool_modules:
    from .tools import knowledge as _knowledge  # noqa: F401
if "attachment" in _selected_tool_modules:
    from .tools import attachment as _attachment  # noqa: F401
if "update_set" in _selected_tool_modules:
    from .tools import update_set as _update_set  # noqa: F401
if "audit" in _selected_tool_modules:
    from .tools import audit as _audit  # noqa: F401


#: Handled by auth_cli, not by the server's own parser.
AUTH_SUBCOMMANDS = ("login", "logout", "auth-status")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="simple-servicenow-mcp")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "sse"),
        default="stdio",
        help="MCP transport. 'stdio' (default) for local clients; 'http' (streamable HTTP) "
        "or 'sse' for remote/multi-client deployments.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="HTTP bind host (only used with --transport=http|sse)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP bind port (only used with --transport=http|sse)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--read-only",
        action="store_true",
        help="Refuse all mutating tool calls (create/update/delete/comment/resolve). "
        "This is the default; the flag exists to override SN_READ_ONLY=false.",
    )
    mode.add_argument(
        "--read-write",
        action="store_true",
        help="Allow mutating tool calls. Equivalent to SN_READ_ONLY=false.",
    )
    return parser.parse_args(argv)


def _read_only_from_env() -> bool:
    raw = os.environ.get("SN_READ_ONLY", "true").strip().lower()
    return raw not in ("false", "0", "no", "off", "f", "n")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point — runs the MCP server over stdio or HTTP."""
    # Auth subcommands are dispatched before argparse: the server's parser has no
    # subparsers and would reject a bare `login`. Keeping one console script means
    # the same launch command — and the cwd it sets up for .env — works for both.
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in AUTH_SUBCOMMANDS:
        from .auth_cli import main as auth_main

        raise SystemExit(auth_main(argv))

    args = _parse_args(argv)
    if args.read_write:
        os.environ["SN_READ_ONLY"] = "false"
    elif args.read_only:
        os.environ["SN_READ_ONLY"] = "true"
    mode = "read-only" if _read_only_from_env() else "read-write"
    print(
        f"simple-servicenow-mcp starting on {args.transport} ({mode})…",
        file=sys.stderr,
    )
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:  # sse
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")


if __name__ == "__main__":
    main()
