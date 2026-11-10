# SPDX-License-Identifier: Apache-2.0
# Derived from data2agent (xingyekuo-spec/data2agent @ 60bcbe2),
# data2agent/shared/metamodel/schema.py. Modified by AI Hub; see
# third_party/data2agent/copy-manifest.json for the change summary.
"""Semantic metamodel: what a valid semantic package is.

A package is the immutable *content* of one version: objects, relations,
metrics and source bindings. Review, verification and publication state lives
outside the content (see ``lifecycle``), so approving a package never changes
its digest.

Source bindings reference a registered ingest contract and map properties to
payload fields. They never carry physical table names, column names, join
expressions or SQL: those stay with the adapter that produced the payload.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PropertyType = Literal[
    "string",
    "text",
    "int",
    "decimal",
    "money",
    "date",
    "datetime",
    "bool",
    "ref",
    "enum",
]
Cardinality = Literal["1", "0..1", "1..n", "0..n"]
Classification = Literal["public", "internal", "confidential", "restricted"]
MetricStatus = Literal["DRAFT", "CERTIFIED", "DEPRECATED"]
SourceKind = Literal["external_system_adapter", "internal_application"]
RelationDirection = Literal["forward", "bidirectional"]
FallbackPolicy = Literal["fail_closed", "serve_stale_with_warning"]
ReconciliationPolicy = Literal["none", "scheduled"]

NAME_PATTERN = r"^[a-z][a-z0-9_]*$"
SEMANTIC_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
# Raw object id of the change record, for objects whose payload has no key field.
OBJECT_ID_SOURCE = "$object_id"
_PAYLOAD_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MASKED_CLASSIFICATIONS: frozenset[str] = frozenset({"confidential", "restricted"})

Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN, max_length=100)]
SemanticId = Annotated[str, StringConstraints(pattern=SEMANTIC_ID_PATTERN, max_length=200)]
Sha256Hex = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
NonEmpty = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Property(SemanticModel):
    name: Name
    type: PropertyType
    display_name: str = ""
    description: str = ""
    required: bool = False
    ref: SemanticId | None = None
    enum_values: list[str] = Field(default_factory=list[str])
    sensitive: bool = False
    classification: Classification = "internal"
    quality_rules: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def _check_type_extras(self) -> Property:
        if self.type == "ref" and not self.ref:
            raise ValueError(f"property {self.name}: type=ref requires a ref target")
        if self.type != "ref" and self.ref is not None:
            raise ValueError(f"property {self.name}: ref is only allowed for type=ref")
        if self.type == "enum" and not self.enum_values:
            raise ValueError(f"property {self.name}: type=enum requires enum_values")
        if len(self.enum_values) != len(set(self.enum_values)):
            raise ValueError(f"property {self.name}: enum_values must be unique")
        if self.classification in _MASKED_CLASSIFICATIONS and not self.sensitive:
            raise ValueError(
                f"property {self.name}: classification {self.classification} "
                "requires sensitive=true"
            )
        return self


class ObjectDef(SemanticModel):
    semantic_id: SemanticId
    display_name: NonEmpty
    description: str = ""
    domain: str = ""
    owner_application: str = ""
    authoritative_source_system: str = ""
    data_owner: str = ""
    keys: list[Name] = Field(min_length=1)
    properties: list[Property] = Field(min_length=1)
    states: list[str] = Field(default_factory=list[str])
    data_classification: Classification = "internal"

    @model_validator(mode="after")
    def _check_integrity(self) -> ObjectDef:
        names = [prop.name for prop in self.properties]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"{self.semantic_id}: duplicate property names {duplicates}")
        if len(self.keys) != len(set(self.keys)):
            raise ValueError(f"{self.semantic_id}: keys must be unique")
        by_name = {prop.name: prop for prop in self.properties}
        for key in self.keys:
            prop = by_name.get(key)
            if prop is None:
                raise ValueError(f"{self.semantic_id}: key '{key}' is not a property")
            if not prop.required:
                raise ValueError(f"{self.semantic_id}: key '{key}' must be required")
        return self

    def property_map(self) -> dict[str, Property]:
        return {prop.name: prop for prop in self.properties}


class Relation(SemanticModel):
    semantic_id: SemanticId
    display_name: str = ""
    description: str = ""
    source_object: SemanticId
    target_object: SemanticId
    source_cardinality: Cardinality = "0..n"
    target_cardinality: Cardinality = "1"
    direction: RelationDirection = "forward"
    # Property on source_object (type=ref to target_object) that realises the link.
    source_property: Name | None = None
    source_binding_ref: str | None = None


class Metric(SemanticModel):
    semantic_id: SemanticId
    display_name: NonEmpty
    description: str = ""
    formula: NonEmpty
    grain: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list[str])
    unit: str = ""
    freshness_slo: str = "T+1"
    owner: str = ""
    status: MetricStatus = "DRAFT"
    caveats: str = ""


class FieldMapping(SemanticModel):
    """Where one property comes from in the contract payload.

    ``source_field`` is a dotted payload path or ``$object_id``. ``value_map``
    translates source values for enum properties. Nothing else is expressible:
    no expressions, joins, SQL or URLs.
    """

    source_field: str
    value_map: dict[str, str] = Field(default_factory=dict[str, str])

    @model_validator(mode="after")
    def _check_source_field(self) -> FieldMapping:
        if self.source_field != OBJECT_ID_SOURCE and not _PAYLOAD_FIELD.fullmatch(
            self.source_field
        ):
            raise ValueError(
                f"source_field '{self.source_field}' must be a payload field path "
                f"or {OBJECT_ID_SOURCE}"
            )
        return self


class SourceBinding(SemanticModel):
    binding_id: NonEmpty
    semantic_object: SemanticId
    source_kind: SourceKind = "external_system_adapter"
    source_application_id: NonEmpty
    object_type: NonEmpty
    payload_contract_version: NonEmpty
    contract_fingerprint: Sha256Hex
    adapter_mapping_ref: str | None = None
    field_map: dict[Name, FieldMapping] = Field(min_length=1)
    freshness_slo: str = "T+1"
    fallback_policy: FallbackPolicy = "fail_closed"
    reconciliation_policy: ReconciliationPolicy = "scheduled"
    mapping_version: int = Field(default=1, ge=1)
    deprecated: bool = False


class SemanticPackage(SemanticModel):
    package_id: Name
    display_name: NonEmpty
    description: str = ""
    version: NonEmpty
    owner: str = ""
    objects: list[ObjectDef] = Field(default_factory=list[ObjectDef])
    relations: list[Relation] = Field(default_factory=list[Relation])
    metrics: list[Metric] = Field(default_factory=list[Metric])
    bindings: list[SourceBinding] = Field(default_factory=list[SourceBinding])

    def object_map(self) -> dict[str, ObjectDef]:
        return {obj.semantic_id: obj for obj in self.objects}

    def active_bindings(self) -> list[SourceBinding]:
        return [binding for binding in self.bindings if not binding.deprecated]

    def cross_validate(self) -> list[str]:
        """Reference integrity inside the package; returns human-readable errors."""
        errors: list[str] = []
        namespace = f"{self.package_id}."
        objects = self.object_map()

        def check_ids(kind: str, ids: list[str]) -> None:
            for duplicate in sorted({item for item in ids if ids.count(item) > 1}):
                errors.append(f"duplicate {kind} id: {duplicate}")
            for item in ids:
                if not item.startswith(namespace):
                    errors.append(f"{kind} {item} is outside package namespace {self.package_id}")

        check_ids("object", [obj.semantic_id for obj in self.objects])
        check_ids("relation", [relation.semantic_id for relation in self.relations])
        check_ids("metric", [metric.semantic_id for metric in self.metrics])

        for obj in self.objects:
            for prop in obj.properties:
                if prop.type == "ref" and prop.ref not in objects:
                    errors.append(f"{obj.semantic_id}.{prop.name}: ref target {prop.ref} undefined")

        binding_ids = [binding.binding_id for binding in self.bindings]
        for duplicate in sorted({item for item in binding_ids if binding_ids.count(item) > 1}):
            errors.append(f"duplicate binding id: {duplicate}")

        for relation in self.relations:
            errors.extend(self._relation_errors(relation, objects, set(binding_ids)))

        active_by_object: dict[str, list[str]] = {}
        for binding in self.bindings:
            errors.extend(self._binding_errors(binding, objects))
            if not binding.deprecated:
                active_by_object.setdefault(binding.semantic_object, []).append(binding.binding_id)
        for semantic_object, bound in sorted(active_by_object.items()):
            if len(bound) > 1:
                errors.append(
                    f"{semantic_object}: more than one active binding {sorted(bound)}; "
                    "an object has a single authoritative source"
                )
        return errors

    @staticmethod
    def _relation_errors(
        relation: Relation, objects: dict[str, ObjectDef], binding_ids: set[str]
    ) -> list[str]:
        errors: list[str] = []
        source = objects.get(relation.source_object)
        if source is None:
            errors.append(
                f"{relation.semantic_id}: source object {relation.source_object} undefined"
            )
        if relation.target_object not in objects:
            errors.append(
                f"{relation.semantic_id}: target object {relation.target_object} undefined"
            )
        if relation.source_property is not None and source is not None:
            prop = source.property_map().get(relation.source_property)
            if prop is None:
                errors.append(
                    f"{relation.semantic_id}: source_property {relation.source_property} "
                    f"is not a property of {relation.source_object}"
                )
            elif prop.type != "ref" or prop.ref != relation.target_object:
                errors.append(
                    f"{relation.semantic_id}: source_property {relation.source_property} "
                    f"must be a ref to {relation.target_object}"
                )
        if (
            relation.source_binding_ref is not None
            and relation.source_binding_ref not in binding_ids
        ):
            errors.append(
                f"{relation.semantic_id}: source_binding_ref "
                f"{relation.source_binding_ref} undefined"
            )
        return errors

    @staticmethod
    def _binding_errors(binding: SourceBinding, objects: dict[str, ObjectDef]) -> list[str]:
        errors: list[str] = []
        obj = objects.get(binding.semantic_object)
        if obj is None:
            return [f"binding {binding.binding_id}: object {binding.semantic_object} undefined"]
        properties = obj.property_map()
        for name, mapping in binding.field_map.items():
            prop = properties.get(name)
            if prop is None:
                errors.append(
                    f"binding {binding.binding_id}: '{name}' is not a property of {obj.semantic_id}"
                )
                continue
            if mapping.value_map:
                if prop.type != "enum":
                    errors.append(
                        f"binding {binding.binding_id}: value_map on non-enum property '{name}'"
                    )
                else:
                    unknown = sorted(set(mapping.value_map.values()) - set(prop.enum_values))
                    if unknown:
                        errors.append(
                            f"binding {binding.binding_id}: value_map for '{name}' targets "
                            f"undeclared enum values {unknown}"
                        )
        for prop in obj.properties:
            if prop.required and prop.name not in binding.field_map:
                errors.append(
                    f"binding {binding.binding_id}: required property '{prop.name}' is not mapped"
                )
        return errors
