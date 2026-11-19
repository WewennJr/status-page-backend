"""Simple in-memory rate limiting (per IP) for sensitive endpoints."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, max_attempts: int = 10, window_seconds: int = 60) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Raise 429 if there are too many recent attempts, otherwise record this one."""
        now = time.monotonic()
        if len(self._hits) > 5000:
            for stale in [k for k, v in self._hits.items() if not v]:
                del self._hits[stale]
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_attempts:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts, please try again later",
            )
        hits.append(now)


def client_key(request: Request) -> str:
    # Behind nginx, uvicorn (proxy_headers) provides the real client IP.
    return request.client.host if request.client else "unknown"


login_limiter = RateLimiter(max_attempts=10, window_seconds=60)
setup_limiter = RateLimiter(max_attempts=20, window_seconds=60)
