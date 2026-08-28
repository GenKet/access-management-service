import asyncio
import logging

from app.application.provisioning import ProvisioningService
from app.config import Settings, get_settings
from app.infrastructure.db import repositories as repos
from app.infrastructure.db import session as db
from app.infrastructure.provisioning.fake import FakeProvisioningProvider, fail_resource_names

logger = logging.getLogger(__name__)


def build_service(session, settings: Settings) -> ProvisioningService:
    return ProvisioningService(
        uow=repos.SqlAlchemyUnitOfWork(session),
        requests=repos.AccessRequestRepository(session),
        jobs=repos.ProvisioningJobRepository(session),
        provider=FakeProvisioningProvider(
            session,
            fail_resource_names=fail_resource_names(settings),
            delay_seconds=settings.provisioning_delay_seconds,
        ),
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.worker_max_attempts,
    )


async def run_worker() -> None:
    settings = get_settings()
    logger.info("worker started")
    while True:
        did_work = False
        try:
            async with db.session_factory() as session:
                did_work = await build_service(session, settings).process_next()
        except Exception:
            # Воркер не должен умирать: ни от недоступной БД на старте, ни от
            # ошибки в одной задаче.
            logger.exception("worker iteration failed")
        if not did_work:
            await asyncio.sleep(settings.worker_poll_interval)
