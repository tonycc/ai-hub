"""Semantic metamodel, loader, digests and the seed package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from ai_hub_platform.modules.semantic.loader import (
    PackageLoadError,
    load_package_dir,
    load_package_document,
    load_package_text,
)
from ai_hub_platform.modules.semantic.metamodel import Property, SemanticPackage
from ai_hub_platform.modules.semantic.versioning import binding_digest, package_digest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED = PROJECT_ROOT / "semantic-models/manufacturing"
FINGERPRINT = "a" * 64


def package_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "package_id": "demo",
        "display_name": "Demo",
        "version": "1.0.0",
        "objects": [
            {
                "semantic_id": "demo.item",
                "display_name": "Item",
                "keys": ["item_code"],
                "properties": [
                    {"name": "item_code", "type": "string", "required": True},
                    {"name": "name", "type": "string"},
                    {"name": "state", "type": "enum", "enum_values": ["open", "closed"]},
                ],
            },
            {
                "semantic_id": "demo.order_line",
                "display_name": "Order line",
                "keys": ["line_id"],
                "properties": [
                    {"name": "line_id", "type": "string", "required": True},
                    {"name": "item", "type": "ref", "ref": "demo.item"},
                ],
            },
        ],
        "relations": [
            {
                "semantic_id": "demo.order_line_item",
                "source_object": "demo.order_line",
                "target_object": "demo.item",
                "source_property": "item",
            }
        ],
        "bindings": [
            {
                "binding_id": "demo.item@erp",
                "semantic_object": "demo.item",
                "source_application_id": "erp",
                "object_type": "erp.item",
                "payload_contract_version": "item.v1",
                "contract_fingerprint": FINGERPRINT,
                "field_map": {
                    "item_code": {"source_field": "ITEM_CODE"},
                    "name": {"source_field": "ITEM_NAME"},
                    "state": {
                        "source_field": "STATE",
                        "value_map": {"O": "open", "C": "closed"},
                    },
                },
            }
        ],
    }
    document.update(overrides)
    return document


def load(**overrides: Any) -> SemanticPackage:
    return load_package_document(package_document(**overrides))


def errors_of(**overrides: Any) -> str:
    with pytest.raises(PackageLoadError) as raised:
        load(**overrides)
    return "\n".join(raised.value.errors)


def test_valid_package_loads() -> None:
    package = load()
    assert [obj.semantic_id for obj in package.objects] == ["demo.item", "demo.order_line"]
    assert package.cross_validate() == []


def test_property_rules() -> None:
    with pytest.raises(ValueError, match="requires a ref target"):
        Property(name="x", type="ref")
    with pytest.raises(ValueError, match="requires enum_values"):
        Property(name="x", type="enum")
    with pytest.raises(ValueError, match="requires sensitive=true"):
        Property(name="x", type="string", classification="restricted")
    assert Property(name="x", type="string", classification="restricted", sensitive=True)


def test_object_keys_must_be_required_properties() -> None:
    document = package_document()
    document["objects"][0]["keys"] = ["missing"]
    assert "key 'missing' is not a property" in errors_of(objects=document["objects"])
    document = package_document()
    document["objects"][0]["properties"][0]["required"] = False
    assert "key 'item_code' must be required" in errors_of(objects=document["objects"])


def test_ids_must_be_namespaced_and_unique() -> None:
    document = package_document()
    document["objects"][1]["semantic_id"] = "other.order_line"
    message = errors_of(objects=document["objects"])
    assert "outside package namespace demo" in message


def test_references_must_resolve() -> None:
    document = package_document()
    document["objects"][1]["properties"][1]["ref"] = "demo.missing"
    message = errors_of(objects=document["objects"])
    assert "ref target demo.missing undefined" in message
    assert "must be a ref to demo.item" in message


def test_binding_rules() -> None:
    binding = package_document()["bindings"][0]

    unknown = {**binding, "field_map": {**binding["field_map"], "ghost": {"source_field": "X"}}}
    assert "'ghost' is not a property" in errors_of(bindings=[unknown])

    unmapped = {**binding, "field_map": {"name": {"source_field": "ITEM_NAME"}}}
    assert "required property 'item_code' is not mapped" in errors_of(bindings=[unmapped])

    bad_enum = {
        **binding,
        "field_map": {
            **binding["field_map"],
            "state": {"source_field": "STATE", "value_map": {"O": "unknown"}},
        },
    }
    assert "undeclared enum values ['unknown']" in errors_of(bindings=[bad_enum])

    second = {**binding, "binding_id": "demo.item@mes", "source_application_id": "mes"}
    assert "more than one active binding" in errors_of(bindings=[binding, second])
    assert load(bindings=[binding, {**second, "deprecated": True}])


@pytest.mark.parametrize(
    "source_field",
    ["ITEM.ITEM_CODE (join X.Y)", "select * from item", "http://example.test/x", ""],
)
def test_binding_source_is_a_payload_field_not_an_expression(source_field: str) -> None:
    binding = package_document()["bindings"][0]
    binding["field_map"]["name"] = {"source_field": source_field}
    assert "must be a payload field path" in errors_of(bindings=[binding])


def test_unknown_fields_are_rejected() -> None:
    binding = {**package_document()["bindings"][0], "tables": ["ITEM"]}
    assert "tables" in errors_of(bindings=[binding])


def test_digest_ignores_layout_but_tracks_content() -> None:
    package = load()
    reordered = load_package_text(json.dumps(package_document(), sort_keys=True, indent=4), "json")
    assert package_digest(reordered) == package_digest(package)
    assert package_digest(package).startswith("sha256:")

    changed = package_document()
    changed["objects"][0]["properties"][1]["type"] = "text"
    assert package_digest(load(objects=changed["objects"])) != package_digest(package)

    binding = package.bindings[0]
    remapped = binding.model_copy(update={"mapping_version": 2})
    assert binding_digest(remapped) != binding_digest(binding)


def test_yaml_and_json_documents_are_equivalent() -> None:
    import yaml

    document = package_document()
    from_yaml = load_package_text(yaml.safe_dump(document), "yaml")
    from_json = load_package_text(json.dumps(document), "json")
    assert package_digest(from_yaml) == package_digest(from_json)
    with pytest.raises(PackageLoadError, match="invalid YAML"):
        load_package_text("objects: [", "yaml")
    with pytest.raises(PackageLoadError, match="top level must be a mapping"):
        load_package_text("[1, 2]", "json")


def test_directory_loader_skips_hidden_files(tmp_path: Path) -> None:
    import yaml

    document = package_document()
    (tmp_path / "objects").mkdir()
    (tmp_path / "bindings").mkdir()
    meta = {key: document[key] for key in ("package_id", "display_name", "version")}
    (tmp_path / "package.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    for index, obj in enumerate(document["objects"]):
        (tmp_path / "objects" / f"{index}.yaml").write_text(yaml.safe_dump(obj), encoding="utf-8")
    (tmp_path / "objects" / "._0.yaml").write_bytes(b"\x00\x05\x16\x07binary")
    (tmp_path / "relations.yaml").write_text(
        yaml.safe_dump({"relations": document["relations"]}), encoding="utf-8"
    )
    (tmp_path / "bindings" / "erp.yaml").write_text(
        yaml.safe_dump({"bindings": document["bindings"]}), encoding="utf-8"
    )
    assert package_digest(load_package_dir(tmp_path)) == package_digest(load())

    with pytest.raises(PackageLoadError, match=r"package\.yaml not found"):
        load_package_dir(tmp_path / "objects")


def test_seed_package_is_valid_and_carries_no_physical_bindings() -> None:
    package = load_package_dir(SEED)
    assert package.package_id == "manufacturing"
    assert {obj.semantic_id for obj in package.objects} == {
        "manufacturing.material",
        "manufacturing.customer",
        "manufacturing.sales_order",
        "manufacturing.sales_order_line",
        "manufacturing.quotation",
    }
    # Seeds define meaning only: sources are bound per environment after review.
    assert package.bindings == []
    assert all(metric.status == "DRAFT" for metric in package.metrics)
    cost = package.object_map()["manufacturing.material"].property_map()["standard_cost"]
    assert cost.sensitive and cost.classification == "confidential"


def test_copy_manifest_matches_upstream_and_flags_modified_files() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "third_party/data2agent/copy-manifest.json").read_text(encoding="utf-8")
    )
    for entry in manifest["files"]:
        destination = PROJECT_ROOT / entry["destination_path"]
        assert destination.is_file(), entry["destination_path"]
        current = hashlib.sha256(destination.read_bytes()).hexdigest()
        unchanged = current == entry["source_sha256"]
        assert entry["modified"] is not unchanged, entry["destination_path"]
        assert bool(entry["modification_summary"]) is entry["modified"]
        if entry["modified"] and destination.suffix == ".py":
            head = destination.read_text(encoding="utf-8")[:400]
            assert "SPDX-License-Identifier: Apache-2.0" in head
            assert entry["source_path"] in head


def test_semantic_module_does_not_import_other_business_modules() -> None:
    sources = (PROJECT_ROOT / "backend/src/ai_hub_platform/modules/semantic").glob("*.py")
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith(("from ai_hub_platform", "import ai_hub_platform")):
                assert "ai_hub_platform.modules.semantic" in line, f"{source.name}: {line}"
