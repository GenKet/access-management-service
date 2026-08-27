import logging
from dataclasses import dataclass

from app.application.ports import (
    AccessRequestRepository,
    ProvisioningJobRepository,
    ProvisioningProvider,
    UnitOfWork,
)
from app.domain.enums import RequestStatus
from app.domain.errors import ProvisioningError

logger = logging.getLogger(__name__)


@dataclass
class ProvisioningService:
    uow: UnitOfWork
    requests: AccessRequestRepository
    jobs: ProvisioningJobRepository
    provider: ProvisioningProvider
    lease_seconds: int
    max_attempts: int

    async def process_next(self) -> bool:
        """Обрабатывает одну задачу. False — задач нет."""
        job = await self.jobs.claim_next(self.lease_seconds)
        if job is None:
            return False

        request = await self.requests.get_for_update(job.request_id)

        if request.status != RequestStatus.PROVISIONING:
            if request.status == RequestStatus.ACTIVE:
                job.succeed()
            else:
                job.abandon()
            await self.jobs.save(job)
            await self.uow.commit()
            logger.info("job for request %s closed: status is %s", request.id, request.status)
            return True

        try:
            await self.provider.provision_access(request.user_id, request.resource_id)
        except ProvisioningError as exc:
            await self._handle_failure(job.request_id, str(exc))
            return True

        request.mark_active()
        job.succeed()
        await self.requests.save(request)
        await self.jobs.save(job)
        await self.uow.commit()
        logger.info("access granted for request %s", request.id)
        return True

    async def _handle_failure(self, request_id: int, error: str) -> None:
        # Откат снимает и частичную запись провайдера, и блокировки, поэтому
        # строки перечитываются заново.
        await self.uow.rollback()

        job = await self.jobs.get_by_request(request_id)
        request = await self.requests.get_for_update(request_id)

        if job.fail(error, self.max_attempts):
            request.mark_provisioning_failed(error)
            await self.requests.save(request)
            logger.error("provisioning failed permanently for request %s: %s", request_id, error)
        else:
            logger.warning(
                "provisioning attempt %s/%s failed for request %s: %s",
                job.attempts,
                self.max_attempts,
                request_id,
                error,
            )

        await self.jobs.save(job)
        await self.uow.commit()
