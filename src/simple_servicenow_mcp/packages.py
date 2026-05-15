"""Tool-package gating.

Parses ``SN_TOOL_PACKAGES`` at module-import time and decides which tool
modules ``server.py`` should import. Decorators in those modules register on
``mcp`` at import time, so *not* importing a module is the cleanest way to
keep its tools off the registered list.

Available packages map 1:1 to ``tools/<name>.py`` modules. ``all`` is a
sentinel meaning "everything"; an empty/unset env var loads a curated
default subset (the "developer analytical" loop) rather than every tool —
keeps the LLM's tool list compact for the common case. Pass ``all`` to
opt into every module.
"""

from __future__ import annotations

import os

# package name → tools/<module>.py
PACKAGES: dict[str, str] = {
    "core": "table",  # Generic Table API — the foundational layer
    "itsm": "incident",
    "scripts": "script",
    "catalog": "catalog",
    "cmdb": "cmdb",
    "knowledge": "knowledge",
    "attachment": "attachment",
    "update_set": "update_set",
    "audit": "audit",
}

# Default subset loaded when SN_TOOL_PACKAGES is unset. Picks the
# "developer analytical" loop: generic Table reads + ITSM + script
# reading + catalog UX + scope audits. Domain-specific modules (cmdb,
# knowledge, attachment, update_set) are opt-in via SN_TOOL_PACKAGES or
# the ``all`` sentinel.
DEFAULT_PACKAGES: tuple[str, ...] = ("core", "itsm", "scripts", "catalog", "audit")

_ALL = "all"
_VALID = set(PACKAGES) | {_ALL}


def _default_modules() -> set[str]:
    return {PACKAGES[name] for name in DEFAULT_PACKAGES}


def parse_packages(raw: str | None) -> set[str]:
    """Return the set of tool-module names to import.

    ``raw`` is the value of ``SN_TOOL_PACKAGES``.

    - empty / unset → curated default subset (see ``DEFAULT_PACKAGES``)
    - ``all`` → every module
    - comma-separated names → just those modules; unknown names raise

    The return value is a set of *module names* (``"incident"``,
    ``"cmdb"``), not package names — that's what ``server.py`` needs.
    """
    cleaned = (raw or "").strip().lower()
    if not cleaned:
        return _default_modules()
    if cleaned == _ALL:
        return set(PACKAGES.values())

    selected = {p.strip() for p in cleaned.split(",") if p.strip()}
    unknown = selected - _VALID
    if unknown:
        raise ValueError(
            f"Unknown SN_TOOL_PACKAGES values: {sorted(unknown)}. "
            f"Valid: {sorted(PACKAGES)} (or 'all')"
        )

    if _ALL in selected:
        return set(PACKAGES.values())
    return {PACKAGES[name] for name in selected}


def selected_modules_from_env() -> set[str]:
    """Read ``SN_TOOL_PACKAGES`` from the environment and parse it."""
    return parse_packages(os.environ.get("SN_TOOL_PACKAGES"))
