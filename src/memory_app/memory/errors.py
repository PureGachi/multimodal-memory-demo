class MemoryErrorBase(Exception):
    pass


class Conflict(MemoryErrorBase):
    pass


class InvalidContent(MemoryErrorBase):
    pass


class ModelUnavailable(MemoryErrorBase):
    pass


class StorageUnavailable(MemoryErrorBase):
    pass
