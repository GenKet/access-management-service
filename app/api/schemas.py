import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# За пределами BIGINT asyncpg падает с OverflowError вместо понятной ошибки.
MAX_BIGINT = 2**63 - 1

# Управляющие символы Postgres отвергает на уровне протокола, и это не
# IntegrityError — отсечь их можно только на входе.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_id: int
    criticality: str


class AccessRequestCreate(BaseModel):
    resource_id: int = Field(gt=0, le=MAX_BIGINT)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("обоснование не может быть пустым")
        if _CONTROL_CHARS_RE.search(stripped):
            raise ValueError("обоснование не может содержать управляющие символы")
        return stripped


class DecisionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def comment_is_clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if _CONTROL_CHARS_RE.search(stripped):
            raise ValueError("комментарий не может содержать управляющие символы")
        return stripped


class ResourceBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    criticality: str


class ProvisioningStateOut(BaseModel):
    attempts: int = 0
    last_error: str | None = None


class AccessRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    resource: ResourceBrief
    reason: str
    status: str
    owner_decided_by: int | None = None
    owner_decided_at: datetime | None = None
    security_decided_by: int | None = None
    security_decided_at: datetime | None = None
    decision_comment: str | None = None
    provisioning_error: str | None = None
    provisioning: ProvisioningStateOut = ProvisioningStateOut()
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, request) -> "AccessRequestOut":
        job = request.job
        return cls(
            id=request.id,
            user_id=request.user_id,
            resource=ResourceBrief.model_validate(request.resource),
            reason=request.reason,
            status=request.status,
            owner_decided_by=request.owner_decided_by,
            owner_decided_at=request.owner_decided_at,
            security_decided_by=request.security_decided_by,
            security_decided_at=request.security_decided_at,
            decision_comment=request.decision_comment,
            provisioning_error=request.provisioning_error,
            provisioning=ProvisioningStateOut(
                attempts=job.attempts if job else 0,
                last_error=job.last_error if job else None,
            ),
            created_at=request.created_at,
            updated_at=request.updated_at,
        )
