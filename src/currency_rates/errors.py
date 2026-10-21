class AppError(Exception):
    pass


class InfrastructureError(AppError):
    pass


class UpstreamError(InfrastructureError):
    def __init__(self, url: str, attempts: int) -> None:
        super().__init__(f"Request to {url} failed after {attempts} attempt(s)")
        self.url = url
        self.attempts = attempts
