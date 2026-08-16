"""Unit tests for ingest reconcile / replay drift detection (M7-05)."""

from __future__ import annotations

from ai_hub_platform.modules.ingest.reconcile import (
    ChangeLogEntry,
    StateFingerprint,
    compare_current_state,
    ingest_records_from_replay,
    replay_change_log,
)
from ai_hub_platform.modules.ingest.service import payload_content_hash


def test_replay_change_log_folds_upsert_delete_and_skips_stale() -> None:
    entries = [
        ChangeLogEntry("A", "upsert", 1, {"name": "a1"}, "v1"),
        ChangeLogEntry("B", "upsert", 2, {"name": "b1"}, "v1"),
        ChangeLogEntry("A", "upsert", 3, {"name": "a2"}, "v1"),
        ChangeLogEntry("A", "upsert", 2, {"name": "stale"}, "v1"),  # stale
        ChangeLogEntry("B", "delete", 4, None, "v1"),
    ]
    expected = replay_change_log(entries)
    assert set(expected) == {"A"}
    assert expected["A"].version == 3
    assert expected["A"].content_hash == payload_content_hash({"name": "a2"})
    records = ingest_records_from_replay(entries)
    assert len(records) == 1
    assert records[0].object_id == "A"
    assert records[0].payload == {"name": "a2"}


def test_compare_current_state_detects_missing_unexpected_and_hash_drift() -> None:
    expected = {
        "A": StateFingerprint("A", 3, payload_content_hash({"name": "a2"}), "v1"),
        "C": StateFingerprint("C", 5, payload_content_hash({"name": "c"}), "v1"),
    }
    actual = {
        "A": StateFingerprint("A", 3, payload_content_hash({"name": "wrong"}), "v1"),
        "B": StateFingerprint("B", 1, payload_content_hash({"name": "ghost"}), "v1"),
    }
    report = compare_current_state(
        source_application_id="standalone-example",
        object_type="example_record",
        expected=expected,
        actual=actual,
    )
    assert report.drifted is True
    assert report.expected_count == 2
    assert report.actual_count == 2
    kinds = {drift.kind for drift in report.drifts}
    assert kinds == {"hash_mismatch", "missing", "unexpected"}
    assert report.as_dict()["drift_count"] == 3


def test_compare_current_state_clean() -> None:
    fingerprint = StateFingerprint("A", 1, payload_content_hash({"n": 1}), "v1")
    report = compare_current_state(
        source_application_id="app",
        object_type="device",
        expected={"A": fingerprint},
        actual={"A": fingerprint},
    )
    assert report.drifted is False
    assert report.drifts == ()
