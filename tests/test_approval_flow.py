import asyncio

from sqlalchemy import select

from app.infrastructure.db.models import AccessRequest, ProvisioningJob

ALICE = {"Authorization": "Bearer token-alice"}
BOB = {"Authorization": "Bearer token-bob"}
SEC = {"Authorization": "Bearer token-sec"}


async def create_request(client, seeded, headers=ALICE, resource=None):
    resource_id = (resource or seeded.gitlab).id
    response = await client.post(
        "/api/v1/access-requests",
        headers=headers,
        json={"resource_id": resource_id, "reason": "нужен доступ"},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_owner_approves_normal_request_and_job_is_created(client, seeded, db_session):
    request_id = await create_request(client, seeded)

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    assert response.status_code == 200
    assert response.json()["status"] == "PROVISIONING"

    job = await db_session.scalar(
        select(ProvisioningJob).where(ProvisioningJob.request_id == request_id)
    )
    assert job is not None
    assert job.status == "PENDING"
    assert job.attempts == 0


async def test_owner_approves_saves_decision_fields(client, seeded):
    request_id = await create_request(client, seeded)

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)

    assert response.status_code == 200
    body = response.json()
    assert body["owner_decided_by"] == seeded.bob.id
    assert body["owner_decided_at"] is not None


async def test_stranger_cannot_approve(client, seeded):
    request_id = await create_request(client, seeded)
    # security-user не владелец gitlab и на этом шаге прав не имеет
    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_allowed"


async def test_requester_cannot_approve_own_request(client, seeded):
    # analytics-dashboard принадлежит alice — она одновременно и заявитель, и
    # владелец ресурса. Проверка «не владелец» здесь пройдёт (она и есть
    # владелец), сработать должен именно запрет самосогласования.
    request_id = await create_request(client, seeded, headers=ALICE, resource=seeded.analytics)
    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=ALICE)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_allowed"


async def test_repeated_approve_returns_409_and_does_not_duplicate_job(client, seeded, db_session):
    request_id = await create_request(client, seeded)

    first = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    assert first.status_code == 200

    second = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_transition"

    jobs = await db_session.scalars(
        select(ProvisioningJob).where(ProvisioningJob.request_id == request_id)
    )
    assert len(list(jobs)) == 1


async def test_owner_rejects_request_and_no_job_is_created(client, seeded, db_session):
    request_id = await create_request(client, seeded)

    response = await client.post(
        f"/api/v1/access-requests/{request_id}/reject",
        headers=BOB,
        json={"comment": "нет обоснования"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["decision_comment"] == "нет обоснования"

    job = await db_session.scalar(
        select(ProvisioningJob).where(ProvisioningJob.request_id == request_id)
    )
    assert job is None


async def test_approve_after_reject_returns_409(client, seeded):
    request_id = await create_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/reject", headers=BOB, json={})
    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    assert response.status_code == 409


async def test_stranger_approve_of_decided_request_returns_409_not_403(client, seeded):
    # bob уже решил заявку (reject). security-user — посторонний: не
    # владелец ресурса и не заявитель. Порядок «статус → права» требует,
    # чтобы даже для постороннего ответ был 409, а не 403 — это отличает от
    # test_stranger_cannot_approve, где заявка ещё PENDING и посторонний
    # закономерно получает 403 на первой же проверке прав.
    request_id = await create_request(client, seeded)
    await client.post(f"/api/v1/access-requests/{request_id}/reject", headers=BOB, json={})

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=SEC)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


async def test_concurrent_approve_is_serialized_by_row_lock(client, seeded, db_session):
    # Независимые пары (заявитель, ресурс): после успешного approve заявка
    # остаётся occupying и вторую такую же завести нельзя.
    scenarios = [
        (ALICE, seeded.gitlab),
        (SEC, seeded.gitlab),
        (ALICE, seeded.production_db),
        (SEC, seeded.production_db),
        (ALICE, seeded.broken),
    ]
    for headers, resource in scenarios:
        request_id = await create_request(client, seeded, headers=headers, resource=resource)

        first, second = await asyncio.gather(
            client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB),
            client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB),
        )

        assert sorted([first.status_code, second.status_code]) == [200, 409]

        jobs = await db_session.scalars(
            select(ProvisioningJob).where(ProvisioningJob.request_id == request_id)
        )
        assert len(list(jobs)) == 1


