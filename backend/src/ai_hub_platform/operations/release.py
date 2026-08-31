from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from ai_hub_platform.operations.backup import sha256_file

RELEASE_SCHEMA_VERSION = 1
RELEASE_ENVIRONMENTS = {"local", "test", "integration", "uat", "production"}
RELEASE_PROFILES = {"base-access"}
RELEASE_STATUSES = {"CANDIDATE", "APPROVED", "DEPLOYED", "ROLLED_BACK"}
MIGRATION_COMPONENTS = ("core", "raw")
CONTRACT_WRITER_SERVICES = ("platform-api", "platform-ingest-scheduler")
PLATFORM_RELEASE_SERVICES = (*CONTRACT_WRITER_SERVICES, "portal")
PROFILE_MIGRATION_COMPONENTS = {
    "base-access": MIGRATION_COMPONENTS,
}
MIGRATION_DIRECTORIES = {
    "core": Path("backend/migrations/versions/core"),
    "raw": Path("backend/migrations/versions/raw"),
}
MIGRATION_TABLES = {
    "core": "platform_core.alembic_version",
    "raw": "platform_raw.alembic_version",
}
CONTRACT_PATHS = (
    Path("contracts/api/platform-api.openapi.yaml"),
)
REQUIRED_RELEASE_GATES = frozenset(
    {
        "python",
        "frontend",
        "deployment",
        "identity-runtime",
        "recovery-runtime",
        "observability-runtime",
        "credential-rotation-runtime",
    }
)
IMAGE_DIGEST_REFERENCE = re.compile(
    r"^[a-z0-9][a-z0-9._:/-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
    r"@sha256:[0-9a-f]{64}$"
)
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
SENSITIVE_FIELD = re.compile(
    r"(?:^|_)(?:access_token|api_token|client_secret|connection_string|password|"
    r"private_key|refresh_token|secret)(?:$|_)",
    re.IGNORECASE,
)
DESTRUCTIVE_SQL = re.compile(
    r"\b(?:DROP\s+(?:TABLE|COLUMN|INDEX|CONSTRAINT)|TRUNCATE|DELETE\s+FROM)\b",
    re.IGNORECASE,
)
LIVE_DATA_CONDITION_CREDENTIALS = "no_environment_has_multiple_credential_rows"
LIVE_DATA_CONDITION_NO_PUSH_SOURCES = "no_push_ingest_sources"
LIVE_DATA_CONDITION_NO_ENFORCE_PULL = "no_enforce_pull_ingest_sources"
KNOWN_LIVE_DATA_CONDITIONS = frozenset(
    {
        LIVE_DATA_CONDITION_CREDENTIALS,
        LIVE_DATA_CONDITION_NO_PUSH_SOURCES,
        LIVE_DATA_CONDITION_NO_ENFORCE_PULL,
    }
)
DEFAULT_LIVE_DATA_CONDITIONS = (LIVE_DATA_CONDITION_CREDENTIALS,)


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationRevision:
    component: str
    revision: str
    down_revision: str | None
    path: Path
    phase: Literal["expand", "contract"] | None
    compatibility_exceptions: frozenset[str]
    rollback_compatible_with: frozenset[str]
    dangerous_operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationTransition:
    component: str
    previous_head: str
    target_head: str
    revisions: tuple[str, ...]
    phases: tuple[str, ...]
    rollback_schema_compatible: bool


@dataclass(frozen=True, slots=True)
class ReleaseTarget:
    compose_file: Path
    env_file: Path
    profile: str
    project_name: str | None = None

    def command(self, *arguments: str) -> list[str]:
        command = [
            "docker",
            "compose",
            "--env-file",
            str(self.env_file),
            "-f",
            str(self.compose_file),
            "--profile",
            self.profile,
        ]
        if self.project_name:
            command[2:2] = ["--project-name", self.project_name]
        return [*command, *arguments]


def _target_components(target: ReleaseTarget) -> tuple[str, ...]:
    try:
        return PROFILE_MIGRATION_COMPONENTS[target.profile]
    except KeyError as error:
        raise ReleaseError(f"Unsupported release profile: {target.profile}") from error


def _assert_target_matches_manifest(
    target: ReleaseTarget,
    manifest: Mapping[str, Any],
) -> None:
    deployment = _expect_object(manifest, "deployment")
    if deployment.get("profile") != target.profile:
        raise ReleaseError("Release target profile differs from the approved manifest")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"Invalid JSON document: {path}") from error
    if not isinstance(raw, dict):
        raise ReleaseError(f"JSON document must be an object: {path}")
    return cast(dict[str, Any], raw)


