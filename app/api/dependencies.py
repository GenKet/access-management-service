import hashlib
import logging

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.application.access_requests import AccessRequestService
from app.config import get_settings
from app.domain.entities import User
from app.infrastructure.db import repositories as repos
from app.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def hash_token(token: str) -> str:
    # Годится только для высокоэнтропийных случайных токенов, не для паролей.
    return hashlib.sha256(token.encode()).hexdigest()


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    client_ip = request.client.host if request.client else "unknown"
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")

    if scheme.lower() != "bearer" or not token:
        logger.warning("unauthorized: missing or malformed header (client=%s)", client_ip)
        raise ApiError(
            401,
            "unauthorized",
            "Требуется заголовок Authorization: Bearer <token>",
            headers=_WWW_AUTHENTICATE,
        )

    user = await repos.UserRepository(session).get_by_token_hash(hash_token(token))
    if user is None:
        logger.warning("unauthorized: unknown token (client=%s)", client_ip)
        raise ApiError(401, "unauthorized", "Неизвестный токен", headers=_WWW_AUTHENTICATE)
    return user


def get_access_request_service(
    session: AsyncSession = Depends(get_session),
) -> AccessRequestService:
    return AccessRequestService(
        uow=repos.SqlAlchemyUnitOfWork(session),
        requests=repos.AccessRequestRepository(session),
        resources=repos.ResourceRepository(session),
        jobs=repos.ProvisioningJobRepository(session),
    )


def get_read_model(session: AsyncSession = Depends(get_session)) -> repos.RequestReadModel:
    return repos.RequestReadModel(session)


def get_resource_repository(
    session: AsyncSession = Depends(get_session),
) -> repos.ResourceRepository:
    return repos.ResourceRepository(session)


__all__ = [
    "current_user",
    "get_access_request_service",
    "get_read_model",
    "get_resource_repository",
    "get_settings",
    "hash_token",
]
