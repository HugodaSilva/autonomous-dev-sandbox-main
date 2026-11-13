import threading
import time

import pytest

from project.exceptions import InvalidTokenRequestError
from project.rate_limiter import TokenBucket


def test_burst_capacity():
    capacity = 10.0
    limiter = TokenBucket(capacity, 1.0)
    assert limiter.try_consume(capacity) is True
    assert limiter.try_consume(1.0) is False


def test_refill_overflow():
    limiter = TokenBucket(10.0, 100.0)
    time.sleep(0.2)
    assert limiter.get_remaining_tokens() <= 10.0


def test_invalid_request():
    limiter = TokenBucket(10.0, 1.0)
    with pytest.raises(InvalidTokenRequestError):
        limiter.try_consume(0)
    with pytest.raises(InvalidTokenRequestError):
        limiter.try_consume(-5.0)


def test_partial_refill_precision():
    # 0.5 tokens per second
    limiter = TokenBucket(10.0, 0.5)
    # Consume all
    limiter.try_consume(10.0)
    # Wait 1.5 seconds -> should have 0.75 tokens
    time.sleep(1.5)
    remaining = limiter.get_remaining_tokens()
    assert 0.74 <= remaining <= 0.76


def test_high_concurrency():
    capacity = 100.0
    limiter = TokenBucket(capacity, 1000.0)

    def worker():
        for _ in range(100):
            limiter.try_consume(1.0)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert limiter.get_remaining_tokens() >= 0.0
