"""security approval для high-ресурсов

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUSES = (
    "'ACTIVE', 'PENDING_OWNER_APPROVAL', 'PROVISIONING', 'PROVISIONING_FAILED', 'REJECTED'"
)
_NEW_STATUSES = (
    "'ACTIVE', 'PENDING_OWNER_APPROVAL', 'PENDING_SECURITY_APPROVAL', "
    "'PROVISIONING', 'PROVISIONING_FAILED', 'REJECTED'"
)

_OLD_OCCUPYING = "'ACTIVE', 'PENDING_OWNER_APPROVAL', 'PROVISIONING'"
_NEW_OCCUPYING = "'ACTIVE', 'PENDING_OWNER_APPROVAL', 'PENDING_SECURITY_APPROVAL', 'PROVISIONING'"


def upgrade() -> None:
    op.add_column(
        "access_requests", sa.Column("security_decided_by", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "access_requests",
        sa.Column("security_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_access_requests_security_decided_by"),
        "access_requests",
        "users",
        ["security_decided_by"],
        ["id"],
    )

    # Статусы хранятся как VARCHAR с CHECK именно ради этого шага: расширение
    # набора значений — обычный ALTER TABLE, без ограничений
    # ALTER TYPE ... ADD VALUE внутри транзакции.
    op.drop_constraint(op.f("ck_access_requests_status"), "access_requests", type_="check")
    op.create_check_constraint(
        op.f("ck_access_requests_status"), "access_requests", f"status IN ({_NEW_STATUSES})"
    )

    # Запрос, ждущий решения security, тоже занимает пару сотрудник+ресурс.
    op.drop_index("uq_access_request_occupying", table_name="access_requests")
    op.create_index(
        "uq_access_request_occupying",
        "access_requests",
        ["user_id", "resource_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_NEW_OCCUPYING})"),
    )


def downgrade() -> None:
    op.drop_index("uq_access_request_occupying", table_name="access_requests")
    op.create_index(
        "uq_access_request_occupying",
        "access_requests",
        ["user_id", "resource_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_OLD_OCCUPYING})"),
    )

    op.drop_constraint(op.f("ck_access_requests_status"), "access_requests", type_="check")
    op.create_check_constraint(
        op.f("ck_access_requests_status"), "access_requests", f"status IN ({_OLD_STATUSES})"
    )

    op.drop_constraint(
        op.f("fk_access_requests_security_decided_by"), "access_requests", type_="foreignkey"
    )
    op.drop_column("access_requests", "security_decided_at")
    op.drop_column("access_requests", "security_decided_by")
