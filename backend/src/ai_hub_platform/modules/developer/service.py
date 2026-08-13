from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AssetKind = Literal["OPENAPI", "ASYNCAPI", "JSON_SCHEMA", "SDK_EXAMPLE", "GUIDE"]


@dataclass(frozen=True, slots=True)
class AssetDefinition:
    asset_id: str
    kind: AssetKind
    title: str
    version: str
    relative_path: str
    media_type: str
    required_capability: str


@dataclass(frozen=True, slots=True)
class DeveloperAsset:
    asset_id: str
    kind: AssetKind
    title: str
    version: str
    media_type: str
    required_capability: str
    sha256: str
    size_bytes: int
    download_path: str


ASSETS: tuple[AssetDefinition, ...] = (
    AssetDefinition(
        "platform-openapi",
        "OPENAPI",
        "Platform API OpenAPI",
        "0.2.0",
        "contracts/api/platform-api.openapi.yaml",
        "application/yaml",
        "API_CLIENT",
    ),
    AssetDefinition(
        "platform-asyncapi",
        "ASYNCAPI",
        "Reliable events AsyncAPI",
        "0.2.0",
        "contracts/events/ai-hub.asyncapi.yaml",
        "application/yaml",
        "EVENT_PUBLISHER or EVENT_CONSUMER",
    ),
    AssetDefinition(
        "cloud-event-schema",
        "JSON_SCHEMA",
        "CloudEvents envelope schema",
        "1.0.0",
        "contracts/events/cloud-event.schema.json",
        "application/schema+json",
        "EVENT_PUBLISHER or EVENT_CONSUMER",
    ),
    AssetDefinition(
        "api-only-python",
        "SDK_EXAMPLE",
        "Python API-only quickstart",
        "0.1.0",
        "examples/sdk/api_only.py",
        "text/x-python",
        "API_CLIENT",
    ),
    AssetDefinition(
        "integration-guide",
        "GUIDE",
        "Independent application integration guide",
        "0.1.0",
        "docs/developer-integration-guide.md",
        "text/markdown",
        "API_CLIENT",
    ),
)


class DeveloperAssetNotFoundError(LookupError):
    pass


class DeveloperAssetUnavailableError(RuntimeError):
    pass


class DeveloperCatalogService:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def _definition(asset_id: str) -> AssetDefinition:
        definition = next((item for item in ASSETS if item.asset_id == asset_id), None)
        if definition is None:
            raise DeveloperAssetNotFoundError("Developer asset was not found")
        return definition

    def _path(self, definition: AssetDefinition) -> Path:
        candidate = (self.root / definition.relative_path).resolve()
        if self.root not in candidate.parents:
            raise DeveloperAssetUnavailableError("Developer asset path is outside the catalog")
        if not candidate.is_file():
            raise DeveloperAssetUnavailableError("Developer asset is unavailable")
        return candidate

    def asset_bytes(self, asset_id: str) -> tuple[AssetDefinition, bytes]:
        definition = self._definition(asset_id)
        return definition, self._path(definition).read_bytes()

    def list_assets(self) -> list[DeveloperAsset]:
        assets: list[DeveloperAsset] = []
        for definition in ASSETS:
            content = self._path(definition).read_bytes()
            assets.append(
                DeveloperAsset(
                    asset_id=definition.asset_id,
                    kind=definition.kind,
                    title=definition.title,
                    version=definition.version,
                    media_type=definition.media_type,
                    required_capability=definition.required_capability,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    download_path=f"/portal-api/v1/developer/assets/{definition.asset_id}",
                )
            )
        return assets

    def catalog_digest(self, assets: list[DeveloperAsset]) -> str:
        canonical = json.dumps(
            [
                {
                    "asset_id": item.asset_id,
                    "version": item.version,
                    "sha256": item.sha256,
                }
                for item in assets
            ],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()
