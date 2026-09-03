from __future__ import annotations

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HELPER = PROJECT_ROOT / "scripts/deploy/reconcile-authentik-blueprints.py"


def load_helper() -> dict[str, Any]:
    # Imports of Authentik itself occur only in main(), inside its own image.
    return runpy.run_path(str(HELPER))


def blueprint(path: str) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        pk=path,
        enabled=True,
        status="successful",
        last_applied=datetime(2026, 9, 3, tzinfo=UTC),
        refresh_from_db=lambda: None,
    )


def test_trusted_cli_waits_for_each_specific_task_in_baseline_production_order() -> None:
    helper = load_helper()
    instances = [blueprint(path) for path in helper["BLUEPRINT_PATHS"]]
    calls: list[str] = []

    def send(*, args: tuple[str], rel_obj: SimpleNamespace, store_results: bool) -> SimpleNamespace:
        assert args == (rel_obj.pk,)
        assert store_results is True
        calls.append(f"send:{rel_obj.path}")

        def wait(*, block: bool, timeout: int) -> None:
            assert block is True
            assert timeout == 300_000
            calls.append(f"wait:{rel_obj.path}")
            rel_obj.last_applied += timedelta(seconds=1)

        return SimpleNamespace(get_result=wait)

    helper["apply_instances"](instances, SimpleNamespace(send_with_options=send))

    assert calls == [
        f"{action}:{path}" for path in helper["BLUEPRINT_PATHS"] for action in ("send", "wait")
    ]


@pytest.mark.parametrize("failure", ["disabled", "error", "warning", "stale", "missing", "task"])
def test_convergence_fails_closed_without_applying_the_next_blueprint(failure: str) -> None:
    helper = load_helper()
    instances = [blueprint(path) for path in helper["BLUEPRINT_PATHS"]]
    calls: list[str] = []
    if failure == "disabled":
        instances[0].enabled = False

    def send(**kwargs: Any) -> SimpleNamespace:
        instance = kwargs["rel_obj"]
        calls.append(instance.path)

        def wait(**_kwargs: Any) -> None:
            if failure == "task":
                raise TimeoutError("resolved_secret=never-print-this")
            if failure in {"error", "warning"}:
                instance.status = failure
                instance.last_applied += timedelta(seconds=1)
            elif failure == "missing":
                instance.last_applied = None

        return SimpleNamespace(get_result=wait)

    with pytest.raises(helper["ConvergenceError"]) as error:
        helper["apply_instances"](instances, SimpleNamespace(send_with_options=send))

    assert "never-print-this" not in str(error.value)
    assert calls == ([] if failure == "disabled" else [instances[0].path])


def test_discovery_wait_is_bounded_and_rejects_ambiguous_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    wait = helper["wait_for_instances"]
    ticks = iter((0, 0, 5))
    sleeps: list[int] = []
    monkeypatch.setitem(
        wait.__globals__,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleeps.append),
    )

    def find_missing(**_kwargs: Any) -> list[object]:
        return []

    missing = SimpleNamespace(objects=SimpleNamespace(filter=find_missing))
    with pytest.raises(helper["ConvergenceError"], match="Timed out"):
        wait(missing, timeout=5)
    assert sleeps == [5]

    monkeypatch.setitem(wait.__globals__, "time", SimpleNamespace(monotonic=lambda: 0))

    def find_duplicate(**_kwargs: Any) -> list[object]:
        return [object(), object()]

    duplicate = SimpleNamespace(objects=SimpleNamespace(filter=find_duplicate))
    with pytest.raises(helper["ConvergenceError"], match="Multiple"):
        wait(duplicate)


def test_automation_token_keeps_only_read_only_blueprint_permissions() -> None:
    document = yaml.load(
        (PROJECT_ROOT / "deploy/authentik/ai-hub-blueprint.yaml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    account = next(
        entry
        for entry in document["entries"]
        if entry.get("id") == "ai-hub-authentik-automation-user"
    )
    permissions = set(account["attrs"]["permissions"])
    assert {item for item in permissions if item.startswith("authentik_blueprints.")} == {
        "authentik_blueprints.view_blueprintinstance"
    }
    assert not any(
        item.startswith(("authentik_flows.", "authentik_stages_", "authentik_rbac."))
        for item in permissions
    )
    assert account["attrs"].get("is_superuser", "false") == "false"
    # Do not break the platform's existing credential lifecycle while removing
    # blueprint writes from this token's deployment responsibilities.
    assert "authentik_providers_oauth2.change_oauth2provider" in permissions
    assert "authentik_core.reset_user_password" in permissions
