import logging

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import hash_token
from app.config import get_settings
from app.domain.enums import Criticality, UserRole
from app.infrastructure.db.models import Resource, User

logger = logging.getLogger(__name__)

# Advisory-лок держит одновременные посевы сериализованными: иначе
# "проверил -> вставил" это TOCTOU между процессами.
_SEED_LOCK_KEY = 4815162342


async def seed(session: AsyncSession) -> None:
    settings = get_settings()

    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SEED_LOCK_KEY})

    try:
        for chunk in filter(None, (c.strip() for c in settings.seed_users.split(","))):
            username, role, token = chunk.split(":")
            if role not in set(UserRole):
                raise ValueError(f"неизвестная роль {role!r} для пользователя {username!r}")
            if not token:
                raise ValueError(f"пустой токен для пользователя {username!r}")

            existing = await session.scalar(select(User).where(User.username == username))
            if existing is None:
                session.add(User(username=username, role=role, token_hash=hash_token(token)))
                logger.info("seeded user %s", username)

        await session.flush()

        for chunk in filter(None, (c.strip() for c in settings.seed_resources.split(","))):
            name, owner_username, criticality = chunk.split(":")
            if criticality not in set(Criticality):
                raise ValueError(f"неизвестная критичность {criticality!r} для ресурса {name!r}")

            existing = await session.scalar(select(Resource).where(Resource.name == name))
            if existing is not None:
                continue
            owner = await session.scalar(select(User).where(User.username == owner_username))
            if owner is None:
                raise ValueError(f"владелец {owner_username!r} не найден для ресурса {name!r}")
            session.add(Resource(name=name, owner_id=owner.id, criticality=criticality))
            logger.info("seeded resource %s", name)

        await session.commit()
    except IntegrityError:
        # Текст исходной ошибки может содержать значения параметров.
        await session.rollback()
        raise RuntimeError("посев данных нарушил ограничение целостности БД") from None
