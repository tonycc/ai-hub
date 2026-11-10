# SPDX-License-Identifier: Apache-2.0
# Derived from data2agent (xingyekuo-spec/data2agent @ 60bcbe2),
# data2agent/shared/metamodel/validate.py. Modified by AI Hub; see
# third_party/data2agent/copy-manifest.json for the change summary.
"""Validate a semantic package against registered contracts and prior versions.

``validate_package`` answers "can this version be reviewed": references
resolve, and every active binding points at an ACTIVE contract with the same
fingerprint, existing payload fields and compatible types.

``compare_packages`` answers "what does this version change for consumers"
and marks the changes that need an impact report and a migration window.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from ai_hub_platform.modules.semantic.metamodel import (
    OBJECT_ID_SOURCE,
    ObjectDef,
    PropertyType,
    SemanticPackage,
    SourceBinding,
)

ContractKey = tuple[str, str, str]
Severity = Literal["error", "warning"]

# JSON Schema types each property type can be built from. Numeric and boolean
# values often arrive as strings from ERP adapters; those are cast at build time
# and rows that fail the cast are quarantined, so "string" is accepted for them.
# Structured values have no property type of their own: they are kept as text
# (serialised JSON) rather than dropped.
_COMPATIBLE_JSON_TYPES: dict[PropertyType, frozenset[str]] = {
    "string": frozenset({"string"}),
    "text": frozenset({"string", "object", "array"}),
    "enum": frozenset({"string", "integer"}),
    "ref": frozenset({"string", "integer"}),
    "int": frozenset({"integer", "string"}),
    "decimal": frozenset({"number", "integer", "string"}),
    "money": frozenset({"number", "integer", "string"}),
    "date": frozenset({"string"}),
    "datetime": frozenset({"string"}),
    "bool": frozenset({"boolean", "string", "integer"}),
}


@dataclass(frozen=True, slots=True)
class ContractView:
    """The parts of a registered ingest contract that bindings are checked against."""

    source_application_id: str
    object_type: str
    contract_version: str
    json_schema: Mapping[str, Any]
    schema_fingerprint: str
    status: str = "ACTIVE"

    @property
    def key(self) -> ContractKey:
        return (self.source_application_id, self.object_type, self.contract_version)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    severity: Severity
    subject: str
    message: str


@dataclass(frozen=True, slots=True)
class CompatibilityChange:
    code: str
    subject: str
    message: str
    breaking: bool


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else None


def _json_types(node: Mapping[str, Any]) -> frozenset[str]:
    declared: object = node.get("type")
    if isinstance(declared, str):
        return frozenset({declared})
    if isinstance(declared, list):
        return frozenset(item for item in cast(list[object], declared) if isinstance(item, str)) - {
            "null"
        }
    return frozenset()


def _resolve_field(
    json_schema: Mapping[str, Any], path: str
) -> tuple[Mapping[str, Any] | None, bool]:
    """Return the schema node for a dotted payload path and whether it is required."""
    node: Mapping[str, Any] = json_schema
    required = True
    for part in path.split("."):
        properties = _as_mapping(node.get("properties"))
        child = None if properties is None else _as_mapping(properties.get(part))
        if child is None:
            return None, False
        listed: object = node.get("required", [])
        required = required and isinstance(listed, list) and part in cast(list[object], listed)
        node = child
    return node, required


def _binding_issues(
    binding: SourceBinding,
    obj: ObjectDef | None,
    contracts: Mapping[ContractKey, ContractView],
) -> list[ValidationIssue]:
    subject = f"binding:{binding.binding_id}"
    key = (binding.source_application_id, binding.object_type, binding.payload_contract_version)
    contract = contracts.get(key)
    if contract is None:
        return [
            ValidationIssue(
                "contract_missing",
                "error",
                subject,
                f"no ingest contract registered for {'/'.join(key)}",
            )
        ]
    issues: list[ValidationIssue] = []
    if contract.status != "ACTIVE":
        issues.append(
            ValidationIssue(
                "contract_not_active",
                "error",
                subject,
                f"ingest contract {'/'.join(key)} status is {contract.status}",
            )
        )
    if contract.schema_fingerprint != binding.contract_fingerprint:
        issues.append(
            ValidationIssue(
                "contract_fingerprint_mismatch",
                "error",
                subject,
                "contract_fingerprint does not match the registered contract schema",
            )
        )
    if obj is None:
        return issues
    properties = obj.property_map()
    for name, mapping in binding.field_map.items():
        prop = properties.get(name)
        if prop is None or mapping.source_field == OBJECT_ID_SOURCE:
            continue
        node, source_required = _resolve_field(contract.json_schema, mapping.source_field)
        if node is None:
            issues.append(
                ValidationIssue(
                    "source_field_missing",
                    "error",
                    subject,
                    f"'{name}' maps to '{mapping.source_field}', which the contract "
                    "schema does not define",
                )
            )
            continue
        json_types = _json_types(node)
        if not json_types:
            issues.append(
                ValidationIssue(
                    "source_type_unknown",
                    "warning",
                    subject,
                    f"contract schema declares no type for '{mapping.source_field}'",
                )
            )
        elif not json_types <= _COMPATIBLE_JSON_TYPES[prop.type]:
            issues.append(
                ValidationIssue(
                    "type_incompatible",
                    "error",
                    subject,
                    f"'{name}' is {prop.type} but '{mapping.source_field}' is "
                    f"{'/'.join(sorted(json_types))}",
                )
            )
        if prop.required and not source_required:
            issues.append(
                ValidationIssue(
                    "required_source_optional",
                    "warning",
                    subject,
                    f"required property '{name}' maps to optional payload field "
                    f"'{mapping.source_field}'; rows without it will be quarantined",
                )
            )
    return issues


def validate_package(
    package: SemanticPackage,
    contracts: Mapping[ContractKey, ContractView],
) -> list[ValidationIssue]:
    issues = [
        ValidationIssue("reference_invalid", "error", f"package:{package.package_id}", message)
        for message in package.cross_validate()
    ]
    objects = package.object_map()
    bound: set[str] = set()
    for binding in package.active_bindings():
        bound.add(binding.semantic_object)
        issues.extend(_binding_issues(binding, objects.get(binding.semantic_object), contracts))
    for semantic_id in sorted(set(objects) - bound):
        issues.append(
            ValidationIssue(
                "object_unbound",
                "warning",
                f"object:{semantic_id}",
                "object has no active source binding and cannot be built into a dataset",
            )
        )
    return issues


def _object_changes(old: ObjectDef, new: ObjectDef) -> list[CompatibilityChange]:
    subject = f"object:{old.semantic_id}"
    changes: list[CompatibilityChange] = []
    if old.keys != new.keys:
        changes.append(
            CompatibilityChange(
                "key_changed", subject, f"keys changed from {old.keys} to {new.keys}", True
            )
        )
    new_properties = new.property_map()
    for prop in old.properties:
        current = new_properties.get(prop.name)
        where = f"{subject}.{prop.name}"
        if current is None:
            changes.append(CompatibilityChange("property_removed", where, "property removed", True))
            continue
        if current.type != prop.type or current.ref != prop.ref:
            changes.append(
                CompatibilityChange(
                    "property_type_changed",
                    where,
                    f"type changed from {prop.type} to {current.type}",
                    True,
                )
            )
        removed_values = sorted(set(prop.enum_values) - set(current.enum_values))
        if removed_values:
            changes.append(
                CompatibilityChange(
                    "enum_value_removed", where, f"enum values removed: {removed_values}", True
                )
            )
        if prop.sensitive and not current.sensitive:
            changes.append(
                CompatibilityChange(
                    "sensitivity_lowered", where, "property is no longer masked", True
                )
            )
    for name in sorted(set(new_properties) - set(old.property_map())):
        changes.append(
            CompatibilityChange("property_added", f"{subject}.{name}", "property added", False)
        )
    return changes


def compare_packages(old: SemanticPackage, new: SemanticPackage) -> list[CompatibilityChange]:
    """List consumer-visible changes from ``old`` to ``new``."""
    changes: list[CompatibilityChange] = []
    old_objects, new_objects = old.object_map(), new.object_map()
    for semantic_id, obj in old_objects.items():
        current = new_objects.get(semantic_id)
        if current is None:
            changes.append(
                CompatibilityChange("object_removed", f"object:{semantic_id}", "removed", True)
            )
        else:
            changes.extend(_object_changes(obj, current))
    for semantic_id in sorted(set(new_objects) - set(old_objects)):
        changes.append(
            CompatibilityChange("object_added", f"object:{semantic_id}", "object added", False)
        )

    new_relations = {relation.semantic_id: relation for relation in new.relations}
    for relation in old.relations:
        subject = f"relation:{relation.semantic_id}"
        current = new_relations.get(relation.semantic_id)
        if current is None:
            changes.append(CompatibilityChange("relation_removed", subject, "removed", True))
            continue
        if (current.source_object, current.target_object) != (
            relation.source_object,
            relation.target_object,
        ):
            changes.append(
                CompatibilityChange("relation_endpoint_changed", subject, "endpoints changed", True)
            )
        if (current.source_cardinality, current.target_cardinality) != (
            relation.source_cardinality,
            relation.target_cardinality,
        ):
            changes.append(
                CompatibilityChange(
                    "relation_cardinality_changed", subject, "cardinality changed", True
                )
            )

    new_metric_ids = {metric.semantic_id for metric in new.metrics}
    for metric in old.metrics:
        if metric.semantic_id not in new_metric_ids:
            changes.append(
                CompatibilityChange(
                    "metric_removed", f"metric:{metric.semantic_id}", "removed", True
                )
            )

    new_bindings = {binding.semantic_object: binding for binding in new.active_bindings()}
    for binding in old.active_bindings():
        current = new_bindings.get(binding.semantic_object)
        if current is None:
            continue
        subject = f"binding:{binding.semantic_object}"
        if (current.source_application_id, current.object_type) != (
            binding.source_application_id,
            binding.object_type,
        ):
            changes.append(
                CompatibilityChange(
                    "authoritative_source_changed",
                    subject,
                    f"source changed from {binding.source_application_id}/{binding.object_type} "
                    f"to {current.source_application_id}/{current.object_type}",
                    True,
                )
            )
        elif (current.payload_contract_version, current.contract_fingerprint) != (
            binding.payload_contract_version,
            binding.contract_fingerprint,
        ):
            changes.append(
                CompatibilityChange(
                    "source_contract_changed",
                    subject,
                    f"contract changed from {binding.payload_contract_version} "
                    f"to {current.payload_contract_version}",
                    True,
                )
            )
    return changes


def breaking_changes(changes: list[CompatibilityChange]) -> list[CompatibilityChange]:
    return [change for change in changes if change.breaking]
