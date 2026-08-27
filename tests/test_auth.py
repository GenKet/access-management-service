from fastapi import Request

from app.api.dependencies import current_user


async def test_request_without_token_is_rejected(client):
    response = await client.get("/api/v1/resources")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_request_with_unknown_token_is_rejected(client):
    response = await client.get("/api/v1/resources", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_valid_token_identifies_user(client, seeded):
    response = await client.get(
        "/api/v1/resources", headers={"Authorization": "Bearer token-alice"}
    )
    assert response.status_code == 200


def _fake_request(headers: dict[str, str] | None = None, query_string: str = "") -> Request:
    # /api/v1/resources не отражает личность вызывающего в теле ответа
    # (список ресурсов один и тот же для всех), поэтому единственный
    # надёжный способ проверить, ЧЬЮ личность резолвит current_user, — это
    # вызвать саму зависимость напрямую с сконструированным Request.
    raw_headers = [
        (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/resources",
        "query_string": query_string.encode(),
        "headers": raw_headers,
        "client": ("test-client", 12345),
    }
    return Request(scope)


async def test_impersonation_via_query_params_without_token_is_rejected(client):
    response = await client.get(
        "/api/v1/resources",
        params={"user_id": 1, "role": "security", "owner_id": 1},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_impersonation_via_query_params_is_ignored_identity_stays_token_owner(
    db_session, seeded
):
    # Клиент пытается объявить себя владельцем security-пользователя через
    # query-параметры, но с реальным токеном alice личность обязана
    # остаться alice.
    request = _fake_request(
        headers={"Authorization": "Bearer token-alice"},
        query_string=f"user_id={seeded.security.id}&role=security&owner_id={seeded.bob.id}",
    )
    user = await current_user(request, session=db_session)
    assert user.id == seeded.alice.id
    assert user.username == "alice"
    assert user.role == "employee"


async def test_impersonation_headers_and_cookies_are_ignored(db_session, seeded):
    request = _fake_request(
        headers={
            "Authorization": "Bearer token-alice",
            "X-User-Id": str(seeded.security.id),
            "X-Role": "security",
            "Cookie": "role=security",
        }
    )
    user = await current_user(request, session=db_session)
    assert user.id == seeded.alice.id
    assert user.username == "alice"
    assert user.role != "security"


async def test_different_tokens_resolve_to_different_identities(db_session, seeded):
    alice_request = _fake_request(headers={"Authorization": "Bearer token-alice"})
    bob_request = _fake_request(headers={"Authorization": "Bearer token-bob"})

    alice = await current_user(alice_request, session=db_session)
    bob = await current_user(bob_request, session=db_session)

    assert alice.id != bob.id
    assert {alice.username, bob.username} == {"alice", "bob"}
