# SPDX-License-Identifier: Apache-2.0
# Derived from data2agent (xingyekuo-spec/data2agent @ 60bcbe2),
# data2agent/shared/metamodel/versioning.py. Modified by AI Hub; see
# third_party/data2agent/copy-manifest.json for the change summary.
"""Deterministic content digests for semantic packages and bindings.

A digest depends only on content: not on YAML layout, key order or which
optional fields were spelled out. List order is kept, because key order,
enum order and property order are meaningful to readers.
"""

from __future__ import annotations

import hashlib
import json

from ai_hub_platform.modules.semantic.metamodel import SemanticPackage, SourceBinding

BINDING_DIGEST_SCHEMA = "ai_hub.semantic.binding.v1"
PACKAGE_DIGEST_SCHEMA = "ai_hub.semantic.package.v1"


def _canonical(schema: str, key: str, content: object) -> str:
    return json.dumps(
        {"schema": schema, key: content},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def canonical_binding_json(binding: SourceBinding) -> str:
    return _canonical(BINDING_DIGEST_SCHEMA, "binding", binding.model_dump(mode="json"))


def binding_digest(binding: SourceBinding) -> str:
    """Digest of everything that affects how a binding maps payloads."""
    return _sha256(canonical_binding_json(binding))


def canonical_package_json(package: SemanticPackage) -> str:
    return _canonical(PACKAGE_DIGEST_SCHEMA, "package", package.model_dump(mode="json"))


def package_digest(package: SemanticPackage) -> str:
    return _sha256(canonical_package_json(package))
