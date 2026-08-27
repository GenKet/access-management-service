import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.domain.enums import JobStatus, RequestStatus
from app.domain.errors import ProvisioningError
from app.infrastructure.db import repositories as repos
from app.infrastructure.db import session as app_db
from app.infrastructure.db.models import AccessRequest, ExternalAccessGrant, ProvisioningJob
from app.infrastructure.provisioning.fake import FakeProvisioningProvider

ALICE = {"Authorization": "Bearer token-alice"}
BOB = {"Authorization": "Bearer token-bob"}

BROKEN = {"broken-service"}


async def make_provisioning_request(db_session, seeded, resource=None) -> AccessRequest:
    resource = resource or seeded.gitlab
    request = AccessRequest(
        user_id=seeded.alice.id,
        resource_id=resource.id,
        reason="нужен доступ",
        status=RequestStatus.PROVISIONING,
    )
    db_session.add(request)
    await db_session.flush()
    db_session.add(ProvisioningJob(request_id=request.id, status=JobStatus.PENDING, attempts=0))
    await db_session.commit()
    return request


async def release_lease(db_session) -> None:
    await db_session.execute(text("UPDATE provisioning_jobs SET locked_until = NULL"))
    await db_session.commit()


async def count_grants(db_session) -> int:
    return await db_session.scalar(select(func.count()).select_from(ExternalAccessGrant))


async def test_successful_provisioning_activates_request(db_session, seeded, provisioning):
    request = await make_provisioning_request(db_session, seeded)

    assert await provisioning().process_next() is True

    await db_session.refresh(request)
    assert request.status == RequestStatus.ACTIVE
    assert request.provisioning_error is None
    assert await count_grants(db_session) == 1

    job = await db_session.scalar(select(ProvisioningJob))
    assert job.status == JobStatus.DONE
    assert job.locked_until is None


async def test_failing_provisioning_retries_then_marks_failed(db_session, seeded, provisioning):
    request = await make_provisioning_request(db_session, seeded, resource=seeded.broken)
    service = provisioning(fail_names=BROKEN)

    for attempt in range(1, 4):
        await release_lease(db_session)
        assert await service.process_next() is True

        await db_session.refresh(request)
        job = await db_session.scalar(select(ProvisioningJob))
        if attempt < 3:
            assert job.status == JobStatus.PENDING
            assert request.status == RequestStatus.PROVISIONING

    assert request.status == RequestStatus.PROVISIONING_FAILED
    assert "broken-service" in request.provisioning_error

    job = await db_session.scalar(select(ProvisioningJob))
    assert job.status == JobStatus.FAILED
    assert job.attempts == 3
    assert job.last_error
    assert await count_grants(db_session) == 0


async def test_repeated_job_run_does_not_grant_access_twice(db_session, seeded, provisioning):
    request = await make_provisioning_request(db_session, seeded)
    service = provisioning()

    await service.process_next()

    await db_session.execute(
        text("UPDATE provisioning_jobs SET status = 'PENDING', locked_until = NULL")
    )
    await db_session.commit()
    assert await service.process_next() is True

    assert await count_grants(db_session) == 1
    await db_session.refresh(request)
    assert request.status == RequestStatus.ACTIVE

    job = await db_session.scalar(select(ProvisioningJob))
    assert job.status == JobStatus.DONE


async def test_provider_is_idempotent_for_same_pair(db_session, seeded):
    provider = FakeProvisioningProvider(db_session, set())

    await provider.provision_access(seeded.alice.id, seeded.gitlab.id)
    await provider.provision_access(seeded.alice.id, seeded.gitlab.id)
    await db_session.commit()

    assert await count_grants(db_session) == 1


async def test_job_with_live_lease_is_not_claimed(db_session, seeded):
    await make_provisioning_request(db_session, seeded)
    await db_session.execute(
        text("UPDATE provisioning_jobs SET status = 'IN_PROGRESS', locked_until = :until"),
        {"until": datetime.now(UTC) + timedelta(seconds=60)},
    )
    await db_session.commit()

    assert await repos.ProvisioningJobRepository(db_session).claim_next(30) is None


async def test_job_with_expired_lease_is_reclaimed_after_restart(db_session, seeded):
    await make_provisioning_request(db_session, seeded)
    await db_session.execute(
        text("UPDATE provisioning_jobs SET status = 'IN_PROGRESS', locked_until = :until"),
        {"until": datetime.now(UTC) - timedelta(seconds=1)},
    )
    await db_session.commit()

    job = await repos.ProvisioningJobRepository(db_session).claim_next(30)

    assert job is not None
    assert job.attempts == 1
    assert job.status == JobStatus.IN_PROGRESS


async def test_no_pending_jobs_returns_false(db_session, seeded, provisioning):
    assert await provisioning().process_next() is False


async def test_rejected_request_is_never_provisioned(db_session, seeded, provisioning):
    request = await make_provisioning_request(db_session, seeded)
    await db_session.execute(
        text("UPDATE access_requests SET status = 'REJECTED' WHERE id = :id"), {"id": request.id}
    )
    await db_session.commit()

    assert await provisioning().process_next() is True

    assert await count_grants(db_session) == 0
    job = await db_session.scalar(select(ProvisioningJob))
    assert job.status == JobStatus.FAILED


async def test_two_workers_process_one_job_once(db_session, seeded):
    from app.infrastructure.worker import build_service

    await make_provisioning_request(db_session, seeded)
    settings = type("S", (), {})()
    settings.provisioning_fail_resource_names = ""
    settings.provisioning_delay_seconds = 0.2
    settings.worker_lease_seconds = 30
    settings.worker_max_attempts = 3

    async def worker_iteration() -> bool:
        async with app_db.session_factory() as session:
            return await build_service(session, settings).process_next()

    results = await asyncio.gather(worker_iteration(), worker_iteration())

    assert sorted(results) == [False, True]
    assert await count_grants(db_session) == 1


async def test_provider_raises_for_configured_resource(db_session, seeded):
    provider = FakeProvisioningProvider(db_session, BROKEN)

    with pytest.raises(ProvisioningError):
        await provider.provision_access(seeded.alice.id, seeded.broken.id)


async def test_api_shows_provisioning_state(client, seeded, db_session, provisioning):
    created = await client.post(
        "/api/v1/access-requests",
        headers=ALICE,
        json={"resource_id": seeded.broken.id, "reason": "нужен доступ"},
    )
    request_id = created.json()["id"]
    assert created.json()["provisioning"] == {"attempts": 0, "last_error": None}

    await client.post(f"/api/v1/access-requests/{request_id}/approve", headers=BOB)
    await provisioning(fail_names=BROKEN).process_next()

    body = (await client.get(f"/api/v1/access-requests/{request_id}", headers=ALICE)).json()

    assert body["status"] == RequestStatus.PROVISIONING
    assert body["provisioning"]["attempts"] == 1
    assert "broken-service" in body["provisioning"]["last_error"]
