class RateLimiterError(Exception):
    """Base exception for rate limiter."""


class InvalidTokenRequestError(RateLimiterError):
    """Raised when tokens requested are <= 0."""
