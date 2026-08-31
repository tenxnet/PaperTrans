from __future__ import annotations


class WorkerError(Exception):
    """A sanitized, classifiable worker failure."""

    def __init__(self, code: str, message: str, exit_code: int):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.exit_code = exit_code


class ContractError(WorkerError):
    pass


class PolicyRefusal(WorkerError):
    pass


class PdfFailure(WorkerError):
    pass


class ResourceLimit(WorkerError):
    pass


class ProviderFailure(WorkerError):
    pass
