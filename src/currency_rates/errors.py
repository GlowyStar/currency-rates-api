from collections.abc import Sequence


class AppError(Exception):
    pass


class DomainError(AppError):
    """A request breaks a rule of the service; the message is safe to show to the caller."""


class UnknownCurrencyError(DomainError):
    def __init__(self, codes: Sequence[str]) -> None:
        super().__init__(f"Unknown currency code(s): {', '.join(codes)}")
        self.codes = list(codes)


class InvalidRequestError(DomainError):
    pass


class RatesNotFoundError(DomainError):
    def __init__(self, base: str) -> None:
        super().__init__(f"No rates found for base {base}")
        self.base = base


class InvalidApiKeyError(DomainError):
    def __init__(self) -> None:
        super().__init__("Missing or invalid API key")


class InfrastructureError(AppError):
    pass


class UpstreamError(InfrastructureError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"Frankfurter {path}: {reason}")
        self.path = path
