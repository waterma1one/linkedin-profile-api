from app.ratelimit import InboundLimiter, TokenBucket


async def test_burst_requests_do_not_sleep():
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    bucket = TokenBucket(rate_seconds=30, burst=3, sleep=fake_sleep, clock=lambda: 0.0)
    for _ in range(3):
        await bucket.acquire()
    assert slept == []


async def test_fourth_request_sleeps():
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    bucket = TokenBucket(rate_seconds=30, burst=3, sleep=fake_sleep, clock=lambda: 0.0)
    for _ in range(4):
        await bucket.acquire()
    assert len(slept) == 1
    assert slept[0] > 0


def test_inbound_limiter_allows_up_to_the_quota():
    limiter = InboundLimiter(per_minute=3, clock=lambda: 0.0)
    assert [limiter.check("k") for _ in range(3)] == [None, None, None]


def test_inbound_limiter_blocks_beyond_the_quota():
    limiter = InboundLimiter(per_minute=2, clock=lambda: 0.0)
    limiter.check("k")
    limiter.check("k")
    assert limiter.check("k") == 60


def test_inbound_limiter_tracks_keys_independently():
    limiter = InboundLimiter(per_minute=1, clock=lambda: 0.0)
    assert limiter.check("a") is None
    assert limiter.check("b") is None


def test_inbound_limiter_window_expires():
    now = [0.0]
    limiter = InboundLimiter(per_minute=1, clock=lambda: now[0])
    limiter.check("k")
    now[0] = 61.0
    assert limiter.check("k") is None
