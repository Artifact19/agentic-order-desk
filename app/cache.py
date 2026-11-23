import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    def as_dict(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


@dataclass
class _Entry:
    value: object
    expires_at: float
    tags: frozenset = field(default_factory=frozenset)


class TTLCache:
    def __init__(self, name: str, max_entries: int, ttl_seconds: float, clock=time.monotonic):
        self.name = name
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict = OrderedDict()
        self._lock = threading.RLock()
        self.stats = CacheStats()

    def get(self, key, default=None):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.stats.misses += 1
                return default
            if entry.expires_at <= self._clock():
                del self._entries[key]
                self.stats.expirations += 1
                self.stats.misses += 1
                return default
            self._entries.move_to_end(key)
            self.stats.hits += 1
            return entry.value

    def set(self, key, value, tags=()):
        with self._lock:
            self._entries[key] = _Entry(value, self._clock() + self.ttl_seconds, frozenset(tags))
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self.stats.evictions += 1

    def get_or_load(self, key, loader, tags=()):
        sentinel = object()
        value = self.get(key, sentinel)
        if value is not sentinel:
            return value, True
        value = loader()
        self.set(key, value, tags)
        return value, False

    def invalidate(self, key) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None

    def invalidate_tag(self, tag) -> int:
        with self._lock:
            doomed = [k for k, e in self._entries.items() if tag in e.tags]
            for key in doomed:
                del self._entries[key]
            return len(doomed)

    def clear(self) -> int:
        with self._lock:
            size = len(self._entries)
            self._entries.clear()
            return size

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def snapshot(self) -> dict:
        return {"name": self.name, "size": len(self), "capacity": self.max_entries,
                "ttl_seconds": self.ttl_seconds, **self.stats.as_dict()}


class CacheRegistry:
    def __init__(self, ttl: dict, capacity: dict):
        self.caches = {name: TTLCache(name, capacity[name], ttl[name]) for name in ttl}

    def __getitem__(self, name) -> TTLCache:
        return self.caches[name]

    def snapshot(self) -> list:
        return [cache.snapshot() for cache in self.caches.values()]

    def clear_all(self) -> int:
        return sum(cache.clear() for cache in self.caches.values())
