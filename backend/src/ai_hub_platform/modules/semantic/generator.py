"""Generate a DRAFT semantic package from registered ingest contracts.

Generation is deterministic and makes no business judgement. It turns what a
contract states into a starting point for a human reviewer:

- one object per selected contract;
- one property per payload field, with the type narrowed from JSON Schema;
- keys from the caller, or the Raw object id when none are given;
- relations only from an explicit ``x-ai-hub-ref`` annotation in the schema;
- sensitivity from the contract's field classifications, raised (never
  lowered) by field-name rules;
- one binding per object, pinned to the contract version and fingerprint.

Everything it could not decide is reported as a warning rather than guessed or
dropped. The result has no lifecycle state of its own; callers store it as a
DRAFT version (see ``lifecycle.new_version_state``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from ai_hub_platform.modules.semantic.metamodel import (
    OBJECT_ID_SOURCE,
    Classification,
    FieldMapping,
    ObjectDef,
    Property,
    PropertyType,
    Relation,
    SemanticPackage,
    SourceBinding,
    SourceKind,
)

REF_ANNOTATION = "x-ai-hub-ref"
OBJECT_ID_PROPERTY = "object_id"
_CLASSIFICATIONS: tuple[Classification, ...] = (
    "public",
    "internal",
    "confidential",
    "restricted",
)
_SENSITIVE_NAME = re.compile(
    r"(phone|mobile|email|contact|address|id_?card|passport|bank|account_no|salary|"
    r"wage|cost|birth|password|secret|token)",
    re.IGNORECASE,
)
_NON_IDENTIFIER = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class GenerationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ContractInput:
    source_application_id: str
    object_type: str
    contract_version: str
    json_schema: Mapping[str, Any]
    schema_fingerprint: str
    field_classifications: Mapping[str, Any] = field(default_factory=dict[str, Any])
    source_kind: SourceKind = "external_system_adapter"


@dataclass(frozen=True, slots=True)
class GenerationWarning:
    code: str
    subject: str
    message: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    package: SemanticPackage
    warnings: tuple[GenerationWarning, ...]


def snake_case(value: str) -> str:
    spaced = _CAMEL_BOUNDARY.sub("_", value.strip())
    return _NON_IDENTIFIER.sub("_", spaced.lower()).strip("_")


def _identifier(value: str, fallback: str) -> str:
    name = snake_case(value)
    if not name:
        return fallback
    return name if name[0].isalpha() else f"{fallback}_{name}"


def _json_types(node: Mapping[str, Any]) -> set[str]:
    declared: object = node.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {item for item in cast(list[object], declared) if isinstance(item, str)} - {"null"}
    return set()


def _narrow_type(node: Mapping[str, Any]) -> tuple[PropertyType, list[str], str | None]:
    """Map a JSON Schema node to (property type, enum values, warning)."""
    enum: object = node.get("enum")
    if isinstance(enum, list) and enum:
        values = [str(item) for item in cast(list[object], enum) if item is not None]
        if values:
            return "enum", values, None
    types = _json_types(node)
    if types == {"string"}:
        declared_format: object = node.get("format")
        if declared_format == "date":
            return "date", [], None
        if declared_format == "date-time":
            return "datetime", [], None
        return "string", [], None
    if types == {"integer"}:
        return "int", [], None
    if types in ({"number"}, {"number", "integer"}):
        return "decimal", [], None
    if types == {"boolean"}:
        return "bool", [], None
    described = "/".join(sorted(types)) or "unspecified"
    return "text", [], f"JSON Schema type {described} kept as text; confirm the type"


def _contract_classification(value: object) -> tuple[Classification | None, bool]:
    """Read one field_classifications entry: (classification, sensitive)."""
    label: object = value
    sensitive = False
    if isinstance(value, Mapping):
        entry = cast(Mapping[str, Any], value)
        label = entry.get("classification")
        sensitive = entry.get("sensitive") is True
    if isinstance(label, str):
        lowered = label.strip().lower()
        for known in _CLASSIFICATIONS:
            if lowered == known:
                return known, sensitive or known in {"confidential", "restricted"}
        if lowered in {"sensitive", "pii", "secret"}:
            return "confidential", True
    return None, sensitive


def _generate_object(
    contract: ContractInput,
    *,
    semantic_id: str,
    key_fields: Sequence[str] | None,
    object_ids_by_type: Mapping[str, str],
    owner: str,
) -> tuple[ObjectDef, SourceBinding, list[Relation], list[GenerationWarning]]:
    subject = f"object:{semantic_id}"
    warnings: list[GenerationWarning] = []
    raw_properties: object = contract.json_schema.get("properties")
    if not isinstance(raw_properties, Mapping) or not raw_properties:
        raise GenerationError(
            f"{contract.object_type}: contract schema declares no payload properties"
        )
    schema_properties = cast(Mapping[str, Any], raw_properties)
    listed: object = contract.json_schema.get("required", [])
    required_fields = (
        {item for item in cast(list[object], listed) if isinstance(item, str)}
        if isinstance(listed, list)
        else set[str]()
    )
    for key_field in key_fields or ():
        if key_field not in schema_properties:
            raise GenerationError(
                f"{contract.object_type}: key field '{key_field}' is not in the contract schema"
            )

    properties: list[Property] = []
    field_map: dict[str, FieldMapping] = {}
    relations: list[Relation] = []
    name_by_field: dict[str, str] = {}
    used: set[str] = set()

    for index, (field_name, raw_node) in enumerate(schema_properties.items()):
        node: Mapping[str, Any] = (
            cast(Mapping[str, Any], raw_node) if isinstance(raw_node, Mapping) else {}
        )
        name = _identifier(field_name, f"field_{index + 1}")
        if name in used or name == OBJECT_ID_PROPERTY:
            renamed = f"{name}_{index + 1}"
            warnings.append(
                GenerationWarning(
                    "property_renamed",
                    f"{subject}.{renamed}",
                    f"payload field '{field_name}' normalises to a name already in use; "
                    f"named '{renamed}' pending review",
                )
            )
            name = renamed
        used.add(name)
        name_by_field[field_name] = name

        property_type, enum_values, type_warning = _narrow_type(node)
        if type_warning:
            warnings.append(GenerationWarning("type_unresolved", f"{subject}.{name}", type_warning))
        ref_target: str | None = None
        annotated: object = node.get(REF_ANNOTATION)
        if isinstance(annotated, str):
            ref_target = object_ids_by_type.get(annotated)
            if ref_target is None:
                warnings.append(
                    GenerationWarning(
                        "reference_unresolved",
                        f"{subject}.{name}",
                        f"references object type '{annotated}', which is not part of this "
                        "generation; kept as a plain property",
                    )
                )
            else:
                property_type, enum_values = "ref", []

        classification, sensitive = _contract_classification(
            contract.field_classifications.get(field_name)
        )
        if not sensitive and _SENSITIVE_NAME.search(name):
            sensitive = True
            classification = "confidential"
            warnings.append(
                GenerationWarning(
                    "sensitivity_suggested",
                    f"{subject}.{name}",
                    "field name suggests sensitive data; marked sensitive pending "
                    "data owner confirmation",
                )
            )
        description: object = node.get("description")
        properties.append(
            Property(
                name=name,
                type=property_type,
                display_name=field_name,
                description=description if isinstance(description, str) else "",
                required=field_name in required_fields or field_name in (key_fields or ()),
                ref=ref_target,
                enum_values=enum_values,
                sensitive=sensitive,
                classification=classification or "internal",
            )
        )
        field_map[name] = FieldMapping(source_field=field_name)
        if ref_target is not None:
            relations.append(
                Relation(
                    semantic_id=f"{semantic_id}_{name}",
                    display_name=f"{semantic_id} → {ref_target}",
                    source_object=semantic_id,
                    target_object=ref_target,
                    source_property=name,
                )
            )

    if key_fields:
        keys = [name_by_field[key_field] for key_field in key_fields]
    else:
        keys = [OBJECT_ID_PROPERTY]
        properties.insert(
            0,
            Property(
                name=OBJECT_ID_PROPERTY,
                type="string",
                display_name="object_id",
                description="Source object identifier assigned by the adapter",
                required=True,
            ),
        )
        field_map[OBJECT_ID_PROPERTY] = FieldMapping(source_field=OBJECT_ID_SOURCE)
        warnings.append(
            GenerationWarning(
                "business_key_unconfirmed",
                subject,
                "no business key supplied; keyed by the source object id pending review",
            )
        )
    warnings.append(
        GenerationWarning(
            "naming_unreviewed",
            subject,
            "display names default to payload field names; meaning, authoritative source "
            "and data owner must be confirmed by a reviewer",
        )
    )

    binding_id = f"{semantic_id}@{contract.source_application_id}"
    obj = ObjectDef(
        semantic_id=semantic_id,
        display_name=contract.object_type,
        owner_application=contract.source_application_id,
        data_owner=owner,
        keys=keys,
        properties=properties,
    )
    binding = SourceBinding(
        binding_id=binding_id,
        semantic_object=semantic_id,
        source_kind=contract.source_kind,
        source_application_id=contract.source_application_id,
        object_type=contract.object_type,
        payload_contract_version=contract.contract_version,
        contract_fingerprint=contract.schema_fingerprint,
        field_map=field_map,
    )
    relations = [
        relation.model_copy(update={"source_binding_ref": binding_id}) for relation in relations
    ]
    return obj, binding, relations, warnings


def generate_draft_package(
    contracts: Sequence[ContractInput],
    *,
    package_id: str,
    display_name: str,
    version: str,
    owner: str = "",
    key_fields: Mapping[str, Sequence[str]] | None = None,
) -> GenerationResult:
    """Build a DRAFT package from ``contracts``.

    ``key_fields`` maps an ``object_type`` to the payload fields that form its
    business key.
    """
    if not contracts:
        raise GenerationError("select at least one contract")
    object_ids_by_type: dict[str, str] = {}
    for contract in contracts:
        local = _identifier(contract.object_type.rsplit(".", 1)[-1], "object")
        semantic_id = f"{package_id}.{local}"
        if contract.object_type in object_ids_by_type:
            raise GenerationError(f"object type {contract.object_type} selected more than once")
        if semantic_id in object_ids_by_type.values():
            raise GenerationError(
                f"object types normalise to the same semantic id {semantic_id}; "
                "generate them into separate packages"
            )
        object_ids_by_type[contract.object_type] = semantic_id

    objects: list[ObjectDef] = []
    bindings: list[SourceBinding] = []
    relations: list[Relation] = []
    warnings: list[GenerationWarning] = []
    for contract in contracts:
        obj, binding, object_relations, object_warnings = _generate_object(
            contract,
            semantic_id=object_ids_by_type[contract.object_type],
            key_fields=(key_fields or {}).get(contract.object_type),
            object_ids_by_type=object_ids_by_type,
            owner=owner,
        )
        objects.append(obj)
        bindings.append(binding)
        relations.extend(object_relations)
        warnings.extend(object_warnings)

    package = SemanticPackage(
        package_id=package_id,
        display_name=display_name,
        version=version,
        owner=owner,
        objects=objects,
        relations=relations,
        bindings=bindings,
    )
    errors = package.cross_validate()
    if errors:  # pragma: no cover - generation produces consistent references
        raise GenerationError("; ".join(errors))
    return GenerationResult(package=package, warnings=tuple(warnings))
