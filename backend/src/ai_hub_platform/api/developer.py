from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from ai_hub_platform.api.dependencies import portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.developer.service import (
    DeveloperAssetNotFoundError,
    DeveloperAssetUnavailableError,
    DeveloperCatalogService,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1/developer", tags=["developer-center"])


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeveloperAssetResponse(ApiModel):
    asset_id: str
    kind: str
    title: str
    version: str
    media_type: str
    required_capability: str
    sha256: str
    size_bytes: int
    download_path: str


class DeveloperCatalogResponse(ApiModel):
    catalog_version: str
    catalog_sha256: str
    items: list[DeveloperAssetResponse]
    total: int


def _catalog(request: Request) -> DeveloperCatalogService:
    return DeveloperCatalogService(request.app.state.settings.public_asset_root)


@router.get("/catalog", response_model=DeveloperCatalogResponse)
async def developer_catalog(
    request: Request,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.developer.read",
                application_parameter=None,
            )
        ),
    ],
) -> DeveloperCatalogResponse:
    service = _catalog(request)
    try:
        assets = service.list_assets()
    except DeveloperAssetUnavailableError as error:
        raise ApiError(503, "developer_asset_unavailable", str(error)) from error
    items = [DeveloperAssetResponse.model_validate(item, from_attributes=True) for item in assets]
    return DeveloperCatalogResponse(
        catalog_version="0.1.0",
        catalog_sha256=service.catalog_digest(assets),
        items=items,
        total=len(items),
    )


@router.get("/assets/{asset_id}")
async def download_developer_asset(
    asset_id: str,
    request: Request,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.developer.read",
                application_parameter=None,
            )
        ),
    ],
) -> Response:
    try:
        definition, content = _catalog(request).asset_bytes(asset_id)
    except DeveloperAssetNotFoundError as error:
        raise ApiError(404, "developer_asset_not_found", str(error)) from error
    except DeveloperAssetUnavailableError as error:
        raise ApiError(503, "developer_asset_unavailable", str(error)) from error
    filename = definition.relative_path.rsplit("/", 1)[-1]
    return Response(
        content=content,
        media_type=definition.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )
