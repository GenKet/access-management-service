from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import OCCUPYING_STATUSES, Criticality, JobStatus, RequestStatus, UserRole


class Base(DeclarativeBase):
    # Явные имена констрейнтов: без них миграция второго этапа не сможет
    # адресовать CHECK по имени.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _in_check(column: str, values) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)

    __table_args__ = (CheckConstraint(_in_check("role", list(UserRole)), name="role"),)


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    criticality: Mapped[str] = mapped_column(String(16))

    owner: Mapped["User"] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint(_in_check("criticality", list(Criticality)), name="criticality"),
    )


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)

    owner_decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    owner_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    security_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    provisioning_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    resource: Mapped["Resource"] = relationship(lazy="raise")
    job: Mapped["ProvisioningJob | None"] = relationship(lazy="raise", back_populates="request")

    __table_args__ = (
        CheckConstraint(_in_check("status", list(RequestStatus)), name="status"),
        Index(
            "uq_access_request_occupying",
            "user_id",
            "resource_id",
            unique=True,
            postgresql_where=text(_in_check("status", sorted(OCCUPYING_STATUSES))),
        ),
    )


class ProvisioningJob(Base):
    __tablename__ = "provisioning_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("access_requests.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    request: Mapped["AccessRequest"] = relationship(lazy="raise", back_populates="job")

    __table_args__ = (CheckConstraint(_in_check("status", list(JobStatus)), name="status"),)


class ExternalAccessGrant(Base):
    """Состояние внешней системы. Уникальность пары делает выдачу идемпотентной."""

    __tablename__ = "external_access_grants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    resource_id: Mapped[int] = mapped_column(BigInteger)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "resource_id", name="uq_external_grant"),)
