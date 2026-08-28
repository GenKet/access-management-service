import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain import errors as domain

logger = logging.getLogger(__name__)

_DOMAIN_TO_HTTP: dict[type[domain.DomainError], tuple[int, str, str]] = {
    domain.RequestNotFound: (404, "request_not_found", "Запрос не найден"),
    domain.ResourceNotFound: (404, "resource_not_found", "Ресурс не найден"),
    domain.DuplicateRequest: (
        409,
        "request_already_exists",
        "У вас уже есть незавершённый запрос или активный доступ к этому ресурсу",
    ),
    domain.DuplicateProvisioningJob: (
        409,
        "invalid_transition",
        "Запрос уже поставлен в очередь на выдачу доступа",
    ),
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(domain.DomainError)
    async def _domain_error(_: Request, exc: domain.DomainError) -> JSONResponse:
        if isinstance(exc, domain.InvalidTransition):
            status, code, message = 409, "invalid_transition", str(exc)
        elif isinstance(exc, domain.NotAllowed):
            status, code, message = 403, "not_allowed", str(exc)
        else:
            status, code, message = _DOMAIN_TO_HTTP[type(exc)]
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Только type/loc/msg: "input" вернул бы пользовательский ввод целиком.
        details = [
            {"type": err["type"], "loc": err["loc"], "msg": err["msg"]} for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Некорректные входные данные",
                    "details": jsonable_encoder(details),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error while processing %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Внутренняя ошибка сервера"}},
        )
