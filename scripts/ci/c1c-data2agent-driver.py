"""Drive the locked data2agent adapter against a live AI Hub C1-C stack."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import tomllib
import urllib.error
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import data2agent
from data2agent.middle.extract import ai_hub_object_push_sink as push_module
from data2agent.middle.extract.ai_hub_object_map import ObjectBinding
from data2agent.middle.extract.ai_hub_object_push_sink import (
    AiHubObjectPushSink,
    AiHubProtocolError,
    AiHubPushRejected,
    oidc_client_credentials_provider,
)
from data2agent.shared.store.table import TableInfo

SOURCE_APPLICATION_ID = "standalone-example"
TABLE_KEYS = {
    "ITEM": ["ITEM_CODE"],
    "SALES_ORDER": ["ORDER_NO"],
    "SALES_ORDER_D": ["ORDER_NO", "LINE_NO"],
}

INITIAL_ITEM_PAGES: tuple[tuple[str, list[dict[str, object]]], ...] = (
    (
        "item-full-page-1",
        [
            {"ITEM_CODE": "I-1", "ITEM_NAME": "Widget", "DELETED": 0},
            {"ITEM_CODE": "I-2", "ITEM_NAME": "Part", "DELETED": 0},
        ],
    ),
    (
        "item-full-page-2",
        [{"ITEM_CODE": "I-3", "ITEM_NAME": "Bolt", "DELETED": 0}],
    ),
)

# A restarted extraction run receives a fresh table_batch_id in data2agent's
# scheduler, so the retried pages must have new external batch identities even
# when their source rows are unchanged.
RESTARTED_ITEM_PAGES: tuple[tuple[str, list[dict[str, object]]], ...] = tuple(
    (batch_id.replace("item-full-", "item-full-restart-"), rows)
    for batch_id, rows in INITIAL_ITEM_PAGES
)

OTHER_OBJECT_PAGES: dict[str, tuple[tuple[str, list[dict[str, object]]], ...]] = {
    "SALES_ORDER": (
        (
            "order-full-page-1",
            [
                {
                    "ORDER_NO": "SO-1",
                    "CUSTOMER_CODE": "C-1",
                    "ORDER_STATUS": "OPEN",
                },
                {
                    "ORDER_NO": "SO-2",
                    "CUSTOMER_CODE": "C-2",
                    "ORDER_STATUS": "OPEN",
                },
            ],
        ),
    ),
    "SALES_ORDER_D": (
        (
            "line-full-page-1",
            [
                {
                    "ORDER_NO": "SO-1",
                    "LINE_NO": "10",
                    "ITEM_CODE": "I-1",
                    "QTY": "2",
                },
                {
                    "ORDER_NO": "SO-1",
                    "LINE_NO": "20",
                    "ITEM_CODE": "I-3",
                    "QTY": "5",
                },
            ],
        ),
    ),
}

INCREMENTAL_ITEM_ROWS = [
    {"ITEM_CODE": "I-1", "ITEM_NAME": "Widget v2", "DELETED": 0},
    {"ITEM_CODE": "I-2", "ITEM_NAME": "Part", "DELETED": 1},
    {"ITEM_CODE": "I-4", "ITEM_NAME": "Nut", "DELETED": 0},
]

REBUILT_ITEM_PAGES: tuple[tuple[str, list[dict[str, object]]], ...] = (
    (
        "item-rebuild-page-1",
        [
            {"ITEM_CODE": "I-1", "ITEM_NAME": "Widget v2", "DELETED": 0},
            {"ITEM_CODE": "I-4", "ITEM_NAME": "Nut recovered", "DELETED": 0},
        ],
    ),
    (
        "item-rebuild-page-2",
        [{"ITEM_CODE": "I-5", "ITEM_NAME": "Washer", "DELETED": 0}],
    ),
)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _load_lock() -> dict[str, Any]:
    path = Path(_required_env("C1C_INTEGRATION_LOCK"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("source_application_id") != SOURCE_APPLICATION_ID:
        raise RuntimeError("integration lock source_application_id drifted")
    if payload.get("ai_hub", {}).get("push_protocol_version") != "1":
        raise RuntimeError("integration lock does not select PUSH_AGENT v1")
    expected_version = str(payload.get("data2agent", {}).get("package_version") or "")
    actual_version = _data2agent_version()
    if actual_version != expected_version:
        raise RuntimeError(
            f"data2agent package version drifted: {actual_version} != {expected_version}"
        )
    expected_adapter = str(payload.get("data2agent", {}).get("adapter") or "")
    actual_adapter = f"{AiHubObjectPushSink.__module__}.{AiHubObjectPushSink.__qualname__}"
    if actual_adapter != expected_adapter:
        raise RuntimeError(
            f"data2agent adapter drifted: {actual_adapter} != {expected_adapter}"
        )
    return payload


def _data2agent_version() -> str:
    project_root = Path(_required_env("C1C_DATA2AGENT_ROOT")).resolve()
    imported_module = Path(data2agent.__file__ or "").resolve()
    if not imported_module.is_relative_to(project_root):
        raise RuntimeError(f"data2agent imported from {imported_module}, not locked {project_root}")
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


LOCK = _load_lock()
OBJECTS: dict[str, dict[str, Any]] = {str(item["table"]): item for item in LOCK["objects"]}


def _table_info(table: str) -> TableInfo:
    fixture = OBJECTS[table]
    columns = [(str(column), "text") for column in fixture["payload_columns"]]
    delete_flag = fixture.get("delete_flag_column")
    if delete_flag:
        columns.append((str(delete_flag), "int"))
    return TableInfo(name=table, columns=columns, pk=TABLE_KEYS[table])


def _binding(table: str) -> ObjectBinding:
    fixture = OBJECTS[table]
    return ObjectBinding(
        object_type=str(fixture["object_type"]),
        payload_contract_version=str(fixture["contract_version"]),
        schema_fingerprint=str(fixture["schema_fingerprint"]),
        payload_columns=tuple(str(item) for item in fixture["payload_columns"]),
        delete_flag_column=(
            str(fixture["delete_flag_column"]) if fixture.get("delete_flag_column") else None
        ),
    )


TABLES = {table: _table_info(table) for table in TABLE_KEYS}
BINDINGS = {table: _binding(table) for table in TABLE_KEYS}


def _token_provider() -> Callable[[], str]:
    return oidc_client_credentials_provider(
        _required_env("C1C_OIDC_TOKEN_URL"),
        _required_env("C1C_OIDC_CLIENT_ID"),
        _required_env("C1C_OIDC_CLIENT_SECRET"),
        audience="ai-hub-platform",
        scope="ai_hub.identity ai_hub.ingest.push",
        timeout=15,
        allow_insecure_http=True,
    )


def _sink(
    slot: str,
    *,
    source_application_id: str = SOURCE_APPLICATION_ID,
    retries: int = 3,
    post: Callable[[str, dict | None, str | None, float], dict | None] | None = None,
) -> AiHubObjectPushSink:
    state_root = Path(_required_env("C1C_STATE_DIR"))
    state_root.mkdir(parents=True, exist_ok=True)
    spool = state_root / "spool" / slot
    spool.mkdir(parents=True, exist_ok=True)
    return AiHubObjectPushSink(
        _required_env("C1C_PLATFORM_BASE"),
        source_application_id=source_application_id,
        bindings=BINDINGS,
        token_provider=_token_provider(),
        timeout=15,
        retries=retries,
        state_path=state_root / f"{slot}.sqlite",
        spool_directory=spool,
        post=post,
        complete_poll_interval=0.05,
    )


class DropResponseAfterSuccess:
    """Simulate a network break after AI Hub committed an HTTP request."""

    def __init__(self, endpoint_suffix: str) -> None:
        self.endpoint_suffix = endpoint_suffix
        self.dropped = False

    def __call__(
        self,
        endpoint: str,
        payload: dict | None,
        token: str | None,
        timeout: float,
    ) -> dict | None:
        result = push_module._urllib_request(
            endpoint,
            method="POST",
            payload=payload,
            token=token,
            timeout=timeout,
        )
        if not self.dropped and endpoint.endswith(self.endpoint_suffix):
            self.dropped = True
            raise urllib.error.URLError("simulated response loss after commit")
        return result


def _full(
    sink: AiHubObjectPushSink,
    table: str,
    pages: Sequence[tuple[str, list[dict[str, object]]]],
    *,
    complete: bool,
) -> int:
    info = TABLES[table]
    sink.begin_sync("c1c-reference", [table], 1)
    sink.begin_table("c1c-reference", info, mode="full_refresh", snapshot_id="c1c")
    written = 0
    for batch_id, rows in pages:
        written += sink.write(
            "c1c-reference",
            info,
            rows,
            batch_id,
            mode="full_refresh",
            snapshot_id="c1c",
        )
    if complete:
        sink.complete_table(
            "c1c-reference",
            info,
            "c1c-full-complete",
            written,
            len(pages),
            mode="full_refresh",
            snapshot_id="c1c",
        )
    return written


def check_lock() -> dict[str, object]:
    expected_tables = {"ITEM", "SALES_ORDER", "SALES_ORDER_D"}
    if set(OBJECTS) != expected_tables:
        raise RuntimeError(f"integration lock objects drifted: {sorted(OBJECTS)}")
    return {
        "phase": "check-lock",
        "combination_id": LOCK["combination_id"],
        "data2agent_version": _data2agent_version(),
    }


def stage_initial_full() -> dict[str, object]:
    written = _full(_sink("item-main"), "ITEM", INITIAL_ITEM_PAGES, complete=False)
    return {"phase": "stage-initial-full", "written": written}


def restart_complete_initial_full() -> dict[str, object]:
    written = _full(_sink("item-main"), "ITEM", RESTARTED_ITEM_PAGES, complete=True)
    return {"phase": "restart-complete-initial-full", "written": written}


def full_other_objects() -> dict[str, object]:
    counts = {
        table: _full(
            _sink(f"{table.lower()}-main"),
            table,
            pages,
            complete=True,
        )
        for table, pages in OTHER_OBJECT_PAGES.items()
    }
    return {"phase": "full-other-objects", "written": counts}


def incremental_batch_replay() -> dict[str, object]:
    fault = DropResponseAfterSuccess("/batches")
    sink = _sink("item-main", retries=2, post=fault)
    info = TABLES["ITEM"]
    sink.begin_table("c1c-reference", info, mode="incremental")
    written = sink.write(
        "c1c-reference",
        info,
        INCREMENTAL_ITEM_ROWS,
        "item-incremental-replay",
    )
    sink.complete_table("c1c-reference", info, "item-incremental-complete", written, 1)
    if not fault.dropped:
        raise RuntimeError("batch response-loss fault was not exercised")
    return {"phase": "incremental-batch-replay", "written": written}


def lose_complete_response() -> dict[str, object]:
    fault = DropResponseAfterSuccess("/complete")
    sink = _sink("item-main", retries=1, post=fault)
    info = TABLES["ITEM"]
    sink.begin_table("c1c-reference", info, mode="incremental")
    written = sink.write(
        "c1c-reference",
        info,
        [{"ITEM_CODE": "I-4", "ITEM_NAME": "Nut recovered", "DELETED": 0}],
        "item-complete-loss",
    )
    try:
        sink.complete_table("c1c-reference", info, "item-complete-loss", written, 1)
    except RuntimeError as exc:
        if "simulated response loss" not in str(exc):
            raise
    else:
        raise RuntimeError("complete response-loss fault did not interrupt the adapter")
    if not fault.dropped:
        raise RuntimeError("complete response-loss fault was not exercised")
    return {"phase": "lose-complete-response", "written": written}


def recover_complete_response() -> dict[str, object]:
    sink = _sink("item-main")
    info = TABLES["ITEM"]
    sink.begin_table("c1c-reference", info, mode="incremental")
    # Recovery finishes the persisted COMPLETING generation before opening a
    # new one. Abort that new empty generation so the next scenario starts cleanly.
    sink.abort_table("c1c-reference", info, mode="incremental")
    return {"phase": "recover-complete-response", "recovered": True}


def generation_race() -> dict[str, object]:
    barrier = threading.Barrier(2)

    def contender(slot: str) -> str:
        sink = _sink(slot)
        info = TABLES["ITEM"]
        sink.ensure_protocol()
        barrier.wait(timeout=10)
        try:
            sink.begin_table("c1c-reference", info, mode="incremental")
        except AiHubPushRejected as exc:
            if exc.error_code != "generation_in_progress":
                raise
            return "rejected"
        time.sleep(1)
        sink.abort_table("c1c-reference", info, mode="incremental")
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contender, ("race-a", "race-b")))
    if sorted(results) != ["accepted", "rejected"]:
        raise RuntimeError(f"generation race did not select one winner: {results}")
    return {"phase": "generation-race", "results": results}


def source_impersonation() -> dict[str, object]:
    sink = _sink("impersonation", source_application_id="other-application")
    try:
        sink.begin_table("c1c-reference", TABLES["ITEM"], mode="incremental")
    except AiHubPushRejected as exc:
        if exc.error_code != "source_impersonation_denied" or exc.status_code != 403:
            raise
    else:
        raise RuntimeError("source impersonation was unexpectedly accepted")
    return {"phase": "source-impersonation", "rejected": True}


def source_rebuild_full() -> dict[str, object]:
    written = _full(_sink("item-main"), "ITEM", REBUILT_ITEM_PAGES, complete=True)
    return {"phase": "source-rebuild-full", "written": written}


def push_disabled() -> dict[str, object]:
    try:
        _sink("push-disabled").ensure_protocol()
    except AiHubProtocolError as exc:
        if "Push 未启用" not in str(exc):
            raise
    else:
        raise RuntimeError("adapter accepted capabilities after Push was disabled")
    return {"phase": "push-disabled", "rejected": True}


COMMANDS: Mapping[str, Callable[[], dict[str, object]]] = {
    "check-lock": check_lock,
    "stage-initial-full": stage_initial_full,
    "restart-complete-initial-full": restart_complete_initial_full,
    "full-other-objects": full_other_objects,
    "incremental-batch-replay": incremental_batch_replay,
    "lose-complete-response": lose_complete_response,
    "recover-complete-response": recover_complete_response,
    "generation-race": generation_race,
    "source-impersonation": source_impersonation,
    "source-rebuild-full": source_rebuild_full,
    "push-disabled": push_disabled,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=sorted(COMMANDS))
    args = parser.parse_args()
    print(json.dumps(COMMANDS[args.phase](), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