def _sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReleaseError(f"{field_name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ReleaseError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _literal_assignment(tree: ast.Module, name: str) -> object | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if value is None:
            return None
        try:
            return ast.literal_eval(value)
        except (ValueError, TypeError) as error:
            raise ReleaseError(f"Migration metadata {name} must be a literal") from error
    return None


def _string_argument(call: ast.Call, position: int) -> str | None:
    if len(call.args) <= position:
        return None
    try:
        value = ast.literal_eval(call.args[position])
    except ValueError, TypeError:
        return None
    return value if isinstance(value, str) else None


def _dangerous_operation(call: ast.Call) -> str | None:
    function = call.func
    if not (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "op"
    ):
        return None
    operation = function.attr
    if operation == "drop_constraint":
        return f"drop_constraint:{_string_argument(call, 0) or '<dynamic>'}"
    if operation == "drop_index":
        return f"drop_index:{_string_argument(call, 0) or '<dynamic>'}"
    if operation == "drop_table":
        return f"drop_table:{_string_argument(call, 0) or '<dynamic>'}"
    if operation == "drop_column":
        table = _string_argument(call, 0) or "<dynamic>"
        column = _string_argument(call, 1) or "<dynamic>"
        return f"drop_column:{table}.{column}"
    if operation == "alter_column":
        breaking_keywords: list[str] = []
        for keyword in call.keywords:
            if keyword.arg == "type_":
                breaking_keywords.append("type")
            if keyword.arg == "nullable":
                try:
                    nullable = ast.literal_eval(keyword.value)
                except ValueError, TypeError:
                    nullable = None
                if nullable is False:
                    breaking_keywords.append("nullable_false")
        if breaking_keywords:
            table = _string_argument(call, 0) or "<dynamic>"
            column = _string_argument(call, 1) or "<dynamic>"
            return f"alter_column:{table}.{column}:{'+'.join(breaking_keywords)}"
    if operation == "add_column" and len(call.args) >= 2:
        column_call = call.args[1]
        if isinstance(column_call, ast.Call):
            column_name = _string_argument(column_call, 0) or "<dynamic>"
            nullable: object = True
            has_server_default = False
            for keyword in column_call.keywords:
                if keyword.arg == "nullable":
                    try:
                        nullable = ast.literal_eval(keyword.value)
                    except ValueError, TypeError:
                        nullable = None
                if keyword.arg == "server_default":
                    has_server_default = True
            if nullable is False and not has_server_default:
                table = _string_argument(call, 0) or "<dynamic>"
                return f"add_column:{table}.{column_name}:not_null_no_default"
    if operation in {
        "create_check_constraint",
        "create_foreign_key",
        "create_unique_constraint",
    }:
        return f"{operation}:{_string_argument(call, 0) or '<dynamic>'}"
    if operation == "create_index":
        unique = False
        for keyword in call.keywords:
            if keyword.arg == "unique":
                try:
                    unique = ast.literal_eval(keyword.value) is True
                except ValueError, TypeError:
                    unique = False
        if unique:
            return f"create_unique_index:{_string_argument(call, 0) or '<dynamic>'}"
    if operation == "execute":
        statement = _string_argument(call, 0)
        if statement is not None and DESTRUCTIVE_SQL.search(statement):
            normalized = " ".join(statement.split())[:80]
            return f"execute:{normalized}"
    return None


def parse_migration(path: Path, component: str) -> MigrationRevision:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise ReleaseError(f"Cannot parse migration: {path}") from error
    revision = _literal_assignment(tree, "revision")
    down_revision = _literal_assignment(tree, "down_revision")
    phase = _literal_assignment(tree, "release_phase")
    raw_exceptions = _literal_assignment(tree, "compatibility_exceptions")
    raw_rollback = _literal_assignment(tree, "rollback_compatible_with")
    if raw_exceptions is None:
        raw_exceptions = frozenset[str]()
    if raw_rollback is None:
        raw_rollback = frozenset[str]()
    if not isinstance(revision, str) or not revision:
        raise ReleaseError(f"Migration lacks a revision: {path}")
    if down_revision is not None and not isinstance(down_revision, str):
        raise ReleaseError(f"Merge migrations require an explicit release review: {path}")
    if phase not in {None, "expand", "contract"}:
        raise ReleaseError(f"Unsupported release_phase in {path}: {phase}")
    if not isinstance(raw_exceptions, set | frozenset) or not all(
        isinstance(value, str) for value in cast(set[object] | frozenset[object], raw_exceptions)
    ):
        raise ReleaseError(f"compatibility_exceptions must be a set of strings: {path}")
    if not isinstance(raw_rollback, set | frozenset) or not all(
        isinstance(value, str) for value in cast(set[object] | frozenset[object], raw_rollback)
    ):
        raise ReleaseError(f"rollback_compatible_with must be a set of revisions: {path}")
    upgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "upgrade"
        ),
        None,
    )
    operations: set[str] = set()
    if upgrade is not None:
        for node in ast.walk(upgrade):
            if isinstance(node, ast.Call):
                operation = _dangerous_operation(node)
                if operation is not None:
                    operations.add(operation)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if DESTRUCTIVE_SQL.search(node.value):
                    operations.add(f"execute:{' '.join(node.value.split())[:80]}")
    return MigrationRevision(
        component=component,
        revision=revision,
        down_revision=down_revision,
        path=path,
        phase=cast(Literal["expand", "contract"] | None, phase),
        compatibility_exceptions=frozenset(cast(set[str] | frozenset[str], raw_exceptions)),
        rollback_compatible_with=frozenset(cast(set[str] | frozenset[str], raw_rollback)),
        dangerous_operations=tuple(sorted(operations)),
    )


def migration_inventory(project_root: Path) -> dict[str, dict[str, MigrationRevision]]:
    inventory: dict[str, dict[str, MigrationRevision]] = {}
    for component, relative_directory in MIGRATION_DIRECTORIES.items():
        directory = project_root / relative_directory
        revisions: dict[str, MigrationRevision] = {}
        for path in sorted(directory.glob("*.py")):
            migration = parse_migration(path, component)
            if migration.revision in revisions:
                raise ReleaseError(
                    f"Duplicate {component} migration revision: {migration.revision}"
                )
            revisions[migration.revision] = migration
        if not revisions:
            raise ReleaseError(f"No {component} migrations found in {directory}")
        inventory[component] = revisions
    return inventory


