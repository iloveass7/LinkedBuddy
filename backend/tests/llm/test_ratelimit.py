import asyncio

from linkedbuddy.llm.ratelimit import TokenBucket


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_bucket_limits_rate() -> None:
    t = FakeTime()
    bucket = TokenBucket(rpm=60, clock=t.clock, sleep=t.sleep)
    for _ in range(60):  # drain the full bucket: no waiting
        await bucket.acquire()
    assert t.slept == []

    for _ in range(3):
        await bucket.acquire()
    assert t.slept == [1.0, 1.0, 1.0]  # 60 rpm = 1 token/s, so each waits ~1 s


async def test_bucket_refills_over_time_up_to_capacity() -> None:
    t = FakeTime()
    bucket = TokenBucket(rpm=2, clock=t.clock, sleep=t.sleep)
    await bucket.acquire()
    await bucket.acquire()
    t.now += 3600  # refill is capped at capacity, not accumulated
    await bucket.acquire()
    await bucket.acquire()
    assert t.slept == []
    await bucket.acquire()
    assert t.slept == [30.0]  # 2 rpm = 1 token per 30 s


async def test_bucket_concurrent_acquires_are_serialised() -> None:
    t = FakeTime()
    bucket = TokenBucket(rpm=60, clock=t.clock, sleep=t.sleep)
    for _ in range(60):
        await bucket.acquire()
    await asyncio.gather(*(bucket.acquire() for _ in range(3)))
    assert t.now - 1000.0 == 3.0
