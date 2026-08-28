from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.enums import Criticality, JobStatus, RequestStatus, UserRole
from app.domain.errors import InvalidTransition, NotAllowed


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class User:
    id: int
    username: str
    role: UserRole

    @property
    def is_security(self) -> bool:
        return self.role == UserRole.SECURITY


@dataclass
class Resource:
    id: int
    name: str
    owner_id: int
    criticality: Criticality

    @property
    def needs_security_approval(self) -> bool:
        return self.criticality == Criticality.HIGH

    def is_owned_by(self, user: User) -> bool:
        return self.owner_id == user.id


@dataclass
class ProvisioningJob:
    request_id: int
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    locked_until: datetime | None = None
    last_error: str | None = None
    id: int | None = None

    def claim(self, lease_seconds: int, now: datetime | None = None) -> None:
        now = now or _now()
        self.status = JobStatus.IN_PROGRESS
        self.attempts += 1
        self.locked_until = now + timedelta(seconds=lease_seconds)

    def succeed(self) -> None:
        self.status = JobStatus.DONE
        self.locked_until = None

    def fail(self, error: str, max_attempts: int) -> bool:
        """Возвращает True, если попытки исчерпаны и запрос надо признать провалившимся."""
        self.last_error = error
        self.locked_until = None
        if self.attempts >= max_attempts:
            self.status = JobStatus.FAILED
            return True
        self.status = JobStatus.PENDING
        return False

    def abandon(self) -> None:
        self.status = JobStatus.FAILED
        self.locked_until = None


@dataclass
class AccessRequest:
    """Корень агрегата: только здесь меняется состояние запроса на доступ."""

    user_id: int
    resource_id: int
    reason: str
    status: RequestStatus = RequestStatus.PENDING_OWNER_APPROVAL
    owner_decided_by: int | None = None
    owner_decided_at: datetime | None = None
    security_decided_by: int | None = None
    security_decided_at: datetime | None = None
    decision_comment: str | None = None
    provisioning_error: str | None = None
    id: int | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            RequestStatus.ACTIVE,
            RequestStatus.REJECTED,
            RequestStatus.PROVISIONING_FAILED,
        }

    def approve(self, actor: User, resource: Resource, comment: str | None = None) -> bool:
        """Одобряет текущий шаг. True — пора запускать выдачу."""
        # Статус проверяется раньше прав: повторное одобрение — конфликт
        # состояния, а не нехватка прав.
        if self.status == RequestStatus.PENDING_OWNER_APPROVAL:
            self._ensure_owner(actor, resource)
            self.owner_decided_by = actor.id
            self.owner_decided_at = _now()
            self.decision_comment = comment
            if resource.needs_security_approval:
                self.status = RequestStatus.PENDING_SECURITY_APPROVAL
                return False
            self.status = RequestStatus.PROVISIONING
            return True

        if self.status == RequestStatus.PENDING_SECURITY_APPROVAL:
            self._ensure_security(actor)
            self.security_decided_by = actor.id
            self.security_decided_at = _now()
            self.decision_comment = comment
            self.status = RequestStatus.PROVISIONING
            return True

        raise InvalidTransition(self.status, "согласован")

    def reject(self, actor: User, resource: Resource, comment: str | None = None) -> None:
        if self.status == RequestStatus.PENDING_OWNER_APPROVAL:
            self._ensure_owner(actor, resource)
            self.owner_decided_by = actor.id
            self.owner_decided_at = _now()
        elif self.status == RequestStatus.PENDING_SECURITY_APPROVAL:
            self._ensure_security(actor)
            self.security_decided_by = actor.id
            self.security_decided_at = _now()
        else:
            raise InvalidTransition(self.status, "отклонён")

        self.status = RequestStatus.REJECTED
        self.decision_comment = comment

    def mark_active(self) -> None:
        self.status = RequestStatus.ACTIVE
        self.provisioning_error = None

    def mark_provisioning_failed(self, error: str) -> None:
        self.status = RequestStatus.PROVISIONING_FAILED
        self.provisioning_error = error

    def _ensure_owner(self, actor: User, resource: Resource) -> None:
        if not resource.is_owned_by(actor):
            raise NotAllowed("Решение по запросу принимает владелец ресурса")
        if self.user_id == actor.id:
            raise NotAllowed("Нельзя согласовывать собственный запрос")

    def _ensure_security(self, actor: User) -> None:
        if not actor.is_security:
            raise NotAllowed("Этот шаг согласования выполняет security-роль")
