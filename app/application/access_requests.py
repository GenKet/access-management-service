from dataclasses import dataclass

from app.application.ports import (
    AccessRequestRepository,
    ProvisioningJobRepository,
    ResourceRepository,
    UnitOfWork,
)
from app.domain.entities import AccessRequest, ProvisioningJob, Resource, User
from app.domain.errors import RequestNotFound, ResourceNotFound


@dataclass
class AccessRequestService:
    uow: UnitOfWork
    requests: AccessRequestRepository
    resources: ResourceRepository
    jobs: ProvisioningJobRepository

    async def create(self, actor: User, resource_id: int, reason: str) -> AccessRequest:
        await self._load_resource(resource_id)
        request = AccessRequest(user_id=actor.id, resource_id=resource_id, reason=reason)
        created = await self.requests.add(request)
        await self.uow.commit()
        return created

    async def get(self, actor: User, request_id: int) -> AccessRequest:
        request = await self.requests.get_visible(request_id, actor)
        if request is None:
            raise RequestNotFound
        return request

    async def list(
        self, actor: User, status: str | None = None, resource_id: int | None = None
    ) -> list[AccessRequest]:
        return await self.requests.list_visible(actor, status=status, resource_id=resource_id)

    async def approve(
        self, actor: User, request_id: int, comment: str | None = None
    ) -> AccessRequest:
        request = await self._lock(request_id)
        resource = await self._load_resource(request.resource_id)

        if request.approve(actor, resource, comment):
            # Постановка задачи и переход статуса — одна транзакция, иначе
            # выдача может потеряться между коммитом и очередью.
            await self.jobs.add(ProvisioningJob(request_id=request.id))

        await self.requests.save(request)
        await self.uow.commit()
        return await self.get(actor, request_id)

    async def reject(
        self, actor: User, request_id: int, comment: str | None = None
    ) -> AccessRequest:
        request = await self._lock(request_id)
        resource = await self._load_resource(request.resource_id)

        request.reject(actor, resource, comment)

        await self.requests.save(request)
        await self.uow.commit()
        return await self.get(actor, request_id)

    async def _lock(self, request_id: int) -> AccessRequest:
        request = await self.requests.get_for_update(request_id)
        if request is None:
            raise RequestNotFound
        return request

    async def _load_resource(self, resource_id: int) -> Resource:
        resource = await self.resources.get(resource_id)
        if resource is None:
            raise ResourceNotFound
        return resource
