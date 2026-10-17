"""Expected application failures shared by HTTP, OCPP and charging modules."""


class ServiceError(Exception):
    pass


class ValidationError(ServiceError):
    pass


class NotFound(ServiceError):
    pass


class AlreadyExists(ServiceError):
    pass


class StaleConnection(ServiceError):
    pass


class RequestConflict(ServiceError):
    pass


class DependencyUnavailable(ServiceError):
    pass


class InvalidTransaction(ServiceError):
    """Unknown or mismatched reference; never disclose another station's record."""
