from enum import StrEnum


class UserRole(StrEnum):
    EMPLOYEE = "employee"
    SECURITY = "security"


class Criticality(StrEnum):
    NORMAL = "normal"
    HIGH = "high"


class RequestStatus(StrEnum):
    PENDING_OWNER_APPROVAL = "PENDING_OWNER_APPROVAL"
    PENDING_SECURITY_APPROVAL = "PENDING_SECURITY_APPROVAL"
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    PROVISIONING_FAILED = "PROVISIONING_FAILED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"


TERMINAL_STATUSES = frozenset(
    {RequestStatus.ACTIVE, RequestStatus.REJECTED, RequestStatus.PROVISIONING_FAILED}
)

# Пара «сотрудник + ресурс» занята, пока запрос в работе или доступ уже выдан.
OCCUPYING_STATUSES = frozenset(
    {
        RequestStatus.PENDING_OWNER_APPROVAL,
        RequestStatus.PENDING_SECURITY_APPROVAL,
        RequestStatus.PROVISIONING,
        RequestStatus.ACTIVE,
    }
)
