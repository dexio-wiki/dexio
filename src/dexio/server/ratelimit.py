"""In-memory sliding-window rate limits for the sign-in, signup and password forms.

One process serves everything, so a dict is enough; the limits reset on restart,
which is acceptable for slowing down password guessing and signup floods.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class Limiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record one attempt for `key`; False once the window is full."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:            # bound memory under a flood
                for k in [k for k, v in self._hits.items() if not v][:25_000]:
                    self._hits.pop(k, None)
            return True


def client_ip(request) -> str:
    """The caller's address. Caddy is the only thing in front of the app and
    appends the address it saw to X-Forwarded-For, so the last entry is the one
    to trust; anything earlier in the header came from the client."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