async def test_concurrent_approve_and_reject_is_serialized_by_row_lock(client, seeded, db_session):
    # Ключевой сценарий гонки: approve и reject ОДНОГО запроса одновременно.
    # ProvisioningJob.request_id уникален и сам по себе ловит гонку
    # approve‖approve (см. тест выше), но эта пара его не задействует — reject
    # ничего не вставляет в provisioning_jobs. Без SELECT ... FOR UPDATE обе
    # транзакции читают статус PENDING_OWNER_APPROVAL до commit друг друга и
    # обе проходят проверку статуса; UPDATE в Postgres блокируется физически,
    # но без повторной проверки после снятия блокировки «последний write»
    # молча перезаписывает решение — обе ручки вернут 200, а в очереди
    # останется живая задача по отклонённой заявке.
    request_id = await create_request(client, seeded)

    approve_resp, reject_resp = await asyncio.gather(
        client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB),
        client.post(f"/api/v1/access-requests/{request_id}/reject", headers=BOB, json={}),
    )

    assert sorted([approve_resp.status_code, reject_resp.status_code]) == [200, 409]

    db_session.expire_all()
    request = await db_session.get(AccessRequest, request_id)
    job = await db_session.scalar(
        select(ProvisioningJob).where(ProvisioningJob.request_id == request_id)
    )

    if approve_resp.status_code == 200:
        assert request.status == "PROVISIONING"
        assert job is not None
    else:
        assert request.status == "REJECTED"
        assert job is None


async def test_approve_with_out_of_range_request_id_returns_422_not_500(client, seeded):
    huge_id = 2**63  # за пределами PostgreSQL BIGINT (MAX_BIGINT = 2**63 - 1)
    response = await client.post(f"/api/v1/access-requests/{huge_id}/approve", headers=BOB)
    assert response.status_code == 422


async def test_reject_with_out_of_range_request_id_returns_422_not_500(client, seeded):
    huge_id = 2**63
    response = await client.post(f"/api/v1/access-requests/{huge_id}/reject", headers=BOB, json={})
    assert response.status_code == 422


async def test_decision_comment_with_control_char_is_rejected(client, seeded):
    request_id = await create_request(client, seeded)
    response = await client.post(
        f"/api/v1/access-requests/{request_id}/reject",
        headers=BOB,
        json={"comment": "\x00nul"},
    )
    assert response.status_code == 422


async def test_decision_comment_blank_after_strip_becomes_none(client, seeded):
    request_id = await create_request(client, seeded)
    response = await client.post(
        f"/api/v1/access-requests/{request_id}/reject",
        headers=BOB,
        json={"comment": "   "},
    )
    assert response.status_code == 200
    assert response.json()["decision_comment"] is None


async def test_approve_rolls_back_completely_when_job_insert_fails(client, seeded, db_session):
    # Симулируем сбой постановки задачи, не трогая код: заранее занимаем
    # request_id в provisioning_jobs (уникальный индекс на request_id).
    # Вставка внутри approve() наткнётся на этот же констрейнт — так же, как
    # наткнулась бы при реальной гонке или задвоенном вызове.
    request_id = await create_request(client, seeded)
    db_session.add(ProvisioningJob(request_id=request_id, status="PENDING", attempts=0))
    await db_session.commit()

    response = await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"

    # Переход статуса и простановка полей решения должны были откатиться
    # целиком вместе с неудавшейся вставкой job — это и есть проверка того,
    # что «статус → PROVISIONING» и «вставка ProvisioningJob» одна транзакция.
    db_session.expire_all()
    request = await db_session.get(AccessRequest, request_id)
    assert request.status == "PENDING_OWNER_APPROVAL"
    assert request.owner_decided_by is None
    assert request.owner_decided_at is None
    assert request.decision_comment is None

    jobs = await db_session.scalars(
        select(ProvisioningJob).where(ProvisioningJob.request_id == request_id)
    )
    assert len(list(jobs)) == 1
