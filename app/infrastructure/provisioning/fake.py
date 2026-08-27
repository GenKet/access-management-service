import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.errors import ProvisioningError
from app.infrastructure.db import models


class FakeProvisioningProvider:
    """Заглушка внешней системы: детерминированный отказ, идемпотентная выдача."""

    def __init__(
        self,
        session: AsyncSession,
        fail_resource_names: set[str],
        delay_seconds: float = 0.0,
    ) -> None:
        self._session = session
        self._fail_resource_names = fail_resource_names
        self._delay_seconds = delay_seconds

    async def provision_access(self, user_id: int, resource_id: int) -> None:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)

        name = await self._session.scalar(
            select(models.Resource.name).where(models.Resource.id == resource_id)
        )
        if name in self._fail_resource_names:
            raise ProvisioningError(f"внешняя система отклонила выдачу доступа к «{name}»")

        await self._session.execute(
            insert(models.ExternalAccessGrant)
            .values(user_id=user_id, resource_id=resource_id)
            .on_conflict_do_nothing(constraint="uq_external_grant")
        )


def fail_resource_names(settings: Settings) -> set[str]:
    return {n.strip() for n in settings.provisioning_fail_resource_names.split(",") if n.strip()}
