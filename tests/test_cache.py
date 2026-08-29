from app.cache import TTLCache


def test_returns_stored_value():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", {"x": 1})
    assert cache.get("a") == {"x": 1}


def test_missing_key_returns_none():
    assert TTLCache(ttl_seconds=60).get("nope") is None


def test_expired_entry_returns_none():
    # First value is consumed by set(), the second by get().
    clock = iter([0.0, 100.0])
    cache = TTLCache(ttl_seconds=60, clock=lambda: next(clock))
    cache.set("a", 1)
    assert cache.get("a") is None


def test_clear_removes_everything():
    cache = TTLCache(ttl_seconds=60)
    cache.set("a", 1)
    cache.clear()
    assert cache.get("a") is None
