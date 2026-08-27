import asyncio

from sqlalchemy import func, select

from app.domain.enums import RequestStatus
from app.infrastructure.db.models import ProvisioningJob

ALICE = {"Authorization": "Bearer token-alice"}
BOB = {"Authorization": "Bearer token-bob"}
SEC = {"Authorization": "Bearer token-sec"}


async def create_high_request(client, seeded) -> int:
    response = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.production_db.id, "reason": "инцидент на проде"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == RequestStatus.PENDING_OWNER_APPROVAL
    return response.json()["id"]


async def count_jobs(db_session, request_id: int) -> int:
    return await db_session.scalar(
        select(func.count())
        .select_from(ProvisioningJob)
        .where(ProvisioningJob.request_id == request_id)
    )


async def test_owner_approval_moves_high_request_to_security(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    assert response.status_code == 200
    assert response.json()["status"] == RequestStatus.PENDING_SECURITY_APPROVAL
    assert response.json()["owner_decided_by"] == seeded.bob.id
    assert response.json()["security_decided_by"] is None
    assert (
        await count_jobs(db_session, request_id) == 0
    ), "provisioning не должен стартовать до согласования security"


async def test_full_high_flow_reaches_provisioning(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == RequestStatus.PROVISIONING
    assert body["security_decided_by"] == seeded.security.id
    assert body["security_decided_at"] is not None
    assert await count_jobs(db_session, request_id) == 1


async def test_high_request_reaches_active_after_worker(client, seeded, db_session, provisioning):
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)

    await provisioning().process_next()

    response = await client.get(f"/api/v1/access-requests/{request_id}", headers=ALICE)
    assert response.json()["status"] == RequestStatus.ACTIVE


async def test_security_cannot_approve_before_owner(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_allowed"
    assert await count_jobs(db_session, request_id) == 0


async def test_owner_cannot_perform_security_approval(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    second = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    assert second.status_code == 403
    assert second.json()["error"]["code"] == "not_allowed"
    assert await count_jobs(db_session, request_id) == 0


async def test_security_reject_prevents_provisioning(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    response = await client.post(
        f"/api/v1/access-requests/{request_id}/reject",
        headers=SEC,
        json={"comment": "недостаточное обоснование"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == RequestStatus.REJECTED
    assert body["security_decided_by"] == seeded.security.id
    assert body["decision_comment"] == "недостаточное обоснование"
    assert await count_jobs(db_session, request_id) == 0


async def test_owner_reject_of_high_request_prevents_security_step(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)

    response = await client.post(f"/api/v1/access-requests/{request_id}/reject", headers=BOB)

    assert response.json()["status"] == RequestStatus.REJECTED
    # После отклонения владельцем security-шага уже не будет.
    late = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)
    assert late.status_code == 409
    assert await count_jobs(db_session, request_id) == 0


async def test_repeated_security_approve_returns_409(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)

    third = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)

    assert third.status_code == 409
    assert third.json()["error"]["code"] == "invalid_transition"
    assert await count_jobs(db_session, request_id) == 1


async def test_concurrent_security_approves_are_serialized(client, seeded, db_session):
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    first, second = await asyncio.gather(
        client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC),
        client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert await count_jobs(db_session, request_id) == 1


async def test_pending_security_request_blocks_duplicate(client, seeded):
    """Запрос, ждущий security, занимает пару сотрудник+ресурс так же, как остальные."""
    request_id = await create_high_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    duplicate = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.production_db.id, "reason": "ещё раз"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "request_already_exists"


async def test_normal_flow_is_unchanged(client, seeded, db_session):
    """Регрессия: для normal-ресурсов процесс остался прежним, без шага security."""
    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.gitlab.id, "reason": "нужен доступ"},
    )
    request_id = created.json()["id"]

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    assert response.json()["status"] == RequestStatus.PROVISIONING
    assert response.json()["security_decided_by"] is None
    assert await count_jobs(db_session, request_id) == 1