def migration_heads(project_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for component, revisions in migration_inventory(project_root).items():
        parents = {
            migration.down_revision
            for migration in revisions.values()
            if migration.down_revision is not None
        }
        heads = sorted(set(revisions) - parents)
        if len(heads) != 1:
            raise ReleaseError(f"{component} must have exactly one migration head: {heads}")
        result[component] = heads[0]
    return result


def validate_migration_transition(
    project_root: Path,
    previous_heads: Mapping[str, str],
    target_heads: Mapping[str, str],
    *,
    allow_contract: bool = False,
) -> dict[str, MigrationTransition]:
    inventory = migration_inventory(project_root)
    transitions: dict[str, MigrationTransition] = {}
    for component in MIGRATION_COMPONENTS:
        previous = previous_heads.get(component)
        target = target_heads.get(component)
        if not previous or not target:
            raise ReleaseError(f"Both previous and target {component} heads are required")
        if previous not in inventory[component]:
            raise ReleaseError(f"Unknown previous {component} revision: {previous}")
        if target not in inventory[component]:
            raise ReleaseError(f"Unknown target {component} revision: {target}")
        pending: list[MigrationRevision] = []
        cursor = target
        seen: set[str] = set()
        while cursor != previous:
            if cursor in seen:
                raise ReleaseError(f"Cycle detected in {component} migration history")
            seen.add(cursor)
            migration = inventory[component].get(cursor)
            if migration is None or migration.down_revision is None:
                raise ReleaseError(
                    f"{previous} is not an ancestor of {target} in {component} migrations"
                )
            pending.append(migration)
            cursor = migration.down_revision
        pending.reverse()
        for migration in pending:
            if migration.phase is None:
                raise ReleaseError(f"New migration {migration.revision} must declare release_phase")
            unreviewed = sorted(
                set(migration.dangerous_operations) - migration.compatibility_exceptions
            )
            if migration.phase == "expand" and unreviewed:
                raise ReleaseError(
                    f"Expand migration {migration.revision} has destructive operations without "
                    f"compatibility exceptions: {', '.join(unreviewed)}"
                )
            unknown_exceptions = sorted(
                migration.compatibility_exceptions - set(migration.dangerous_operations)
            )
            if unknown_exceptions:
                raise ReleaseError(
                    f"Migration {migration.revision} declares unused compatibility exceptions: "
                    f"{', '.join(unknown_exceptions)}"
                )
            if migration.phase == "contract" and not allow_contract:
                raise ReleaseError(
                    f"Contract migration {migration.revision} requires a separate approved window"
                )
        rollback_compatible = all(
            migration.phase == "expand"
            and (
                migration.down_revision in migration.rollback_compatible_with
                or previous in migration.rollback_compatible_with
            )
            for migration in pending
        )
        transitions[component] = MigrationTransition(
            component=component,
            previous_head=previous,
            target_head=target,
            revisions=tuple(migration.revision for migration in pending),
            phases=tuple(cast(str, migration.phase) for migration in pending),
            rollback_schema_compatible=rollback_compatible,
        )
    pending_revisions = [
        (component, revision, phase)
        for component, transition in transitions.items()
        for revision, phase in zip(
            transition.revisions, transition.phases, strict=True
        )
    ]
    if any(phase == "contract" for _, _, phase in pending_revisions) and (
        len(pending_revisions) != 1 or pending_revisions[0][2] != "contract"
    ):
        raise ReleaseError(
            "A contract migration must be the only pending revision in its approved window"
        )
    return transitions


def validate_backup_receipt(
    receipt_path: Path,
    *,
    now: datetime | None = None,
    maximum_age_minutes: int = 60,
    require_off_host: bool = True,
) -> dict[str, Any]:
    receipt = _load_json_object(receipt_path)
    required = {
        "schema_version",
        "verified",
        "archive",
        "archive_sha256",
        "backup_id",
        "created_at",
        "verified_at",
        "storage_class",
        "profile",
    }
    missing = sorted(required - receipt.keys())
    if missing:
        raise ReleaseError(f"Backup receipt lacks fields: {', '.join(missing)}")
    if receipt["schema_version"] != 1 or receipt["verified"] is not True:
        raise ReleaseError("Backup receipt is not a successful full verification receipt")
    archive_name = receipt["archive"]
    if not isinstance(archive_name, str) or Path(archive_name).name != archive_name:
        raise ReleaseError("Backup receipt archive must be a local filename")
    archive_path = receipt_path.parent / archive_name
    sidecar_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    if not archive_path.is_file() or not sidecar_path.is_file():
        raise ReleaseError("Verified backup archive or SHA-256 sidecar is missing")
    fields = sidecar_path.read_text(encoding="utf-8").split()
    if len(fields) != 2 or fields[1] != archive_name:
        raise ReleaseError("Backup SHA-256 sidecar does not match the archive filename")
    archive_sha256 = sha256_file(archive_path)
    if fields[0] != archive_sha256 or receipt["archive_sha256"] != archive_sha256:
        raise ReleaseError("Backup archive SHA-256 does not match its receipt")
    expected_backup_id = archive_name.removesuffix(".tar.aesgcm")
    if receipt["backup_id"] != expected_backup_id:
        raise ReleaseError("Backup receipt identifier does not match its archive")
    if require_off_host and receipt["storage_class"] != "off-host":
        raise ReleaseError("Production release requires a verified off-host backup")
    created_at = _parse_time(receipt["created_at"], "backup.created_at")
    verified_at = _parse_time(receipt["verified_at"], "backup.verified_at")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if verified_at < created_at or created_at > current or verified_at > current:
        raise ReleaseError("Backup receipt timestamps are inconsistent")
    if current - created_at > timedelta(minutes=maximum_age_minutes):
        raise ReleaseError(
            f"Verified backup is older than the {maximum_age_minutes}-minute release limit"
        )
    return {
        "backup_id": receipt["backup_id"],
        "receipt": str(receipt_path.resolve()),
        "archive_sha256": archive_sha256,
        "created_at": created_at.isoformat(),
        "verified_at": verified_at.isoformat(),
        "storage_class": receipt["storage_class"],
        "profile": receipt["profile"],
    }


def _assert_no_sensitive_fields(value: object, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for raw_key, child in cast(dict[object, object], value).items():
            key = str(raw_key)
            if SENSITIVE_FIELD.search(key):
                raise ReleaseError(
                    f"Release manifest contains a forbidden secret field: {path}.{key}"
                )
            _assert_no_sensitive_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(cast(list[object], value)):
            _assert_no_sensitive_fields(child, f"{path}[{index}]")


def _expect_object(parent: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ReleaseError(f"Release manifest field {key} must be an object")
    return cast(dict[str, Any], value)


def validate_release_manifest(
    manifest: Mapping[str, Any],
    *,
    project_root: Path | None = None,
    verify_repository_digests: bool = False,
) -> None:
    _assert_no_sensitive_fields(manifest)
    if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ReleaseError("Unsupported release manifest schema_version")
    release_id = manifest.get("release_id")
    if not isinstance(release_id, str) or RELEASE_ID.fullmatch(release_id) is None:
        raise ReleaseError("Release manifest has an invalid release_id")
    if manifest.get("status") not in RELEASE_STATUSES:
        raise ReleaseError("Release manifest has an invalid status")
    _parse_time(manifest.get("created_at"), "created_at")
    source = _expect_object(manifest, "source")
    commit = source.get("commit_sha")
    if not isinstance(commit, str) or COMMIT_SHA.fullmatch(commit) is None:
        raise ReleaseError("Release manifest commit_sha must be a full Git SHA")
    if not isinstance(source.get("dirty"), bool):
        raise ReleaseError("Release manifest source.dirty must be boolean")
    deployment = _expect_object(manifest, "deployment")
    environment = deployment.get("environment")
    if environment not in RELEASE_ENVIRONMENTS:
        raise ReleaseError("Release manifest has an invalid deployment environment")
    if deployment.get("tier") != "STANDARD_SINGLE_NODE":
        raise ReleaseError("Release manifest has an unsupported deployment tier")
    if deployment.get("profile") not in RELEASE_PROFILES:
        raise ReleaseError("Release manifest has an invalid deployment profile")
    images = _expect_object(manifest, "images")
    if set(images) != {"platform", "portal"}:
        raise ReleaseError("Release manifest must contain exactly platform and portal images")
    production = environment == "production"
    for name, reference in images.items():
        if not isinstance(reference, str) or not reference:
            raise ReleaseError(f"Release image {name} must be a non-empty reference")
        if production and IMAGE_DIGEST_REFERENCE.fullmatch(reference) is None:
            raise ReleaseError(
                f"Production release image {name} must use an exact tag and registry digest"
            )
    component_lock = _expect_object(manifest, "component_lock")
    if not isinstance(component_lock.get("lock_id"), str):
        raise ReleaseError("Release manifest lacks component_lock.lock_id")
    if not re.fullmatch(r"[0-9a-f]{64}", str(component_lock.get("sha256", ""))):
        raise ReleaseError("Release manifest has an invalid component lock SHA-256")
    migrations = _expect_object(manifest, "migrations")
    if set(migrations) != set(MIGRATION_COMPONENTS):
        raise ReleaseError("Release manifest must record every platform migration component")
    pending_revision_count = 0
    contract_revision_count = 0
    for component in MIGRATION_COMPONENTS:
        entry = migrations[component]
        if not isinstance(entry, dict):
            raise ReleaseError(f"Migration entry {component} must be an object")
        typed = cast(dict[str, Any], entry)
        if not isinstance(typed.get("previous_head"), str) or not isinstance(
            typed.get("target_head"), str
        ):
            raise ReleaseError(f"Migration entry {component} lacks revision heads")
        revisions = typed.get("revisions")
        phases = typed.get("phases")
        if not isinstance(revisions, list) or not isinstance(phases, list) or not isinstance(
            typed.get("rollback_schema_compatible"), bool
        ):
            raise ReleaseError(f"Migration entry {component} has invalid compatibility data")
        typed_revisions = cast(list[object], revisions)
        typed_phases = cast(list[object], phases)
        if (
            len(typed_revisions) != len(typed_phases)
            or any(not isinstance(revision, str) for revision in typed_revisions)
            or any(phase not in {"expand", "contract"} for phase in typed_phases)
        ):
            raise ReleaseError(f"Migration entry {component} has invalid release phases")
        pending_revision_count += len(typed_revisions)
        contract_revision_count += typed_phases.count("contract")
    has_contract_migration = contract_revision_count > 0
    if has_contract_migration and (
        pending_revision_count != 1 or contract_revision_count != 1
    ):
        raise ReleaseError(
            "A contract migration must be the only pending revision in its approved window"
        )
    contracts = _expect_object(manifest, "contracts")
    if set(contracts) != {str(path) for path in CONTRACT_PATHS}:
        raise ReleaseError("Release manifest contract inventory is incomplete")
    if not all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in contracts.values()):
        raise ReleaseError("Release manifest contains an invalid contract SHA-256")
    backup = _expect_object(manifest, "backup")
    for key in ("backup_id", "receipt", "archive_sha256", "created_at", "verified_at"):
        if not isinstance(backup.get(key), str) or not backup[key]:
            raise ReleaseError(f"Release manifest backup.{key} is required")
    if production and backup.get("storage_class") != "off-host":
        raise ReleaseError("Production release manifest must reference an off-host backup")
    gates = manifest.get("gates")
    if not isinstance(gates, list):
        raise ReleaseError("Release manifest gates must be an array")
    gate_ids: set[str] = set()
    for raw_gate in cast(list[object], gates):
        if not isinstance(raw_gate, dict):
            raise ReleaseError("Release gate must be an object")
        gate = cast(dict[str, Any], raw_gate)
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or gate_id in gate_ids:
            raise ReleaseError("Release gate identifiers must be unique strings")
        gate_ids.add(gate_id)
        if gate.get("status") != "PASSED":
            raise ReleaseError(f"Release gate did not pass: {gate_id}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(gate.get("evidence_sha256", ""))):
            raise ReleaseError(f"Release gate lacks immutable evidence: {gate_id}")
    missing_gates = sorted(REQUIRED_RELEASE_GATES - gate_ids)
    if missing_gates:
        raise ReleaseError(f"Release manifest lacks required gates: {', '.join(missing_gates)}")
    approval = _expect_object(manifest, "approval")
    if not isinstance(approval.get("approved_by"), str) or not approval["approved_by"].strip():
        raise ReleaseError("Release manifest requires an approver")
    _parse_time(approval.get("approved_at"), "approval.approved_at")
    remaining_risks = approval.get("remaining_risks")
    if not isinstance(remaining_risks, list) or any(
        not isinstance(risk, str) or not risk.strip()
        for risk in cast(list[object], remaining_risks)
    ):
        raise ReleaseError("Release manifest approval.remaining_risks must be strings")
    if has_contract_migration and not remaining_risks:
        raise ReleaseError("Contract release manifests must document the remaining risk")
    rollback = _expect_object(manifest, "rollback")
    if not isinstance(rollback.get("previous_manifest_sha256"), str):
        raise ReleaseError("Release manifest lacks the previous approved manifest digest")
    if not isinstance(rollback.get("live_data_check_required"), bool):
        raise ReleaseError("Release manifest lacks the rollback live-data policy")
    parse_live_data_conditions(rollback.get("live_data_condition"))
    derived_schema_compatible = all(
        bool(
            cast(dict[str, Any], migrations[component])["rollback_schema_compatible"]
        )
        for component in MIGRATION_COMPONENTS
    )
    if rollback.get("schema_compatible") is not derived_schema_compatible:
        raise ReleaseError(
            "rollback.schema_compatible must equal all "
            "migrations.*.rollback_schema_compatible"
        )
    if production and source.get("dirty") is not False:
        raise ReleaseError("Production release manifests cannot be created from a dirty tree")
    if manifest.get("status") in {"APPROVED", "DEPLOYED"} and not rollback.get(
        "previous_manifest_sha256"
    ):
        raise ReleaseError("Approved releases require a previous approved release manifest")
    if project_root is not None and verify_repository_digests:
        lock_path = project_root / "deploy/component-lock.json"
        if _sha256_text(lock_path) != component_lock["sha256"]:
            raise ReleaseError("Component lock digest differs from the release manifest")
        lock = _load_json_object(lock_path)
        if lock.get("lock_id") != component_lock["lock_id"]:
            raise ReleaseError("Component lock identifier differs from the release manifest")
        for path in CONTRACT_PATHS:
            if _sha256_text(project_root / path) != contracts[str(path)]:
                raise ReleaseError(f"Contract changed after manifest creation: {path}")


def _git_source(project_root: Path) -> tuple[str, bool]:
    revision = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode != 0:
        raise ReleaseError("Cannot resolve the release Git commit")
    commit = revision.stdout.strip()
    if COMMIT_SHA.fullmatch(commit) is None:
        raise ReleaseError("Git returned an invalid release commit")
    status = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        raise ReleaseError("Cannot inspect the release worktree")
    return commit, bool(status.stdout.strip())


def _gate_documents(gate_paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for gate_id, path in sorted(gate_paths.items()):
        document = _load_json_object(path)
        status = document.get("status", document.get("result"))
        passed = document.get("passed")
        if status is not None and status not in {"PASSED", "SUCCESS"}:
            raise ReleaseError(f"Gate evidence is not successful: {gate_id}")
        if passed is not None and passed is not True:
            raise ReleaseError(f"Gate evidence is not successful: {gate_id}")
        if status is None and passed is not True:
            raise ReleaseError(f"Gate evidence is not successful: {gate_id}")
        documents.append(
            {
                "id": gate_id,
                "status": "PASSED",
                "evidence": str(path.resolve()),
                "evidence_sha256": _sha256_text(path),
            }
        )
    return documents


def _verify_manifest_artifacts(manifest: Mapping[str, Any]) -> None:
    raw_gates = manifest.get("gates")
    if not isinstance(raw_gates, list):
        raise ReleaseError("Release manifest gates must be an array")
    for raw_gate in cast(list[object], raw_gates):
        if not isinstance(raw_gate, dict):
            raise ReleaseError("Release gate must be an object")
        gate = cast(dict[str, Any], raw_gate)
        evidence_path = Path(str(gate.get("evidence", "")))
        if not evidence_path.is_file():
            raise ReleaseError(f"Release gate evidence is unavailable: {gate.get('id')}")
        if _sha256_text(evidence_path) != gate.get("evidence_sha256"):
            raise ReleaseError(f"Release gate evidence digest changed: {gate.get('id')}")

    rollback = _expect_object(manifest, "rollback")
    previous_path = Path(str(rollback.get("previous_manifest", "")))
    if not previous_path.is_file():
        raise ReleaseError("Previous approved release manifest is unavailable")
    if _sha256_text(previous_path) != rollback.get("previous_manifest_sha256"):
        raise ReleaseError("Previous release manifest digest does not match the rollback point")
    previous = _load_json_object(previous_path)
    validate_release_manifest(previous)
    if previous.get("status") not in {"APPROVED", "DEPLOYED"}:
        raise ReleaseError("Previous release manifest is not approved")


def create_release_manifest(
    *,
    project_root: Path,
    release_id: str,
    environment: str,
    profile: str,
    platform_image: str,
    portal_image: str,
    backup_receipt: Path,
    previous_manifest_path: Path,
    gate_paths: Mapping[str, Path],
    approved_by: str,
    allow_contract: bool = False,
    risks: Sequence[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    previous_manifest = _load_json_object(previous_manifest_path)
    validate_release_manifest(previous_manifest)
    previous_status = previous_manifest.get("status")
    if previous_status not in {"APPROVED", "DEPLOYED"}:
        raise ReleaseError("Previous release manifest is not approved")
    previous_migrations = _expect_object(previous_manifest, "migrations")
    previous_heads = {
        component: str(cast(dict[str, Any], previous_migrations[component])["target_head"])
        for component in MIGRATION_COMPONENTS
    }
    target_heads = migration_heads(project_root)
    transitions = validate_migration_transition(
        project_root,
        previous_heads,
        target_heads,
        allow_contract=allow_contract,
    )
    backup = validate_backup_receipt(
        backup_receipt,
        now=created_at,
        maximum_age_minutes=60,
        require_off_host=environment == "production",
    )
    if backup["profile"] != profile:
        raise ReleaseError("Backup profile does not match the release profile")
    lock_path = project_root / "deploy/component-lock.json"
    component_lock = _load_json_object(lock_path)
    commit, dirty = _git_source(project_root)
    if environment == "production" and dirty:
        raise ReleaseError("Production release manifest cannot be created from a dirty tree")
    schema_compatible = all(
        transition.rollback_schema_compatible for transition in transitions.values()
    )
    manifest: dict[str, Any] = {
        "$schema": "../operations/release-manifest.schema.json",
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_id": release_id,
        "status": "APPROVED",
        "created_at": created_at.isoformat(),
        "source": {
            "commit_sha": commit,
            "dirty": dirty,
        },
        "deployment": {
            "environment": environment,
            "tier": "STANDARD_SINGLE_NODE",
            "profile": profile,
        },
        "images": {"platform": platform_image, "portal": portal_image},
        "component_lock": {
            "lock_id": component_lock["lock_id"],
            "sha256": _sha256_text(lock_path),
        },
        "migrations": {
            component: {
                **asdict(transitions[component]),
                "revisions": list(transitions[component].revisions),
                "phases": list(transitions[component].phases),
            }
            for component in MIGRATION_COMPONENTS
        },
        "contracts": {str(path): _sha256_text(project_root / path) for path in CONTRACT_PATHS},
        "backup": backup,
        "gates": _gate_documents(gate_paths),
        "approval": {
            "approved_by": approved_by,
            "approved_at": created_at.isoformat(),
            "remaining_risks": list(risks),
        },
        "rollback": {
            "previous_release_id": previous_manifest["release_id"],
            "previous_manifest": str(previous_manifest_path.resolve()),
            "previous_manifest_sha256": _sha256_text(previous_manifest_path),
            "schema_compatible": schema_compatible,
            "live_data_check_required": True,
            "live_data_condition": format_live_data_condition(
                live_data_conditions_for_rollback(schema_compatible=schema_compatible)
            ),
        },
    }
    validate_release_manifest(manifest)
    return manifest


def write_release_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o640)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _release_environment(manifest: Mapping[str, Any]) -> dict[str, str]:
    images = _expect_object(manifest, "images")
    return {
        **os.environ,
        "AI_HUB_PLATFORM_IMAGE_REF": str(images["platform"]),
        "AI_HUB_PORTAL_IMAGE_REF": str(images["portal"]),
    }


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        list(command),
        env=dict(environment) if environment is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"Command failed ({command[0]}): {detail[:1000]}")
    return result


def _ensure_release_image(reference: str, *, production: bool) -> None:
    if production:
        _run(["docker", "pull", reference])
    else:
        inspected = _run(["docker", "image", "inspect", reference], check=False)
        if inspected.returncode != 0:
            _run(["docker", "pull", reference])
    _run(["docker", "image", "inspect", reference])


def _psql_scalar(target: ReleaseTarget, sql: str) -> str:
    return _run(
        target.command(
            "exec",
            "-T",
            "postgres",
            "psql",
            "--username=postgres",
            "--dbname=platform_db",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--command",
            sql,
        )
    ).stdout.strip()


def live_migration_heads(target: ReleaseTarget) -> dict[str, str]:
    heads: dict[str, str] = {}
    for component in _target_components(target):
        table = MIGRATION_TABLES[component]
        value = _psql_scalar(target, f"SELECT version_num FROM {table};")
        if not value or "\n" in value:
            raise ReleaseError(f"Live {component} migration head is missing or ambiguous")
        heads[component] = value
    return heads


def assert_pending_contract_writers_stopped(
    manifest: Mapping[str, Any],
    target: ReleaseTarget,
    live_heads: Mapping[str, str],
) -> None:
    migrations = _expect_object(manifest, "migrations")
    contract_pending = False
    for component in _target_components(target):
        entry = cast(dict[str, Any], migrations[component])
        if (
            live_heads.get(component) == str(entry["previous_head"])
            and "contract" in entry.get("phases", [])
        ):
            contract_pending = True
            break
    if not contract_pending:
        return
    running: list[str] = []
    for service in CONTRACT_WRITER_SERVICES:
        result = _run(target.command("ps", "--quiet", service), check=False)
        if result.returncode != 0:
            raise ReleaseError(f"Cannot verify contract writer state: {service}")
        if result.stdout.strip():
            running.append(service)
    if running:
        raise ReleaseError(
            "Contract migration requires every old writer to be stopped: "
            + ", ".join(running)
        )


def parse_live_data_conditions(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseError("Release manifest rollback.live_data_condition is required")
    parsed: list[str] = []
    for part in value.split(";"):
        condition = part.strip()
        if not condition:
            continue
        if condition not in KNOWN_LIVE_DATA_CONDITIONS:
            raise ReleaseError(f"Unknown rollback live-data condition: {condition}")
        if condition not in parsed:
            parsed.append(condition)
    if not parsed:
        raise ReleaseError("Release manifest rollback.live_data_condition is empty")
    if LIVE_DATA_CONDITION_CREDENTIALS not in parsed:
        raise ReleaseError(
            "rollback.live_data_condition must include "
            f"{LIVE_DATA_CONDITION_CREDENTIALS}"
        )
    return tuple(parsed)


def live_data_conditions_for_rollback(*, schema_compatible: bool) -> tuple[str, ...]:
    if schema_compatible:
        return DEFAULT_LIVE_DATA_CONDITIONS
    return (
        *DEFAULT_LIVE_DATA_CONDITIONS,
        LIVE_DATA_CONDITION_NO_PUSH_SOURCES,
        LIVE_DATA_CONDITION_NO_ENFORCE_PULL,
    )


def format_live_data_condition(
    conditions: Sequence[str] = DEFAULT_LIVE_DATA_CONDITIONS,
) -> str:
    return ";".join(conditions)


def assert_image_rollback_declared(
    *, schema_compatible: bool, conditions: Sequence[str]
) -> None:
    if schema_compatible:
        return
    if LIVE_DATA_CONDITION_NO_PUSH_SOURCES not in conditions:
        raise ReleaseError("Release lacks a schema-compatible image rollback path")
    if LIVE_DATA_CONDITION_NO_ENFORCE_PULL not in conditions:
        raise ReleaseError("Release lacks a schema-compatible image rollback path")


def assert_manifest_image_rollback_allowed(manifest: Mapping[str, Any]) -> None:
    migrations = _expect_object(manifest, "migrations")
    if any(
        "contract" in cast(dict[str, Any], migrations[component]).get("phases", [])
        for component in MIGRATION_COMPONENTS
    ):
        raise ReleaseError(
            "Image rollback is forbidden after a contract migration; "
            "use a forward fix or restore the verified backup"
        )


def _psql_count(target: ReleaseTarget, sql: str, *, error: str) -> int:
    raw = _psql_scalar(target, sql)
    try:
        return int(raw)
    except ValueError as exc:
        raise ReleaseError(error) from exc


def _assert_no_multi_version_credentials(target: ReleaseTarget) -> None:
    duplicates = _psql_count(
        target,
        """
        SELECT COUNT(*)
        FROM (
            SELECT application_id, environment
            FROM platform_core.application_credential
            GROUP BY application_id, environment
            HAVING COUNT(*) > 1
        ) AS multi_version_environment;
        """,
        error="Cannot evaluate the credential rollback data condition",
    )
    if duplicates:
        raise ReleaseError(
            "Rollback to the previous image is forbidden after credential multi-version state "
            "exists; use a forward fix or the verified restore procedure"
        )


_PUSH_COLUMN_EXISTS_SQL = """
SELECT COUNT(*)
FROM information_schema.columns
WHERE table_schema = 'platform_core'
  AND table_name = 'ingest_source'
  AND column_name = 'transport_mode';
"""

_PUSH_SOURCE_COUNT_SQL = """
SELECT COUNT(*)
FROM platform_core.ingest_source
WHERE transport_mode = 'PUSH_AGENT';
"""

_ENFORCE_PULL_COUNT_SQL = """
SELECT COUNT(*)
FROM platform_core.ingest_source
WHERE transport_mode = 'PULL_EXPORT'
  AND contract_validation_mode = 'ENFORCE';
"""


def _assert_no_push_ingest_sources(target: ReleaseTarget) -> None:
    # Two statements: PostgreSQL type-checks every subquery in a statement, so a
    # single query that names ingest_source fails on databases that predate 0020.
    column_exists = _psql_count(
        target,
        _PUSH_COLUMN_EXISTS_SQL,
        error="Cannot evaluate the push-ingest rollback data condition",
    )
    if not column_exists:
        return
    push_count = _psql_count(
        target,
        _PUSH_SOURCE_COUNT_SQL,
        error="Cannot evaluate the push-ingest rollback data condition",
    )
    if push_count:
        raise ReleaseError(
            "Image rollback is forbidden while PUSH_AGENT ingest sources exist; "
            "delete or convert those sources, or use a forward fix"
        )


def _assert_no_enforce_pull_ingest_sources(target: ReleaseTarget) -> None:
    column_exists = _psql_count(
        target,
        _PUSH_COLUMN_EXISTS_SQL,
        error="Cannot evaluate the Pull ENFORCE rollback data condition",
    )
    if not column_exists:
        return
    enforce_count = _psql_count(
        target,
        _ENFORCE_PULL_COUNT_SQL,
        error="Cannot evaluate the Pull ENFORCE rollback data condition",
    )
    if enforce_count:
        raise ReleaseError(
            "Image rollback is forbidden while Pull ENFORCE ingest sources exist "
            "(including disabled rows); revert them to AUDIT_ONLY, or use a forward fix"
        )


def assert_live_rollback_compatible(
    target: ReleaseTarget, conditions: Sequence[str]
) -> None:
    for condition in conditions:
        if condition == LIVE_DATA_CONDITION_CREDENTIALS:
            _assert_no_multi_version_credentials(target)
        elif condition == LIVE_DATA_CONDITION_NO_PUSH_SOURCES:
            _assert_no_push_ingest_sources(target)
        elif condition == LIVE_DATA_CONDITION_NO_ENFORCE_PULL:
            _assert_no_enforce_pull_ingest_sources(target)
        else:
            raise ReleaseError(f"Unknown rollback live-data condition: {condition}")


def release_preflight(
    manifest_path: Path,
    target: ReleaseTarget,
    project_root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    manifest = _load_json_object(manifest_path)
    validate_release_manifest(
        manifest,
        project_root=project_root,
        verify_repository_digests=True,
    )
    _assert_target_matches_manifest(target, manifest)
    _verify_manifest_artifacts(manifest)
    backup = _expect_object(manifest, "backup")
    validated_backup = validate_backup_receipt(
        Path(str(backup["receipt"])),
        now=now,
        maximum_age_minutes=60,
        require_off_host=_expect_object(manifest, "deployment")["environment"] == "production",
    )
    for field in (
        "backup_id",
        "archive_sha256",
        "created_at",
        "verified_at",
        "storage_class",
        "profile",
    ):
        if validated_backup[field] != backup.get(field):
            raise ReleaseError(f"Verified backup differs from manifest field: {field}")
    migrations = _expect_object(manifest, "migrations")
    live_heads = live_migration_heads(target)
    for component in _target_components(target):
        entry = cast(dict[str, Any], migrations[component])
        accepted = {str(entry["previous_head"]), str(entry["target_head"])}
        if live_heads[component] not in accepted:
            raise ReleaseError(
                f"Live {component} migration head is outside the approved transition"
            )
    assert_pending_contract_writers_stopped(manifest, target, live_heads)
    rollback = _expect_object(manifest, "rollback")
    live_conditions = parse_live_data_conditions(rollback.get("live_data_condition"))
    assert_image_rollback_declared(
        schema_compatible=rollback.get("schema_compatible") is True,
        conditions=live_conditions,
    )
    assert_live_rollback_compatible(target, live_conditions)
    production = _expect_object(manifest, "deployment")["environment"] == "production"
    for reference in _expect_object(manifest, "images").values():
        _ensure_release_image(str(reference), production=production)
    return {
        "passed": True,
        "release_id": manifest["release_id"],
        "live_migration_heads": live_heads,
        "backup_id": backup["backup_id"],
        "rollback_live_data_compatible": True,
    }


def apply_expand_migrations(manifest: Mapping[str, Any], target: ReleaseTarget) -> None:
    _assert_target_matches_manifest(target, manifest)
    environment = _release_environment(manifest)
    migrations = _expect_object(manifest, "migrations")
    services = {
        "core": "platform-core-migrate",
        "raw": "platform-raw-migrate",
    }
    for component in _target_components(target):
        _run(
            target.command("run", "--rm", services[component]),
            environment=environment,
        )
    actual = live_migration_heads(target)
    expected = {
        component: str(cast(dict[str, Any], migrations[component])["target_head"])
        for component in _target_components(target)
    }
    if actual != expected:
        raise ReleaseError(f"Migration heads after expand differ from the manifest: {actual}")


def _container_health(container_id: str) -> str:
    result = _run(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container_id,
        ],
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "missing"


def _wait_healthy(container_id: str, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = _container_health(container_id)
        if status in {"healthy", "running"}:
            return
        if status in {"exited", "dead", "missing", "unhealthy"}:
            raise ReleaseError(f"Release container did not become healthy: {status}")
        time.sleep(2)
    raise ReleaseError("Release container health check timed out")


def _run_verified_canary(manifest_path: Path, target: ReleaseTarget) -> dict[str, Any]:
    manifest = _load_json_object(manifest_path)
    validate_release_manifest(manifest)
    _assert_target_matches_manifest(target, manifest)
    apply_expand_migrations(manifest, target)
    environment = _release_environment(manifest)
    safe_release_id = re.sub(r"[^a-z0-9_.-]", "-", str(manifest["release_id"]).lower())
    container_name = f"ai-hub-platform-canary-{safe_release_id}"
    container_id = ""
    try:
        result = _run(
            target.command(
                "run",
                "--detach",
                "--no-deps",
                "--label",
                "traefik.enable=false",
                "--name",
                container_name,
                "platform-api",
            ),
            environment=environment,
        )
        container_id = result.stdout.strip()
        if not container_id:
            raise ReleaseError("Compose did not return a canary container identifier")
        _wait_healthy(container_id)
        probe = (
            "import json, urllib.request; "
            "ready=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', "
            "timeout=3)); "
            "contract=json.load(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', "
            "timeout=3)); "
            "assert ready.get('status') == 'ok'; assert contract.get('openapi')"
        )
        _run(["docker", "exec", container_id, "python", "-c", probe])
        return {
            "passed": True,
            "release_id": manifest["release_id"],
            "canary": "isolated-no-edge-traffic",
            "migration_heads": live_migration_heads(target),
        }
    finally:
        if container_id:
            _run(["docker", "stop", "--time", "10", container_id], check=False)
            _run(["docker", "rm", "-f", container_id], check=False)


def run_canary(
    manifest_path: Path,
    target: ReleaseTarget,
    project_root: Path,
) -> dict[str, Any]:
    preflight = release_preflight(manifest_path, target, project_root)
    result = _run_verified_canary(manifest_path, target)
    return {**result, "preflight_passed": preflight["passed"] is True}


def _compose_service_container(target: ReleaseTarget, service: str) -> str:
    result = _run(target.command("ps", "--quiet", service))
    container_id = result.stdout.strip()
    if not container_id or "\n" in container_id:
        raise ReleaseError(f"Compose service does not have exactly one container: {service}")
    return container_id


def promote_release(
    manifest_path: Path,
    target: ReleaseTarget,
    project_root: Path,
) -> dict[str, Any]:
    manifest = _load_json_object(manifest_path)
    validate_release_manifest(manifest)
    _assert_target_matches_manifest(target, manifest)
    preflight = release_preflight(manifest_path, target, project_root)
    canary = _run_verified_canary(manifest_path, target)
    environment = _release_environment(manifest)
    services = list(PLATFORM_RELEASE_SERVICES)
    _run(
        target.command("up", "--detach", "--no-deps", *services),
        environment=environment,
    )
    for service in PLATFORM_RELEASE_SERVICES:
        _wait_healthy(_compose_service_container(target, service))
    return {
        "promoted": True,
        "release_id": manifest["release_id"],
        "services": services,
        "migration_heads": live_migration_heads(target),
        "preflight_passed": preflight["passed"] is True,
        "canary_passed": canary["passed"] is True,
    }


def rollback_release(
    current_manifest_path: Path,
    target: ReleaseTarget,
) -> dict[str, Any]:
    current = _load_json_object(current_manifest_path)
    validate_release_manifest(current)
    assert_manifest_image_rollback_allowed(current)
    _assert_target_matches_manifest(target, current)
    rollback = _expect_object(current, "rollback")
    live_conditions = parse_live_data_conditions(rollback.get("live_data_condition"))
    assert_image_rollback_declared(
        schema_compatible=rollback.get("schema_compatible") is True,
        conditions=live_conditions,
    )
    previous_path = Path(str(rollback.get("previous_manifest", "")))
    if not previous_path.is_file():
        raise ReleaseError("Previous approved release manifest is unavailable")
    if _sha256_text(previous_path) != rollback.get("previous_manifest_sha256"):
        raise ReleaseError("Previous release manifest digest does not match the rollback point")
    previous = _load_json_object(previous_path)
    validate_release_manifest(previous)
    if previous.get("status") not in {"APPROVED", "DEPLOYED"}:
        raise ReleaseError("Previous release manifest is not approved")
    _assert_target_matches_manifest(target, previous)
    current_migrations = _expect_object(current, "migrations")
    live_heads = live_migration_heads(target)
    for component in _target_components(target):
        entry = cast(dict[str, Any], current_migrations[component])
        if live_heads[component] not in {
            str(entry["previous_head"]),
            str(entry["target_head"]),
        }:
            raise ReleaseError(
                f"Live {component} migration head is outside the rollback transition"
            )
    assert_live_rollback_compatible(target, live_conditions)
    environment = _release_environment(previous)
    production = _expect_object(previous, "deployment")["environment"] == "production"
    for reference in _expect_object(previous, "images").values():
        _ensure_release_image(str(reference), production=production)
    services = list(PLATFORM_RELEASE_SERVICES)
    _run(
        target.command("up", "--detach", "--no-deps", *services),
        environment=environment,
    )
    for service in PLATFORM_RELEASE_SERVICES:
        _wait_healthy(_compose_service_container(target, service))
    return {
        "rolled_back": True,
        "from_release_id": current["release_id"],
        "to_release_id": previous["release_id"],
        "database_downgraded": False,
    }


def _parse_assignment(values: Sequence[str], name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if separator != "=" or not key or not item or key in result:
            raise ReleaseError(f"{name} must use unique NAME=VALUE assignments")
        result[key] = item
    return result


def _target_from_args(args: argparse.Namespace) -> ReleaseTarget:
    return ReleaseTarget(
        compose_file=Path(args.compose_file).resolve(),
        env_file=Path(args.env_file).resolve(),
        profile=str(args.profile),
        project_name=cast(str | None, args.project_name),
    )


def _add_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--compose-file", default="deploy/compose.yaml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--profile", choices=sorted(RELEASE_PROFILES), required=True)
    parser.add_argument("--project-name")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Hub immutable release and rollback gates")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    check = subparsers.add_parser("check-migrations")
    check.add_argument("--project-root", default=".")
    check.add_argument("--previous-head", action="append", default=[])
    check.add_argument("--target-head", action="append", default=[])
    check.add_argument("--allow-contract", action="store_true")

    create = subparsers.add_parser("create-manifest")
    create.add_argument("--project-root", default=".")
    create.add_argument("--release-id", required=True)
    create.add_argument("--environment", choices=sorted(RELEASE_ENVIRONMENTS), required=True)
    create.add_argument("--profile", choices=sorted(RELEASE_PROFILES), required=True)
    create.add_argument("--platform-image", required=True)
    create.add_argument("--portal-image", required=True)
    create.add_argument("--backup-receipt", required=True)
    create.add_argument("--previous-manifest", required=True)
    create.add_argument("--gate", action="append", default=[])
    create.add_argument("--approved-by", required=True)
    create.add_argument("--allow-contract", action="store_true")
    create.add_argument("--risk", action="append", default=[])
    create.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify-manifest")
    verify.add_argument("manifest")
    verify.add_argument("--project-root", default=".")
    verify.add_argument("--verify-repository-digests", action="store_true")

    for name in ("preflight", "canary", "promote"):
        operation = subparsers.add_parser(name)
        operation.add_argument("manifest")
        operation.add_argument("--project-root", default=".")
        _add_target_arguments(operation)

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("manifest")
    _add_target_arguments(rollback)
    return parser


def run_command(args: argparse.Namespace) -> dict[str, Any]:
    operation = str(args.operation)
    if operation == "check-migrations":
        project_root = Path(args.project_root).resolve()
        previous = _parse_assignment(cast(list[str], args.previous_head), "--previous-head")
        target_values = _parse_assignment(cast(list[str], args.target_head), "--target-head")
        target = target_values or migration_heads(project_root)
        transitions = validate_migration_transition(
            project_root,
            previous,
            target,
            allow_contract=bool(args.allow_contract),
        )
        return {
            "passed": True,
            "transitions": {key: asdict(value) for key, value in transitions.items()},
        }
    if operation == "create-manifest":
        project_root = Path(args.project_root).resolve()
        manifest = create_release_manifest(
            project_root=project_root,
            release_id=str(args.release_id),
            environment=str(args.environment),
            profile=str(args.profile),
            platform_image=str(args.platform_image),
            portal_image=str(args.portal_image),
            backup_receipt=Path(args.backup_receipt).resolve(),
            previous_manifest_path=Path(args.previous_manifest).resolve(),
            gate_paths={
                key: Path(value).resolve()
                for key, value in _parse_assignment(cast(list[str], args.gate), "--gate").items()
            },
            approved_by=str(args.approved_by),
            allow_contract=bool(args.allow_contract),
            risks=cast(list[str], args.risk),
        )
        output = Path(args.output).resolve()
        write_release_manifest(output, manifest)
        return {
            "created": True,
            "release_id": manifest["release_id"],
            "manifest": str(output),
            "manifest_sha256": _sha256_text(output),
        }
    if operation == "verify-manifest":
        manifest = _load_json_object(Path(args.manifest).resolve())
        validate_release_manifest(
            manifest,
            project_root=Path(args.project_root).resolve(),
            verify_repository_digests=bool(args.verify_repository_digests),
        )
        return {"verified": True, "release_id": manifest["release_id"]}
    target = _target_from_args(args)
    manifest_path = Path(args.manifest).resolve()
    if operation == "preflight":
        return release_preflight(
            manifest_path,
            target,
            Path(args.project_root).resolve(),
        )
    if operation == "canary":
        return run_canary(
            manifest_path,
            target,
            Path(args.project_root).resolve(),
        )
    if operation == "promote":
        return promote_release(
            manifest_path,
            target,
            Path(args.project_root).resolve(),
        )
    if operation == "rollback":
        return rollback_release(manifest_path, target)
    raise ReleaseError(f"Unsupported release operation: {operation}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = run_command(args)
    except ReleaseError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
