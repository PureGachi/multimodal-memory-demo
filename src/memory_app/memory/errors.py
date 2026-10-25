class MemoryErrorBase(Exception):
    pass


class Conflict(MemoryErrorBase):
    pass


class InvalidContent(MemoryErrorBase):
    pass


class ModelUnavailable(MemoryErrorBase):
    def __init__(self, message, *, code="model_unavailable", component="model", retryable=False,
                 http_status=None, provider_code=None, diagnostic=None):
        super().__init__(message)
        self.code = code
        self.component = component
        self.retryable = retryable
        self.http_status = http_status
        self.provider_code = provider_code
        self.diagnostic = diagnostic


class CheckpointError(MemoryErrorBase):
    pass


class StorageUnavailable(MemoryErrorBase):
    pass
