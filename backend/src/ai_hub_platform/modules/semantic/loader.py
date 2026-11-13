# SPDX-License-Identifier: Apache-2.0
# Derived from data2agent (xingyekuo-spec/data2agent @ 60bcbe2),
# data2agent/shared/metamodel/loader.py. Modified by AI Hub; see
# third_party/data2agent/copy-manifest.json for the change summary.
"""Load a semantic package from a document or from a package directory.

A document is one JSON/YAML mapping in the ``SemanticPackage`` shape; it is
what the management API imports and exports. A directory splits the same
content into files so seed packages stay reviewable::

    package.yaml            package_id, display_name, version, owner
    objects/*.yaml          one object per file
    relations.yaml          relations: [...]
    metrics/*.yaml          metrics: [...]
    bindings/*.yaml         bindings: [...]
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import ValidationError

from ai_hub_platform.modules.semantic.metamodel import SemanticPackage

DocumentFormat = Literal["json", "yaml"]


class PackageLoadError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def _validation_messages(prefix: str, error: ValidationError) -> list[str]:
    messages: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        where = f"{prefix}{location}" if location else prefix.rstrip(": ")
        messages.append(f"{where}: {item['msg']}")
    return messages


def load_package_document(document: Mapping[str, Any]) -> SemanticPackage:
    """Build and cross-validate a package from an already parsed mapping."""
    try:
        package = SemanticPackage.model_validate(dict(document))
    except ValidationError as error:
        raise PackageLoadError(_validation_messages("", error)) from error
    cross = package.cross_validate()
    if cross:
        raise PackageLoadError(cross)
    return package


def _parse_mapping(text: str, document_format: DocumentFormat, name: str) -> dict[str, Any]:
    try:
        parsed: object = json.loads(text) if document_format == "json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise PackageLoadError([f"{name}: invalid {document_format.upper()}: {error}"]) from error
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise PackageLoadError([f"{name}: top level must be a mapping"])
    return cast(dict[str, Any], parsed)


def load_package_text(text: str, document_format: DocumentFormat) -> SemanticPackage:
    return load_package_document(_parse_mapping(text, document_format, "document"))


def _read_yaml_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise PackageLoadError([f"{path.name}: file is not UTF-8 encoded ({error})"]) from error
    return _parse_mapping(text, "yaml", path.name)


def _yaml_files(directory: Path) -> list[Path]:
    """YAML files in name order, skipping hidden and macOS AppleDouble (``._*``) files."""
    if not directory.is_dir():
        return []
    return [path for path in sorted(directory.glob("*.yaml")) if not path.name.startswith(".")]


def _listed(path: Path, key: str) -> list[Any]:
    items = _read_yaml_file(path).get(key, [])
    if not isinstance(items, list):
        raise PackageLoadError([f"{path.name}: '{key}' must be a list"])
    return cast(list[Any], items)


def load_package_dir(root: str | Path) -> SemanticPackage:
    base = Path(root)
    package_file = base / "package.yaml"
    if not package_file.is_file():
        raise PackageLoadError([f"{base}: package.yaml not found"])
    document = _read_yaml_file(package_file)
    for reserved in ("objects", "relations", "metrics", "bindings"):
        if reserved in document:
            raise PackageLoadError(
                [f"package.yaml: '{reserved}' belongs in its own file or directory"]
            )

    document["objects"] = [_read_yaml_file(path) for path in _yaml_files(base / "objects")]
    relations_file = base / "relations.yaml"
    document["relations"] = _listed(relations_file, "relations") if relations_file.is_file() else []
    document["metrics"] = [
        item for path in _yaml_files(base / "metrics") for item in _listed(path, "metrics")
    ]
    document["bindings"] = [
        item for path in _yaml_files(base / "bindings") for item in _listed(path, "bindings")
    ]
    return load_package_document(document)
