import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.enums import OCCUPYING_STATUSES, TERMINAL_STATUSES


async def test_only_one_occupying_request_per_user_and_resource(db_session, seeded):
    await db_session.execute(
        text(
            "INSERT INTO access_requests (user_id, resource_id, reason, status) "
            "VALUES (:u, :r, 'first', 'PENDING_OWNER_APPROVAL')"
        ),
        {"u": seeded.alice.id, "r": seeded.gitlab.id},
    )
    await db_session.commit()

    with pytest.raises(IntegrityError, match="uq_access_request_occupying"):
        await db_session.execute(
            text(
                "INSERT INTO access_requests (user_id, resource_id, reason, status) "
                "VALUES (:u, :r, 'second', 'PENDING_OWNER_APPROVAL')"
            ),
            {"u": seeded.alice.id, "r": seeded.gitlab.id},
        )


@pytest.mark.parametrize("status", ["PROVISIONING", "ACTIVE"])
async def test_occupying_statuses_are_blocked_by_index(db_session, seeded, status):
    await db_session.execute(
        text(
            "INSERT INTO access_requests (user_id, resource_id, reason, status) "
            "VALUES (:u, :r, 'first', :s)"
        ),
        {"u": seeded.alice.id, "r": seeded.gitlab.id, "s": status},
    )
    await db_session.commit()

    with pytest.raises(IntegrityError, match="uq_access_request_occupying"):
        await db_session.execute(
            text(
                "INSERT INTO access_requests (user_id, resource_id, reason, status) "
                "VALUES (:u, :r, 'second', 'PENDING_OWNER_APPROVAL')"
            ),
            {"u": seeded.alice.id, "r": seeded.gitlab.id},
        )


async def test_non_occupying_terminal_statuses_do_not_block_new_request(db_session, seeded):
    # ACTIVE — тоже терминальный статус, но он occupying (см. OCCUPYING_STATUSES)
    # и блокирует повторную вставку — это отдельно проверяется в
    # test_occupying_statuses_are_blocked_by_index. Здесь проверяем только
    # терминальные статусы, которые слот не занимают.
    non_occupying_terminal = sorted(TERMINAL_STATUSES - OCCUPYING_STATUSES)
    statuses = [*non_occupying_terminal, "PENDING_OWNER_APPROVAL"]
    for status in statuses:
        await db_session.execute(
            text(
                "INSERT INTO access_requests (user_id, resource_id, reason, status) "
                "VALUES (:u, :r, 'x', :s)"
            ),
            {"u": seeded.alice.id, "r": seeded.gitlab.id, "s": status},
        )
    await db_session.commit()

    count = await db_session.scalar(
        text("SELECT count(*) FROM access_requests WHERE user_id = :u AND resource_id = :r"),
        {"u": seeded.alice.id, "r": seeded.gitlab.id},
    )
    assert count == len(statuses)
