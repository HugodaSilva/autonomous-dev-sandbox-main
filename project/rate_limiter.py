import threading
import time
from typing import Final

from .exceptions import InvalidTokenRequestError


class TokenBucket:
    """
    A thread-safe Token Bucket implementation.
    Uses a monotonic clock for refill calculations.
    """

    def __init__(self, capacity: float, refill_rate: float):
        """
        :param capacity: Maximum tokens the bucket can hold.
        :param refill_rate: Tokens added per second.
        """
        self.capacity: Final[float] = float(capacity)
        self.refill_rate: Final[float] = float(refill_rate)

        self._tokens: float = float(capacity)
        self._last_refill_timestamp: float = time.monotonic()
        self._lock: threading.Lock = threading.Lock()

    def _refill(self) -> None:
        """Calculates and adds tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill_timestamp
        tokens_to_add = elapsed * self.refill_rate

        self._tokens = min(self.capacity, self._tokens + tokens_to_add)
        self._last_refill_timestamp = now

    def try_consume(self, tokens: float) -> bool:
        """
        Non-blocking attempt to consume tokens.

        :param tokens: Number of tokens to consume.
        :return: True if consumed, False otherwise.
        :raises InvalidTokenRequestError: If tokens <= 0.
        """
        if tokens <= 0:
            raise InvalidTokenRequestError("Requested tokens must be positive.")

        with self._lock:
            self._refill()

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True

            return False

    def get_remaining_tokens(self) -> float:
        """
        Returns the current number of tokens in the bucket.
        """
        with self._lock:
            self._refill()
            return self._tokens
