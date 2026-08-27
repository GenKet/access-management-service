from app.api.dependencies import hash_token
from app.domain.enums import Criticality, RequestStatus, UserRole
from app.infrastructure.db.models import AccessRequest, Resource, User

ALICE = {"Authorization": "Bearer token-alice"}
BOB = {"Authorization": "Bearer token-bob"}
SECURITY = {"Authorization": "Bearer token-sec"}


async def test_employee_creates_request(client, seeded):
    response = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ к репозиториям"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING_OWNER_APPROVAL"
    assert body["resource"]["name"] == "gitlab"


async def test_request_to_unknown_resource_returns_404(client, seeded):
    response = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": 999999, "reason": "нужен доступ"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


async def test_empty_reason_is_rejected(client, seeded):
    response = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "   "},
    )
    assert response.status_code == 422

    # обоснование обрезается по краям, а не просто проверяется на пустоту
    trimmed = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "  ok  "},
    )
    assert trimmed.status_code == 201
    assert trimmed.json()["reason"] == "ok"


async def test_duplicate_pending_request_returns_409(client, seeded):
    payload = {"resource_id": seeded.gitlab.id, "reason": "нужен доступ"}
    first = await client.post("/api/v1/access-requests", headers=ALICE, json=payload)
    assert first.status_code == 201
    second = await client.post("/api/v1/access-requests", headers=ALICE, json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "request_already_exists"


async def test_user_sees_own_requests_and_owner_sees_incoming(client, seeded):
    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ"},
    )
    request_id = created.json()["id"]

    own = await client.get("/api/v1/access-requests", headers=ALICE)
    assert [r["id"] for r in own.json()] == [request_id]

    # bob — владелец gitlab, видит входящий запрос
    incoming = await client.get("/api/v1/access-requests", headers=BOB)
    assert [r["id"] for r in incoming.json()] == [request_id]


async def test_stranger_cannot_read_foreign_request(client, seeded):
    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.analytics.id, "reason": "нужен доступ"},
    )
    request_id = created.json()["id"]
    # analytics-dashboard принадлежит alice, bob к запросу отношения не имеет
    response = await client.get(f"/api/v1/access-requests/{request_id}", headers=BOB)
    assert response.status_code == 404


async def test_visibility_excludes_fully_unrelated_requests(client, seeded, db_session):
    # Все seeded-ресурсы принадлежат alice/bob, поэтому для по-настоящему
    # постороннего запроса (ни requester, ни owner ресурса не alice/bob)
    # нужен третий сотрудник со своим ресурсом — заводим его напрямую в БД.
    carol = User(username="carol", role=UserRole.EMPLOYEE, token_hash=hash_token("token-carol"))
    db_session.add(carol)
    await db_session.flush()
    carol_tool = Resource(name="carol-tool", owner_id=carol.id, criticality=Criticality.NORMAL)
    db_session.add(carol_tool)
    await db_session.flush()
    unrelated = AccessRequest(
        user_id=carol.id,
        resource_id=carol_tool.id,
        reason="запрос постороннего",
        status=RequestStatus.PENDING_OWNER_APPROVAL,
    )
    db_session.add(unrelated)
    await db_session.commit()

    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ"},
    )
    alice_request_id = created.json()["id"]

    own = await client.get("/api/v1/access-requests", headers=ALICE)
    own_ids = [r["id"] for r in own.json()]
    assert alice_request_id in own_ids
    assert unrelated.id not in own_ids

    incoming = await client.get("/api/v1/access-requests", headers=BOB)
    incoming_ids = [r["id"] for r in incoming.json()]
    assert alice_request_id in incoming_ids
    assert unrelated.id not in incoming_ids


async def test_status_query_filter_does_not_bypass_visibility(client, seeded, db_session):
    carol = User(username="carol2", role=UserRole.EMPLOYEE, token_hash=hash_token("token-carol2"))
    db_session.add(carol)
    await db_session.commit()

    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ"},
    )
    assert created.status_code == 201

    response = await client.get(
        "/api/v1/access-requests",
        headers={"Authorization": "Bearer token-carol2"},
        params={"status": "PENDING_OWNER_APPROVAL"},
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_resource_id_query_filter_does_not_bypass_visibility(client, seeded, db_session):
    carol = User(username="carol3", role=UserRole.EMPLOYEE, token_hash=hash_token("token-carol3"))
    db_session.add(carol)
    await db_session.commit()

    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ"},
    )
    assert created.status_code == 201

    response = await client.get(
        "/api/v1/access-requests",
        headers={"Authorization": "Bearer token-carol3"},
        params={"resource_id": seeded.gitlab.id},
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_security_role_sees_all_requests(client, seeded, db_session):
    carol = User(username="carol4", role=UserRole.EMPLOYEE, token_hash=hash_token("token-carol4"))
    db_session.add(carol)
    await db_session.flush()
    carol_tool = Resource(name="carol-tool-2", owner_id=carol.id, criticality=Criticality.NORMAL)
    db_session.add(carol_tool)
    await db_session.flush()
    unrelated = AccessRequest(
        user_id=carol.id,
        resource_id=carol_tool.id,
        reason="запрос постороннего",
        status=RequestStatus.PENDING_OWNER_APPROVAL,
    )
    db_session.add(unrelated)
    await db_session.commit()

    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ"},
    )
    alice_request_id = created.json()["id"]

    response = await client.get("/api/v1/access-requests", headers=SECURITY)
    ids = [r["id"] for r in response.json()]
    assert alice_request_id in ids
    assert unrelated.id in ids
