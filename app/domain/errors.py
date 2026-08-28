class DomainError(Exception):
    pass


class RequestNotFound(DomainError):
    pass


class ResourceNotFound(DomainError):
    pass


class DuplicateRequest(DomainError):
    pass


class DuplicateProvisioningJob(DomainError):
    pass


class InvalidTransition(DomainError):
    def __init__(self, status: str, action: str) -> None:
        super().__init__(f"Запрос находится в статусе {status} и не может быть {action}")


class NotAllowed(DomainError):
    pass


class ProvisioningError(DomainError):
    pass
