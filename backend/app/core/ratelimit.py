"""
In-process token-bucket rate limiting (spec 3.7) — no new dependency.

Limits are per uvicorn worker (with N workers the effective limit is up to N×);
in production the enroll-by-IP limit should also be enforced in Nginx
(`limit_req`). Disabled with FIELD_RATE_LIMITS_ENABLED=false.
"""
import math
import threading
import time
from typing import Optional

from app.core.config import settings

_lock = threading.Lock()
_buckets: dict = {}          # key -> [tokens, last_ts]
_blocks: dict = {}           # key -> blocked_until (monotonic)
_failures: dict = {}         # key -> [timestamps]


def reset() -> None:
    with _lock:
        _buckets.clear()
        _blocks.clear()
        _failures.clear()


def take(key: str, capacity: float, refill_per_s: float, now: Optional[float] = None) -> Optional[int]:
    """Consume one token. Returns None if allowed, else Retry-After seconds."""
    if not settings.FIELD_RATE_LIMITS_ENABLED:
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        until = _blocks.get(key)
        if until is not None:
            if now < until:
                return max(1, math.ceil(until - now))
            _blocks.pop(key, None)
        tokens, last = _buckets.get(key, [capacity, now])
        tokens = min(capacity, tokens + (now - last) * refill_per_s)
        if tokens >= 1:
            _buckets[key] = [tokens - 1, now]
            return None
        _buckets[key] = [tokens, now]
        return max(1, math.ceil((1 - tokens) / refill_per_s))


def record_failure(key: str, max_failures: int, window_s: float, block_s: float,
                   now: Optional[float] = None) -> None:
    """After `max_failures` failures within `window_s`, block `key` for `block_s`."""
    if not settings.FIELD_RATE_LIMITS_ENABLED:
        return
    now = time.monotonic() if now is None else now
    with _lock:
        lst = [t for t in _failures.get(key, []) if now - t <= window_s]
        lst.append(now)
        _failures[key] = lst
        if len(lst) >= max_failures:
            _blocks[key] = now + block_s
            _failures[key] = []


# Limits from the spec (3.3 / 3.4)
BATCH = (10, 30 / 60)            # 30/min per device, burst 10
HEARTBEAT = (6, 6 / 60)          # 6/min per device
ENROLL = (10, 10 / 60)           # 10/min per IP; 5 failures in 10 min → blocked 15 min
COMMANDS = (10, 10 / 3600)       # 10/hour per device
