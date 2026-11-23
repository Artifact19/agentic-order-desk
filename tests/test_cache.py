from app.cache import TTLCache


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_hit_miss_and_expiry():
    clock = FakeClock()
    cache = TTLCache("t", max_entries=10, ttl_seconds=5, clock=clock)
    calls = []
    value, hit = cache.get_or_load("k", lambda: calls.append(1) or "v")
    assert (value, hit) == ("v", False)
    value, hit = cache.get_or_load("k", lambda: calls.append(1) or "v")
    assert (value, hit) == ("v", True)
    clock.now = 6
    _, hit = cache.get_or_load("k", lambda: calls.append(1) or "v")
    assert hit is False
    assert len(calls) == 2
    assert cache.stats.expirations == 1


def test_lru_eviction_keeps_recently_used():
    cache = TTLCache("t", max_entries=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    cache.set("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.stats.evictions == 1


def test_tag_invalidation():
    cache = TTLCache("t", max_entries=10, ttl_seconds=60)
    cache.set("x", 1, tags=("catalog",))
    cache.set("y", 2, tags=("other",))
    assert cache.invalidate_tag("catalog") == 1
    assert cache.get("x") is None
    assert cache.get("y") == 2
