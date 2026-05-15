"""Tests for SN_TOOL_PACKAGES parsing."""

from __future__ import annotations

import pytest

from simple_servicenow_mcp.packages import PACKAGES, parse_packages


def test_unset_returns_all_modules() -> None:
    assert parse_packages(None) == set(PACKAGES.values())


def test_empty_string_returns_all_modules() -> None:
    assert parse_packages("") == set(PACKAGES.values())


def test_whitespace_only_returns_all_modules() -> None:
    assert parse_packages("   ") == set(PACKAGES.values())


def test_all_sentinel_returns_all_modules() -> None:
    assert parse_packages("all") == set(PACKAGES.values())


def test_single_package_returns_its_module() -> None:
    assert parse_packages("cmdb") == {"cmdb"}


def test_comma_separated_returns_union() -> None:
    assert parse_packages("core,itsm") == {"table", "incident"}


def test_whitespace_around_names_tolerated() -> None:
    assert parse_packages(" core , cmdb , knowledge ") == {"table", "cmdb", "knowledge"}


def test_uppercase_normalised_to_lower() -> None:
    assert parse_packages("CORE,ITSM") == {"table", "incident"}


def test_unknown_package_raises_with_helpful_message() -> None:
    with pytest.raises(ValueError, match="Unknown SN_TOOL_PACKAGES.*notathing"):
        parse_packages("core,notathing")


def test_all_combined_with_named_still_returns_everything() -> None:
    """`all` short-circuits — explicit names alongside it don't narrow the set."""
    assert parse_packages("core,all") == set(PACKAGES.values())


def test_every_package_name_maps_to_a_module() -> None:
    """No orphan keys — every package label resolves to a tools/<module>.py."""
    for label, module in PACKAGES.items():
        assert isinstance(label, str) and label
        assert isinstance(module, str) and module


def test_core_package_maps_to_table() -> None:
    """`core` should be the foundational Table API — verify the mapping isn't drifting."""
    assert PACKAGES["core"] == "table"
