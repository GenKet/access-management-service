from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain import entities
from app.domain.enums import JobStatus, RequestStatus
from app.domain.errors import DuplicateProvisioningJob, DuplicateRequest
from app.infrastructure.db import models

_UNIQUE_VIOLATION = "23505"
_OCCUPYING_INDEX = "uq_access_request_occupying"
_JOB_REQUEST_INDEX = "uq_provisioning_jobs_request_id"


def _constraint_of(exc: IntegrityError) -> str | None:
    orig = exc.orig
    name = getattr(orig, "constraint_name", None)
    if name is not None:
        return name
    cause = orig.__cause__ or orig.__context__
    return getattr(cause, "constraint_name", None)


def _translate(exc: IntegrityError) -> Exception:
    if getattr(exc.orig, "sqlstate", None) != _UNIQUE_VIOLATION:
        return exc
    constraint = _constraint_of(exc)
    if constraint == _OCCUPYING_INDEX:
        return DuplicateRequest()
    if constraint == _JOB_REQUEST_INDEX:
        return DuplicateProvisioningJob()
    return exc


def _to_user(row: models.User) -> entities.User:
    return entities.User(id=row.id, username=row.username, role=row.role)


def _to_resource(row: models.Resource) -> entities.Resource:
    return entities.Resource(
        id=row.id, name=row.name, owner_id=row.owner_id, criticality=row.criticality
    )


def _to_request(row: models.AccessRequest) -> entities.AccessRequest:
    return entities.AccessRequest(
        id=row.id,
        user_id=row.user_id,
        resource_id=row.resource_id,
        reason=row.reason,
        status=RequestStatus(row.status),
        owner_decided_by=row.owner_decided_by,
        owner_decided_at=row.owner_decided_at,
        decision_comment=row.decision_comment,
        provisioning_error=row.provisioning_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_job(row: models.ProvisioningJob) -> entities.ProvisioningJob:
    return entities.ProvisioningJob(
        id=row.id,
        request_id=row.request_id,
        status=JobStatus(row.status),
        attempts=row.attempts,
        locked_until=row.locked_until,
        last_error=row.last_error,
    )


class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise _translate(exc) from exc

    async def rollback(self) -> None:
        await self._session.rollback()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash(self, token_hash: str) -> entities.User | None:
        row = await self._session.scalar(
            select(models.User).where(models.User.token_hash == token_hash)
        )
        return _to_user(row) if row else None


class ResourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, resource_id: int) -> entities.Resource | None:
        row = await self._session.get(models.Resource, resource_id)
        return _to_resource(row) if row else None

    async def list_all(self) -> list[entities.Resource]:
        rows = await self._session.scalars(select(models.Resource).order_by(models.Resource.id))
        return [_to_resource(row) for row in rows]


class AccessRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request: entities.AccessRequest) -> entities.AccessRequest:
        row = models.AccessRequest(
            user_id=request.user_id,
            resource_id=request.resource_id,
            reason=request.reason,
            status=request.status,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise _translate(exc) from exc
        return _to_request(row)

    async def get_visible(
        self, request_id: int, actor: entities.User
    ) -> entities.AccessRequest | None:
        query = select(models.AccessRequest).where(models.AccessRequest.id == request_id)
        if not actor.is_security:
            query = query.where(self._visible_to(actor))
        row = await self._session.scalar(query)
        return _to_request(row) if row else None

    async def list_visible(
        self,
        actor: entities.User,
        status: str | None = None,
        resource_id: int | None = None,
    ) -> list[entities.AccessRequest]:
        query = select(models.AccessRequest)
        if not actor.is_security:
            query = query.where(self._visible_to(actor))
        if status is not None:
            query = query.where(models.AccessRequest.status == status)
        if resource_id is not None:
            query = query.where(models.AccessRequest.resource_id == resource_id)
        rows = await self._session.scalars(query.order_by(models.AccessRequest.id))
        return [_to_request(row) for row in rows]

    async def get_for_update(self, request_id: int) -> entities.AccessRequest | None:
        # populate_existing: без него блокировка берётся на свежей строке, а
        # решение принимается по устаревшему снимку из identity map.
        row = await self._session.scalar(
            select(models.AccessRequest)
            .where(models.AccessRequest.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _to_request(row) if row else None

    async def save(self, request: entities.AccessRequest) -> None:
        row = await self._session.get(models.AccessRequest, request.id)
        row.status = request.status
        row.owner_decided_by = request.owner_decided_by
        row.owner_decided_at = request.owner_decided_at
        row.decision_comment = request.decision_comment
        row.provisioning_error = request.provisioning_error

    @staticmethod
    def _visible_to(actor: entities.User):
        owned = select(models.Resource.id).where(models.Resource.owner_id == actor.id)
        return or_(
            models.AccessRequest.user_id == actor.id,
            models.AccessRequest.resource_id.in_(owned),
        )


class ProvisioningJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: entities.ProvisioningJob) -> None:
        self._session.add(
            models.ProvisioningJob(
                request_id=job.request_id, status=job.status, attempts=job.attempts
            )
        )

    async def get_by_request(self, request_id: int) -> entities.ProvisioningJob | None:
        row = await self._session.scalar(
            select(models.ProvisioningJob).where(models.ProvisioningJob.request_id == request_id)
        )
        return _to_job(row) if row else None

    async def claim_next(self, lease_seconds: int) -> entities.ProvisioningJob | None:
        # SKIP LOCKED даёт нескольким воркерам работать параллельно; lease
        # возвращает задачу в очередь, если процесс умер посреди выдачи.
        now = datetime.now(UTC)
        row = await self._session.scalar(
            select(models.ProvisioningJob)
            .where(
                models.ProvisioningJob.status.in_([JobStatus.PENDING, JobStatus.IN_PROGRESS]),
                or_(
                    models.ProvisioningJob.locked_until.is_(None),
                    models.ProvisioningJob.locked_until < now,
                ),
            )
            .order_by(models.ProvisioningJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None

        job = _to_job(row)
        job.claim(lease_seconds, now=now)
        await self.save(job)
        await self._session.commit()
        return job

    async def save(self, job: entities.ProvisioningJob) -> None:
        row = await self._session.get(models.ProvisioningJob, job.id)
        row.status = job.status
        row.attempts = job.attempts
        row.locked_until = job.locked_until
        row.last_error = job.last_error


class RequestReadModel:
    """Чтение для API: запрос вместе с ресурсом и состоянием выдачи."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, request_ids: list[int]) -> dict[int, models.AccessRequest]:
        if not request_ids:
            return {}
        rows = await self._session.scalars(
            select(models.AccessRequest)
            .options(
                selectinload(models.AccessRequest.resource),
                selectinload(models.AccessRequest.job),
            )
            .where(models.AccessRequest.id.in_(request_ids))
        )
        return {row.id: row for row in rows}
