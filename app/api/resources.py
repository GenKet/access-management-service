from fastapi import APIRouter, Depends

from app.api.dependencies import current_user, get_resource_repository
from app.api.schemas import ResourceOut
from app.domain.entities import User
from app.infrastructure.db.repositories import ResourceRepository

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


@router.get("", response_model=list[ResourceOut])
async def list_resources(
    _: User = Depends(current_user),
    resources: ResourceRepository = Depends(get_resource_repository),
) -> list[ResourceOut]:
    return [ResourceOut.model_validate(r, from_attributes=True) for r in await resources.list_all()]
