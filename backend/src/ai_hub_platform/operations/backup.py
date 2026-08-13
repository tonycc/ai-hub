from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"AIHUBBKP1"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
DATABASES = ("authentik_db", "platform_db", "standalone_app_db")
BASE_REQUIRED_ROLES = (
    "authentik",
    "ai_hub_platform_migrator",
    "ai_hub_platform",
    "ai_hub_projection_migrator",
    "ai_hub_projection",
    "standalone_app_migrator",
    "standalone_app",
)
EVENT_REQUIRED_ROLES = (
    "standalone_outbox_publisher",
    "standalone_event_consumer",
)
MIGRATION_TABLES = (
    ("platform_db", "platform_core.alembic_version"),
    ("platform_db", "platform_core.alembic_version_events"),
    ("platform_db", "platform_projection.alembic_version"),
    ("standalone_app_db", "app.alembic_version"),
    ("standalone_app_db", "app.alembic_version_event_publisher"),
    ("standalone_app_db", "app.alembic_version_event_consumer"),
)


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ComposeTarget:
    compose_file: Path
    env_file: Path
    profile: str
    project_name: str | None

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


def _run(
    command: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(  # noqa: S603
        command,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise BackupError(f"Command failed ({command[0]}): {detail}")
    return result


def _compose_exec(target: ComposeTarget, *arguments: str) -> bytes:
    return _run(target.command("exec", "-T", "postgres", *arguments)).stdout


def _key_from_environment() -> bytes:
    encoded = os.environ.get("AI_HUB_BACKUP_KEY_BASE64", "")
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise BackupError("AI_HUB_BACKUP_KEY_BASE64 must be valid base64") from error
    if len(key) != 32:
        raise BackupError("AI_HUB_BACKUP_KEY_BASE64 must decode to exactly 32 bytes")
    return key


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def encrypt_file(source: Path, destination: Path, key: bytes) -> None:
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        output_file.write(MAGIC)
        output_file.write(nonce)
        while chunk := input_file.read(CHUNK_BYTES):
            output_file.write(encryptor.update(chunk))
        output_file.write(encryptor.finalize())
        output_file.write(encryptor.tag)


def decrypt_file(source: Path, destination: Path, key: bytes) -> None:
    minimum_size = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if source.stat().st_size <= minimum_size:
        raise BackupError("Encrypted backup is truncated")
    with source.open("rb") as input_file:
        magic = input_file.read(len(MAGIC))
        if magic != MAGIC:
            raise BackupError("Encrypted backup has an unsupported format")
        nonce = input_file.read(NONCE_BYTES)
        input_file.seek(-TAG_BYTES, os.SEEK_END)
        tag = input_file.read(TAG_BYTES)
        ciphertext_bytes = source.stat().st_size - minimum_size
        input_file.seek(len(MAGIC) + NONCE_BYTES)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        remaining = ciphertext_bytes
        try:
            with destination.open("wb") as output_file:
                while remaining:
                    chunk = input_file.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise BackupError("Encrypted backup ciphertext is truncated")
                    remaining -= len(chunk)
                    output_file.write(decryptor.update(chunk))
                output_file.write(decryptor.finalize())
        except InvalidTag as error:
            destination.unlink(missing_ok=True)
            raise BackupError("Encrypted backup authentication failed") from error
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def _safe_extract(archive: Path, destination: Path) -> None:
    resolved_destination = destination.resolve()
    with tarfile.open(archive, "r") as bundle:
        members = bundle.getmembers()
        for member in members:
            member_path = (destination / member.name).resolve()
            if (
                resolved_destination not in member_path.parents
                and member_path != resolved_destination
            ):
                raise BackupError("Backup archive contains an unsafe path")
            if not member.isfile():
                raise BackupError("Backup archive may only contain regular files")
        bundle.extractall(destination, members=members, filter="data")


def _psql_scalar(target: ComposeTarget, database: str, sql: str) -> str:
    output = _compose_exec(
        target,
        "psql",
        "--username=postgres",
        f"--dbname={database}",
        "--tuples-only",
        "--no-align",
        "--set=ON_ERROR_STOP=1",
        f"--command={sql}",
    )
    return output.decode("utf-8").strip()


def _migration_versions(target: ComposeTarget) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for database, table_name in MIGRATION_TABLES:
        present = _psql_scalar(
            target,
            database,
            f"SELECT to_regclass('{table_name}') IS NOT NULL;",
        )
        versions[table_name] = (
            _psql_scalar(target, database, f"SELECT version_num FROM {table_name};")
            if present == "t"
            else None
        )
    return versions


def _database_dump(target: ComposeTarget, database: str, destination: Path) -> None:
    command = target.command(
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "--username=postgres",
        f"--dbname={database}",
        "--format=custom",
        "--create",
        "--compress=6",
        "--no-password",
    )
    with destination.open("wb") as output_file:
        result = subprocess.run(  # noqa: S603
            command,
            stdout=output_file,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise BackupError(f"pg_dump failed for {database}: {detail}")


def role_names_for_profile(profile: str) -> tuple[str, ...]:
    if profile == "standard-events":
        return (*BASE_REQUIRED_ROLES, *EVENT_REQUIRED_ROLES)
    return BASE_REQUIRED_ROLES


def _role_inventory(target: ComposeTarget) -> list[dict[str, object]]:
    required_roles = role_names_for_profile(target.profile)
    names = ", ".join(f"'{name}'" for name in required_roles)
    output = _psql_scalar(
        target,
        "postgres",
        (
            "SELECT rolname || '|' || rolcanlogin || '|' || rolsuper || '|' || "
            "rolcreatedb || '|' || rolcreaterole || '|' || rolreplication "
            f"FROM pg_roles WHERE rolname IN ({names}) ORDER BY rolname;"
        ),
    )
    result: list[dict[str, object]] = []
    for line in output.splitlines():
        name, login, superuser, create_db, create_role, replication = line.split("|")
        result.append(
            {
                "name": name,
                "login": login == "t",
                "superuser": superuser == "t",
                "create_database": create_db == "t",
                "create_role": create_role == "t",
                "replication": replication == "t",
            }
        )
    discovered = {str(role["name"]) for role in result}
    missing = set(required_roles) - discovered
    if missing:
        raise BackupError(f"Backup source is missing initialized roles: {sorted(missing)}")
    return result


def _manifest_files(directory: Path, filenames: Iterable[str]) -> dict[str, dict[str, object]]:
    return {
        filename: {
            "sha256": sha256_file(directory / filename),
            "size_bytes": (directory / filename).stat().st_size,
        }
        for filename in filenames
    }


def create_backup(args: argparse.Namespace) -> dict[str, object]:
    key = _key_from_environment()
    target = _target_from_args(args)
    output_directory = Path(args.output_dir).resolve()
    output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.now(UTC)
    backup_id = f"ai-hub-backup-{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    archive_path = output_directory / f"{backup_id}.tar.aesgcm"
    with tempfile.TemporaryDirectory(prefix="ai-hub-backup-") as temporary:
        staging = Path(temporary)
        dump_names: list[str] = []
        for database in DATABASES:
            filename = f"{database}.dump"
            _database_dump(target, database, staging / filename)
            dump_names.append(filename)
        globals_name = "globals.sql"
        globals_bytes = _compose_exec(
            target,
            "pg_dumpall",
            "--username=postgres",
            "--globals-only",
            "--no-role-passwords",
        )
        (staging / globals_name).write_bytes(globals_bytes)
        included_files = [*dump_names, globals_name]
        manifest = {
            "schema_version": 1,
            "backup_id": backup_id,
            "created_at": now.isoformat(),
            "profile": target.profile,
            "storage_class": args.storage_class,
            "databases": list(DATABASES),
            "roles": _role_inventory(target),
            "migration_versions": _migration_versions(target),
            "files": _manifest_files(staging, included_files),
            "recovery_model": {
                "authoritative": ["authentik_db", "platform_db", "standalone_app_db"],
                "rebuildable": ["RabbitMQ queues", "platform_projection"],
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tar_path = staging / f"{backup_id}.tar"
        with tarfile.open(tar_path, "w") as bundle:
            for filename in ["manifest.json", *included_files]:
                bundle.add(staging / filename, arcname=filename, recursive=False)
        temporary_archive = output_directory / f".{backup_id}.partial"
        try:
            encrypt_file(tar_path, temporary_archive, key)
            os.chmod(temporary_archive, 0o600)
            temporary_archive.replace(archive_path)
        finally:
            temporary_archive.unlink(missing_ok=True)
    archive_sha256 = sha256_file(archive_path)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{archive_sha256}  {archive_path.name}\n", encoding="utf-8")
    os.chmod(checksum_path, 0o600)
    return {
        "created": True,
        "backup_id": backup_id,
        "archive": str(archive_path),
        "sha256": archive_sha256,
        "storage_class": args.storage_class,
    }


def _read_bundle(archive_path: Path, key: bytes, destination: Path) -> dict[str, Any]:
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    if not checksum_path.is_file():
        raise BackupError("Backup checksum sidecar is missing")
    checksum_fields = checksum_path.read_text(encoding="utf-8").split()
    if len(checksum_fields) != 2 or checksum_fields[1] != archive_path.name:
        raise BackupError("Backup checksum sidecar is invalid")
    expected_archive_hash = checksum_fields[0]
    if sha256_file(archive_path) != expected_archive_hash:
        raise BackupError("Encrypted backup SHA-256 does not match its sidecar")
    decrypted_tar = destination / "bundle.tar"
    decrypt_file(archive_path, decrypted_tar, key)
    extracted = destination / "extracted"
    extracted.mkdir()
    _safe_extract(decrypted_tar, extracted)
    try:
        raw_manifest = json.loads((extracted / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackupError("Backup manifest cannot be read") from error
    if not isinstance(raw_manifest, dict):
        raise BackupError("Backup manifest is missing or unsupported")
    manifest = cast(dict[str, Any], raw_manifest)
    if manifest.get("schema_version") != 1:
        raise BackupError("Backup manifest is missing or unsupported")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict):
        raise BackupError("Backup manifest file inventory is invalid")
    files = cast(dict[str, Any], raw_files)
    expected_files = {"globals.sql", *(f"{database}.dump" for database in DATABASES)}
    if set(files) != expected_files:
        raise BackupError("Backup manifest file inventory is incomplete or unexpected")
    for filename, raw_metadata_value in files.items():
        if not isinstance(raw_metadata_value, dict):
            raise BackupError("Backup manifest file entry is invalid")
        raw_metadata = cast(dict[str, Any], raw_metadata_value)
        file_path = extracted / filename
        if not file_path.is_file() or sha256_file(file_path) != raw_metadata.get("sha256"):
            raise BackupError(f"Backup member failed SHA-256 verification: {filename}")
    manifest["_extracted_path"] = str(extracted)
    return manifest


def verify_backup(args: argparse.Namespace) -> dict[str, object]:
    archive_path = Path(args.archive).resolve()
    with tempfile.TemporaryDirectory(prefix="ai-hub-backup-verify-") as temporary:
        manifest = _read_bundle(archive_path, _key_from_environment(), Path(temporary))
    return {
        "verified": True,
        "backup_id": manifest["backup_id"],
        "created_at": manifest["created_at"],
        "storage_class": manifest["storage_class"],
        "databases": manifest["databases"],
    }


def _assert_restore_state(target: ComposeTarget) -> None:
    running = _run(
        target.command("ps", "--services", "--filter", "status=running")
    ).stdout.decode("utf-8").split()
    if running != ["postgres"]:
        raise BackupError(
            "Restore requires an isolated stack with only the postgres service running"
        )


def _assert_restore_roles(target: ComposeTarget, manifest: dict[str, Any]) -> None:
    if manifest.get("profile") != target.profile:
        raise BackupError("Restore profile must match the backup profile")
    raw_roles_value = manifest.get("roles")
    if not isinstance(raw_roles_value, list):
        raise BackupError("Backup role inventory is invalid")
    raw_roles = cast(list[object], raw_roles_value)
    required_roles: set[str] = set()
    allowed_roles = set((*BASE_REQUIRED_ROLES, *EVENT_REQUIRED_ROLES))
    for raw_role_value in raw_roles:
        if not isinstance(raw_role_value, dict):
            raise BackupError("Backup role inventory entry is invalid")
        raw_role = cast(dict[str, Any], raw_role_value)
        if not isinstance(raw_role.get("name"), str):
            raise BackupError("Backup role inventory entry is invalid")
        role_name = str(raw_role["name"])
        if role_name not in allowed_roles:
            raise BackupError(f"Backup role inventory contains an unexpected role: {role_name}")
        required_roles.add(role_name)
    expected_roles = set(role_names_for_profile(str(manifest.get("profile", ""))))
    if required_roles != expected_roles:
        raise BackupError("Backup role inventory does not match its deployment profile")
    names = ", ".join(f"'{name}'" for name in sorted(required_roles))
    present = set(
        _psql_scalar(
            target,
            "postgres",
            f"SELECT rolname FROM pg_roles WHERE rolname IN ({names}) ORDER BY rolname;",
        ).splitlines()
    )
    missing = required_roles - present
    if missing:
        raise BackupError(f"Restore target is missing initialized roles: {sorted(missing)}")


def _restore_dump(target: ComposeTarget, dump_path: Path) -> None:
    command = target.command(
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "--username=postgres",
        "--dbname=postgres",
        "--clean",
        "--if-exists",
        "--create",
        "--exit-on-error",
        "--no-password",
    )
    with dump_path.open("rb") as input_file:
        result = subprocess.run(  # noqa: S603
            command,
            stdin=input_file,
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise BackupError(f"pg_restore failed: {detail}")


def restore_backup(args: argparse.Namespace) -> dict[str, object]:
    if not args.confirm_replace:
        raise BackupError("Restore requires --confirm-replace")
    target = _target_from_args(args)
    _assert_restore_state(target)
    archive_path = Path(args.archive).resolve()
    started_at = datetime.now(UTC)
    restore_lock = archive_path.with_suffix(archive_path.suffix + ".restore-lock")
    restore_lock.write_text(started_at.isoformat() + "\n", encoding="utf-8")
    try:
        with tempfile.TemporaryDirectory(prefix="ai-hub-backup-restore-") as temporary:
            manifest = _read_bundle(archive_path, _key_from_environment(), Path(temporary))
            _assert_restore_roles(target, manifest)
            extracted = Path(str(manifest.pop("_extracted_path")))
            for database in DATABASES:
                _restore_dump(target, extracted / f"{database}.dump")
            actual_versions = _migration_versions(target)
            if actual_versions != manifest["migration_versions"]:
                raise BackupError("Restored migration versions do not match the backup manifest")
            for database in DATABASES:
                if _psql_scalar(target, database, "SELECT 1;") != "1":
                    raise BackupError(f"Restored database is not queryable: {database}")
            duration_seconds = round((datetime.now(UTC) - started_at).total_seconds(), 3)
            return {
                "restored": True,
                "backup_id": manifest["backup_id"],
                "duration_seconds": duration_seconds,
                "migration_versions": manifest["migration_versions"],
            }
    finally:
        restore_lock.unlink(missing_ok=True)


def _backup_timestamp(path: Path) -> datetime | None:
    parts = path.name.split("-")
    if len(parts) < 5:
        return None
    try:
        return datetime.strptime(parts[3], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def retention_selection(
    backups: Sequence[tuple[Path, datetime]],
    *,
    hourly_count: int,
    daily_days: int,
    now: datetime,
) -> tuple[set[Path], set[Path]]:
    ordered = sorted(backups, key=lambda item: item[1], reverse=True)
    keep = {path for path, _ in ordered[:hourly_count]}
    earliest_daily = now - timedelta(days=daily_days)
    daily_seen: set[str] = set()
    for path, created_at in ordered:
        if created_at < earliest_daily:
            continue
        day = created_at.astimezone(UTC).date().isoformat()
        if day not in daily_seen:
            keep.add(path)
            daily_seen.add(day)
    delete = {path for path, _ in ordered} - keep
    return keep, delete


def prune_backups(args: argparse.Namespace) -> dict[str, object]:
    directory = Path(args.directory).resolve()
    targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    retention = targets["retention"]
    candidates = [
        (path, timestamp)
        for path in directory.glob("ai-hub-backup-*.tar.aesgcm")
        if (timestamp := _backup_timestamp(path)) is not None
    ]
    keep, delete = retention_selection(
        candidates,
        hourly_count=int(retention["backup_hourly_count"]),
        daily_days=int(retention["backup_daily_days"]),
        now=datetime.now(UTC),
    )
    deleted: list[str] = []
    skipped: list[str] = []
    if args.apply:
        for path in sorted(delete):
            checksum = path.with_suffix(path.suffix + ".sha256")
            restore_lock = path.with_suffix(path.suffix + ".restore-lock")
            if restore_lock.exists() or not checksum.is_file():
                skipped.append(path.name)
                continue
            expected = checksum.read_text(encoding="utf-8").split()[0]
            if sha256_file(path) != expected:
                skipped.append(path.name)
                continue
            path.unlink()
            checksum.unlink()
            deleted.append(path.name)
    return {
        "applied": bool(args.apply),
        "kept": sorted(path.name for path in keep),
        "delete_candidates": sorted(path.name for path in delete),
        "deleted": deleted,
        "skipped": skipped,
    }


def _target_from_args(args: argparse.Namespace) -> ComposeTarget:
    return ComposeTarget(
        compose_file=Path(args.compose_file).resolve(),
        env_file=Path(args.env_file).resolve(),
        profile=args.profile,
        project_name=args.project_name,
    )


def _add_compose_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--compose-file", default="deploy/compose.yaml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--profile", choices=("base-access", "standard-events"), required=True)
    parser.add_argument("--project-name")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypted AI Hub backup and restore")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    create = subparsers.add_parser("create")
    _add_compose_arguments(create)
    create.add_argument("--output-dir", required=True)
    create.add_argument("--storage-class", choices=("off-host", "local-drill"), required=True)
    create.set_defaults(handler=create_backup)

    verify = subparsers.add_parser("verify")
    verify.add_argument("archive")
    verify.set_defaults(handler=verify_backup)

    restore = subparsers.add_parser("restore")
    _add_compose_arguments(restore)
    restore.add_argument("archive")
    restore.add_argument("--confirm-replace", action="store_true")
    restore.set_defaults(handler=restore_backup)

    prune = subparsers.add_parser("prune")
    prune.add_argument("--directory", required=True)
    prune.add_argument("--targets", default="deploy/operations/production-targets.json")
    prune.add_argument("--apply", action="store_true")
    prune.set_defaults(handler=prune_backups)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.operation == "create" and args.storage_class == "local-drill":
        environment = os.environ.get("AI_HUB_ENVIRONMENT", "local")
        if environment not in {"local", "test"}:
            parser.error("local-drill backup storage is forbidden outside local/test")
    try:
        result = args.handler(args)
    except BackupError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
