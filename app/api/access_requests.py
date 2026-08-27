from fastapi import APIRouter, Depends, Path, Query, status

from app.api.dependencies import current_user, get_access_request_service, get_read_model
from app.api.schemas import MAX_BIGINT, AccessRequestCreate, AccessRequestOut, DecisionIn
from app.application.access_requests import AccessRequestService
from app.domain.entities import AccessRequest, User
from app.domain.enums import RequestStatus
from app.infrastructure.db.repositories import RequestReadModel

router = APIRouter(prefix="/api/v1/access-requests", tags=["access-requests"])


async def _render(
    read_model: RequestReadModel, requests: list[AccessRequest]
) -> list[AccessRequestOut]:
    rows = await read_model.load([r.id for r in requests])
    return [AccessRequestOut.from_model(rows[r.id]) for r in requests]


@router.post("", response_model=AccessRequestOut, status_code=status.HTTP_201_CREATED)
async def create(
    payload: AccessRequestCreate,
    actor: User = Depends(current_user),
    service: AccessRequestService = Depends(get_access_request_service),
    read_model: RequestReadModel = Depends(get_read_model),
) -> AccessRequestOut:
    request = await service.create(actor, payload.resource_id, payload.reason)
    return (await _render(read_model, [request]))[0]


@router.get("", response_model=list[AccessRequestOut])
async def index(
    status_filter: RequestStatus | None = Query(default=None, alias="status"),
    resource_id: int | None = Query(default=None, gt=0, le=MAX_BIGINT),
    actor: User = Depends(current_user),
    service: AccessRequestService = Depends(get_access_request_service),
    read_model: RequestReadModel = Depends(get_read_model),
) -> list[AccessRequestOut]:
    requests = await service.list(actor, status=status_filter, resource_id=resource_id)
    return await _render(read_model, requests)


@router.get("/{request_id}", response_model=AccessRequestOut)
async def show(
    request_id: int = Path(le=MAX_BIGINT),
    actor: User = Depends(current_user),
    service: AccessRequestService = Depends(get_access_request_service),
    read_model: RequestReadModel = Depends(get_read_model),
) -> AccessRequestOut:
    request = await service.get(actor, request_id)
    return (await _render(read_model, [request]))[0]


@router.post("/{request_id}/approve", response_model=AccessRequestOut)
async def approve(
    request_id: int = Path(le=MAX_BIGINT),
    payload: DecisionIn | None = None,
    actor: User = Depends(current_user),
    service: AccessRequestService = Depends(get_access_request_service),
    read_model: RequestReadModel = Depends(get_read_model),
) -> AccessRequestOut:
    request = await service.approve(actor, request_id, payload.comment if payload else None)
    return (await _render(read_model, [request]))[0]


@router.post("/{request_id}/reject", response_model=AccessRequestOut)
async def reject(
    request_id: int = Path(le=MAX_BIGINT),
    payload: DecisionIn | None = None,
    actor: User = Depends(current_user),
    service: AccessRequestService = Depends(get_access_request_service),
    read_model: RequestReadModel = Depends(get_read_model),
) -> AccessRequestOut:
    request = await service.reject(actor, request_id, payload.comment if payload else None)
    return (await _render(read_model, [request]))[0]
