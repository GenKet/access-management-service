from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, field_validator

from app.api.errors import register_error_handlers


class _Payload(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Валидатор, бросающий ValueError, — именно этот случай кладёт в
        # ctx["error"] pydantic v2 исходный объект исключения, который
        # json.dumps не умеет сериализовать без jsonable_encoder.
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value


def _build_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/validate")
    async def _validate(payload: _Payload) -> dict:
        return {"ok": True}

    return app


async def test_field_validator_error_is_serialized_as_422():
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/validate", json={"reason": "   "})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Некорректные входные данные"

    details = body["error"]["details"]
    assert len(details) == 1
    assert set(details[0].keys()) == {"type", "loc", "msg"}
    assert "input" not in details[0]
    assert "url" not in details[0]
