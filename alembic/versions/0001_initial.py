"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-27 20:42:55.096135

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_access_grants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_access_grants")),
        sa.UniqueConstraint("user_id", "resource_id", name="uq_external_grant"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("role IN ('employee', 'security')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_users_token_hash")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "resources",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("criticality", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "criticality IN ('normal', 'high')", name=op.f("ck_resources_criticality")
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_resources_owner_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
        sa.UniqueConstraint("name", name=op.f("uq_resources_name")),
    )
    op.create_table(
        "access_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_decided_by", sa.BigInteger(), nullable=True),
        sa.Column("owner_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("provisioning_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_OWNER_APPROVAL', 'PROVISIONING', 'ACTIVE', 'REJECTED', "
            "'PROVISIONING_FAILED')",
            name=op.f("ck_access_requests_status"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_decided_by"], ["users.id"], name=op.f("fk_access_requests_owner_decided_by")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resources.id"], name=op.f("fk_access_requests_resource_id")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_access_requests_user_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_requests")),
    )
    op.create_index(
        op.f("ix_access_requests_resource_id"), "access_requests", ["resource_id"], unique=False
    )
    op.create_index(op.f("ix_access_requests_status"), "access_requests", ["status"], unique=False)
    op.create_index(
        op.f("ix_access_requests_user_id"), "access_requests", ["user_id"], unique=False
    )
    op.create_index(
        "uq_access_request_occupying",
        "access_requests",
        ["user_id", "resource_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ACTIVE', 'PENDING_OWNER_APPROVAL', 'PROVISIONING')"),
    )
    op.create_table(
        "provisioning_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED')",
            name=op.f("ck_provisioning_jobs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["access_requests.id"], name=op.f("fk_provisioning_jobs_request_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provisioning_jobs")),
        sa.UniqueConstraint("request_id", name=op.f("uq_provisioning_jobs_request_id")),
    )
    op.create_index(
        op.f("ix_provisioning_jobs_status"), "provisioning_jobs", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_provisioning_jobs_status"), table_name="provisioning_jobs")
    op.drop_table("provisioning_jobs")
    op.drop_index(
        "uq_access_request_occupying",
        table_name="access_requests",
        postgresql_where=sa.text("status IN ('ACTIVE', 'PENDING_OWNER_APPROVAL', 'PROVISIONING')"),
    )
    op.drop_index(op.f("ix_access_requests_user_id"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_status"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_resource_id"), table_name="access_requests")
    op.drop_table("access_requests")
    op.drop_table("resources")
    op.drop_table("users")
    op.drop_table("external_access_grants")
