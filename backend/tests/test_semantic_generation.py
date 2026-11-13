"""Draft generation from ingest contracts, validation and lifecycle gates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from ai_hub_platform.modules.semantic.generator import (
    ContractInput,
    GenerationError,
    generate_draft_package,
    snake_case,
)
from ai_hub_platform.modules.semantic.lifecycle import (
    LifecycleError,
    PackageVersionState,
    deprecate,
    is_consumable,
    new_version_state,
    publish,
    retire,
    review,
    verify,
    verify_binding,
)
from ai_hub_platform.modules.semantic.loader import load_package_document
from ai_hub_platform.modules.semantic.metamodel import SemanticPackage
from ai_hub_platform.modules.semantic.validator import (
    ContractKey,
    ContractView,
    breaking_changes,
    compare_packages,
    has_errors,
    validate_package,
)
from ai_hub_platform.modules.semantic.versioning import package_digest

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
ITEM_FP = "1" * 64
LINE_FP = "2" * 64

ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ITEM_CODE": {"type": "string"},
        "ITEM_NAME": {"type": "string", "description": "品名"},
        "STANDARD_COST": {"type": ["number", "null"]},
        "STATUS": {"type": "string", "enum": ["A", "I"]},
        "CREATED_AT": {"type": "string", "format": "date-time"},
        "EXTRA": {"type": "object"},
    },
    "required": ["ITEM_CODE", "ITEM_NAME"],
    "additionalProperties": False,
}
LINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ORDER_NO": {"type": "string"},
        "LINE_NO": {"type": "string"},
        "ITEM_CODE": {"type": "string", "x-ai-hub-ref": "erp.item"},
        "QTY": {"type": "string"},
        "WAREHOUSE": {"type": "string", "x-ai-hub-ref": "erp.warehouse"},
    },
    "required": ["ORDER_NO", "LINE_NO", "ITEM_CODE", "QTY"],
}


def item_contract(**overrides: Any) -> ContractInput:
    values: dict[str, Any] = {
        "source_application_id": "erp-adapter",
        "object_type": "erp.item",
        "contract_version": "item.v1",
        "json_schema": ITEM_SCHEMA,
        "schema_fingerprint": ITEM_FP,
    }
    values.update(overrides)
    return ContractInput(**values)


def line_contract() -> ContractInput:
    return ContractInput(
        source_application_id="erp-adapter",
        object_type="erp.sales_order_line",
        contract_version="sales-order-line.v1",
        json_schema=LINE_SCHEMA,
        schema_fingerprint=LINE_FP,
    )


def generate(**overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "package_id": "manufacturing",
        "display_name": "Manufacturing",
        "version": "0.1.0",
        "owner": "data-office",
        "key_fields": {
            "erp.item": ["ITEM_CODE"],
            "erp.sales_order_line": ["ORDER_NO", "LINE_NO"],
        },
    }
    arguments.update(overrides)
    return generate_draft_package([item_contract(), line_contract()], **arguments)


def contracts_for(
    *contracts: ContractInput, status: str = "ACTIVE"
) -> dict[ContractKey, ContractView]:
    views = [
        ContractView(
            source_application_id=contract.source_application_id,
            object_type=contract.object_type,
            contract_version=contract.contract_version,
            json_schema=contract.json_schema,
            schema_fingerprint=contract.schema_fingerprint,
            status=status,
        )
        for contract in contracts
    ]
    return {view.key: view for view in views}


def test_snake_case() -> None:
    assert snake_case("ITEM_CODE") == "item_code"
    assert snake_case("itemCode") == "item_code"
    assert snake_case(" Order-No. ") == "order_no"


def test_generates_objects_properties_keys_and_bindings() -> None:
    result = generate()
    package = result.package
    item = package.object_map()["manufacturing.item"]
    properties = item.property_map()

    assert item.keys == ["item_code"]
    assert item.owner_application == "erp-adapter"
    assert properties["item_code"].required and properties["item_name"].required
    assert properties["item_name"].display_name == "ITEM_NAME"
    assert properties["item_name"].description == "品名"
    assert properties["standard_cost"].type == "decimal"
    assert not properties["standard_cost"].required
    assert properties["status"].type == "enum"
    assert properties["status"].enum_values == ["A", "I"]
    assert properties["created_at"].type == "datetime"

    binding = next(b for b in package.bindings if b.semantic_object == "manufacturing.item")
    assert binding.binding_id == "manufacturing.item@erp-adapter"
    assert binding.contract_fingerprint == ITEM_FP
    assert binding.payload_contract_version == "item.v1"
    assert binding.field_map["item_code"].source_field == "ITEM_CODE"
    assert package.cross_validate() == []


def test_unrecognised_types_are_kept_with_a_warning_not_dropped() -> None:
    result = generate()
    extra = result.package.object_map()["manufacturing.item"].property_map()["extra"]
    assert extra.type == "text"
    assert any(
        warning.code == "type_unresolved" and warning.subject.endswith(".extra")
        for warning in result.warnings
    )


def test_relations_come_only_from_explicit_references() -> None:
    result = generate()
    package = result.package
    line = package.object_map()["manufacturing.sales_order_line"].property_map()

    assert line["item_code"].type == "ref"
    assert line["item_code"].ref == "manufacturing.item"
    assert [relation.semantic_id for relation in package.relations] == [
        "manufacturing.sales_order_line_item_code"
    ]
    relation = package.relations[0]
    assert (relation.source_object, relation.target_object) == (
        "manufacturing.sales_order_line",
        "manufacturing.item",
    )
    assert relation.source_binding_ref == "manufacturing.sales_order_line@erp-adapter"

    # ORDER_NO looks like a reference to an order but is not declared as one.
    assert line["order_no"].type == "string"
    # A declared reference to an object outside this generation is reported.
    assert line["warehouse"].type == "string"
    assert any(warning.code == "reference_unresolved" for warning in result.warnings)


def test_sensitivity_is_suggested_and_never_lowered() -> None:
    result = generate()
    cost = result.package.object_map()["manufacturing.item"].property_map()["standard_cost"]
    assert cost.sensitive and cost.classification == "confidential"
    assert any(warning.code == "sensitivity_suggested" for warning in result.warnings)

    classified = generate_draft_package(
        [
            item_contract(
                field_classifications={
                    "ITEM_NAME": "restricted",
                    "STATUS": {"classification": "internal", "sensitive": True},
                    "ITEM_CODE": "public",
                }
            )
        ],
        package_id="manufacturing",
        display_name="Manufacturing",
        version="0.1.0",
    )
    properties = classified.package.objects[0].property_map()
    assert properties["item_name"].classification == "restricted"
    assert properties["item_name"].sensitive
    assert properties["status"].sensitive
    assert properties["item_code"].classification == "public"
    assert not properties["item_code"].sensitive


def test_missing_business_key_falls_back_to_object_id_with_a_warning() -> None:
    result = generate(key_fields=None)
    item = result.package.object_map()["manufacturing.item"]
    assert item.keys == ["object_id"]
    binding = next(b for b in result.package.bindings if b.semantic_object == item.semantic_id)
    assert binding.field_map["object_id"].source_field == "$object_id"
    assert any(warning.code == "business_key_unconfirmed" for warning in result.warnings)


def test_generation_rejects_inputs_it_cannot_represent() -> None:
    with pytest.raises(GenerationError, match="at least one contract"):
        generate_draft_package([], package_id="m", display_name="M", version="1")
    with pytest.raises(GenerationError, match="key field 'NOPE'"):
        generate(key_fields={"erp.item": ["NOPE"]})
    with pytest.raises(GenerationError, match="no payload properties"):
        generate_draft_package(
            [item_contract(json_schema={"type": "object"})],
            package_id="m",
            display_name="M",
            version="1",
        )
    with pytest.raises(GenerationError, match="same semantic id"):
        generate_draft_package(
            [item_contract(), item_contract(object_type="mes.item")],
            package_id="m",
            display_name="M",
            version="1",
        )


def test_generated_package_validates_against_its_contracts() -> None:
    package = generate().package
    issues = validate_package(package, contracts_for(item_contract(), line_contract()))
    assert not has_errors(issues)


def test_validation_rejects_bindings_that_do_not_match_the_contract() -> None:
    package = generate().package
    item_only = contracts_for(item_contract())

    def codes(contracts: dict[ContractKey, ContractView], candidate: SemanticPackage) -> set[str]:
        return {
            issue.code
            for issue in validate_package(candidate, contracts)
            if issue.severity == "error"
        }

    assert codes(item_only, package) == {"contract_missing"}
    assert "contract_not_active" in codes(
        contracts_for(item_contract(), line_contract(), status="DEPRECATED"), package
    )
    drifted = contracts_for(item_contract(schema_fingerprint="9" * 64), line_contract())
    assert codes(drifted, package) == {"contract_fingerprint_mismatch"}

    document = package.model_dump(mode="json")
    item_binding = document["bindings"][0]
    item_binding["field_map"]["item_name"] = {"source_field": "NO_SUCH_COLUMN"}
    item_binding["field_map"]["created_at"] = {"source_field": "STANDARD_COST"}
    broken = load_package_document(document)
    assert codes(contracts_for(item_contract(), line_contract()), broken) == {
        "source_field_missing",
        "type_incompatible",
    }


def test_objects_without_a_binding_are_reported() -> None:
    document = generate().package.model_dump(mode="json")
    document["bindings"] = [
        binding
        for binding in document["bindings"]
        if binding["semantic_object"] != "manufacturing.item"
    ]
    document["relations"] = []
    issues = validate_package(
        load_package_document(document), contracts_for(item_contract(), line_contract())
    )
    assert [issue.subject for issue in issues if issue.code == "object_unbound"] == [
        "object:manufacturing.item"
    ]
    assert not has_errors(issues)


def _reviewed() -> tuple[SemanticPackage, PackageVersionState]:
    package = generate().package
    issues = validate_package(package, contracts_for(item_contract(), line_contract()))
    state = new_version_state(package, origin="GENERATED")
    return package, review(state, package, issues, actor="tech-reviewer", at=NOW)


def test_generated_versions_start_as_draft_and_cannot_skip_gates() -> None:
    package = generate().package
    state = new_version_state(package, origin="GENERATED")
    assert state.status == "DRAFT"
    assert not is_consumable(state)
    assert {binding.status for binding in state.bindings.values()} == {"DRAFT"}

    with pytest.raises(LifecycleError, match="verify requires status REVIEWED"):
        verify(state, actor="owner", at=NOW)
    with pytest.raises(LifecycleError, match="publish requires status VERIFIED"):
        publish(state, package, actor="publisher", at=NOW)
    with pytest.raises(LifecycleError, match="binding verification requires status REVIEWED"):
        verify_binding(state, "manufacturing.item@erp-adapter", actor="owner", at=NOW)


def test_review_requires_a_clean_validation_and_an_actor() -> None:
    package = generate().package
    state = new_version_state(package, origin="GENERATED")
    failing = validate_package(package, {})
    with pytest.raises(LifecycleError, match="validation errors") as raised:
        review(state, package, failing, actor="tech-reviewer", at=NOW)
    assert raised.value.error_code == "validation_failed"
    with pytest.raises(LifecycleError, match="attributed actor"):
        review(state, package, [], actor="  ", at=NOW)


def test_full_lifecycle_to_published_snapshot() -> None:
    package, state = _reviewed()
    assert state.status == "REVIEWED" and state.reviewed_by == "tech-reviewer"

    with pytest.raises(LifecycleError, match="awaiting data owner verification") as raised:
        verify(state, actor="data-owner", at=NOW)
    assert raised.value.error_code == "bindings_not_verified"
    with pytest.raises(LifecycleError, match="no active binding"):
        verify_binding(state, "missing", actor="data-owner", at=NOW)

    for binding_id in list(state.bindings):
        state = verify_binding(state, binding_id, actor="data-owner", at=NOW)
    state = verify(state, actor="data-owner", at=NOW)
    assert state.status == "VERIFIED" and not is_consumable(state)

    state, snapshot = publish(state, package, actor="publisher", at=NOW)
    assert state.status == "PUBLISHED" and is_consumable(state)
    assert snapshot.digest == package_digest(package) == state.digest
    assert set(snapshot.binding_digests) == set(state.bindings)
    restored = load_package_document(json.loads(snapshot.canonical_json)["package"])
    assert package_digest(restored) == snapshot.digest

    with pytest.raises(LifecycleError, match="review requires status DRAFT"):
        review(state, package, [], actor="tech-reviewer", at=NOW)
    state = deprecate(state)
    assert is_consumable(state)
    state = retire(state)
    assert state.status == "RETIRED" and not is_consumable(state)


def test_content_cannot_change_under_an_approved_version() -> None:
    package, state = _reviewed()
    for binding_id in list(state.bindings):
        state = verify_binding(state, binding_id, actor="data-owner", at=NOW)
    state = verify(state, actor="data-owner", at=NOW)

    document = package.model_dump(mode="json")
    document["objects"][0]["properties"][2]["sensitive"] = False
    document["objects"][0]["properties"][2]["classification"] = "public"
    tampered = load_package_document(document)
    with pytest.raises(LifecycleError, match="import it as a new version") as raised:
        publish(state, tampered, actor="publisher", at=NOW)
    assert raised.value.error_code == "content_changed"


def test_compatibility_report_flags_breaking_changes() -> None:
    old = generate().package
    document = old.model_dump(mode="json")
    item = document["objects"][0]
    item["properties"] = [prop for prop in item["properties"] if prop["name"] != "extra"]
    by_name = {prop["name"]: prop for prop in item["properties"]}
    by_name["item_name"]["type"] = "text"
    by_name["status"]["enum_values"] = ["A"]
    by_name["standard_cost"].update({"sensitive": False, "classification": "internal"})
    item["properties"].append({"name": "brand", "type": "string"})
    item_binding = document["bindings"][0]
    del item_binding["field_map"]["extra"]
    item_binding["field_map"]["brand"] = {"source_field": "BRAND"}
    item_binding["payload_contract_version"] = "item.v2"
    document["relations"] = []
    new = load_package_document(document)

    changes = compare_packages(old, new)
    breaking = {change.code for change in breaking_changes(changes)}
    assert breaking == {
        "property_removed",
        "property_type_changed",
        "enum_value_removed",
        "sensitivity_lowered",
        "relation_removed",
        "source_contract_changed",
    }
    assert {change.code for change in changes if not change.breaking} == {"property_added"}
    assert compare_packages(old, old) == []

    moved = old.model_dump(mode="json")
    moved["bindings"][0]["source_application_id"] = "mes-adapter"
    moved["bindings"][0]["binding_id"] = "manufacturing.item@mes-adapter"
    assert "authoritative_source_changed" in {
        change.code for change in compare_packages(old, load_package_document(moved))
    }
