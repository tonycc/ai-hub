"""Unit tests for aggregated data query helpers and scope rules (M7-03)."""

from __future__ import annotations

import pytest
from ai_hub_platform.modules.ingest.query import (
    DataQueryValidationError,
    assert_application_readable,
    merge_portal_scope,
    resolve_service_data_scope,
)


def test_assert_application_readable_enforces_scope() -> None:
    assert_application_readable("app-a", allowed_application_ids=None)
    assert_application_readable("app-a", allowed_application_ids=frozenset({"app-a"}))
    with pytest.raises(DataQueryValidationError, match="outside"):
        assert_application_readable(
            "app-b",
            allowed_application_ids=frozenset({"app-a"}),
        )


def test_resolve_service_data_scope_pins_ordinary_apps() -> None:
    assert resolve_service_data_scope(
        caller_application_id="app-a",
        requested_source_application_id=None,
        allow_cross_application=False,
    ) == frozenset({"app-a"})
    with pytest.raises(DataQueryValidationError, match="cannot read another"):
        resolve_service_data_scope(
            caller_application_id="app-a",
            requested_source_application_id="app-b",
            allow_cross_application=False,
        )


def test_resolve_service_data_scope_allows_cross_application_for_ai() -> None:
    assert (
        resolve_service_data_scope(
            caller_application_id="ai-hub-ai",
            requested_source_application_id=None,
            allow_cross_application=True,
        )
        is None
    )
    assert resolve_service_data_scope(
        caller_application_id="ai-hub-ai",
        requested_source_application_id="app-b",
        allow_cross_application=True,
    ) == frozenset({"app-b"})


def test_merge_portal_scope_intersects_request_filter() -> None:
    assert merge_portal_scope(None, None) is None
    assert merge_portal_scope(None, "app-a") == frozenset({"app-a"})
    assert merge_portal_scope(frozenset({"app-a", "app-b"}), "app-a") == frozenset(
        {"app-a"}
    )
    with pytest.raises(DataQueryValidationError, match="outside"):
        merge_portal_scope(frozenset({"app-a"}), "app-b")
