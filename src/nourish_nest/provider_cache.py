from dataclasses import dataclass
from time import monotonic
from typing import Generic, Protocol, TypeVar

ValueT = TypeVar("ValueT")


class Cache(Protocol[ValueT]):
    def get(self, key: str) -> ValueT | None: ...
    def set(self, key: str, value: ValueT, ttl_seconds: int) -> None: ...


@dataclass
class _Entry(Generic[ValueT]):
    value: ValueT
    expires_at: float


class InMemoryTTLCache(Generic[ValueT]):
    def __init__(self):
        self._entries: dict[str, _Entry[ValueT]] = {}

    def get(self, key: str) -> ValueT | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= monotonic():
            self._entries.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: ValueT, ttl_seconds: int) -> None:
        self._entries[key] = _Entry(value, monotonic() + max(0, ttl_seconds))