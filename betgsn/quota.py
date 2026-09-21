"""BETGSN :: quota — Controle de quota de API com priorização.

Evita exceder limites de rate-limit e quota diária.
Prioriza chamadas por proximidade do jogo e importância.
"""
from __future__ import annotations
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class QuotaState:
    provider: str
    daily_limit: int = 0        # 0 = unlimited
    minute_limit: int = 0       # 0 = unlimited
    requests_today: int = 0
    requests_this_minute: int = 0
    reset_time: float = 0.0     # epoch when daily counter resets
    minute_start: float = 0.0
    last_request: float = 0.0
    errors_today: int = 0
    
    @property
    def daily_remaining(self) -> int:
        if self.daily_limit <= 0:
            return 999999
        return max(0, self.daily_limit - self.requests_today)
    
    @property
    def minute_remaining(self) -> int:
        if self.minute_limit <= 0:
            return 999999
        now = time.time()
        if now - self.minute_start >= 60:
            return self.minute_limit
        return max(0, self.minute_limit - self.requests_this_minute)
    
    @property
    def can_request(self) -> bool:
        return self.daily_remaining > 0 and self.minute_remaining > 0

class QuotaManager:
    """Thread-safe quota manager for multiple providers."""
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotas: dict[str, QuotaState] = {}
    
    def register(self, provider: str, daily_limit: int = 0, 
                 minute_limit: int = 0) -> None:
        with self._lock:
            self._quotas[provider] = QuotaState(
                provider=provider,
                daily_limit=daily_limit,
                minute_limit=minute_limit,
                reset_time=time.time() + 86400,
                minute_start=time.time(),
            )
    
    def can_request(self, provider: str) -> bool:
        with self._lock:
            q = self._quotas.get(provider)
            if q is None:
                return True
            self._maybe_reset(q)
            return q.can_request
    
    def record_request(self, provider: str) -> None:
        with self._lock:
            q = self._quotas.get(provider)
            if q is None:
                return
            self._maybe_reset(q)
            q.requests_today += 1
            q.requests_this_minute += 1
            q.last_request = time.time()
    
    def record_error(self, provider: str) -> None:
        with self._lock:
            q = self._quotas.get(provider)
            if q:
                q.errors_today += 1
    
    def status(self, provider: str) -> Optional[dict]:
        with self._lock:
            q = self._quotas.get(provider)
            if q is None:
                return None
            self._maybe_reset(q)
            return {
                "provider": q.provider,
                "daily_limit": q.daily_limit,
                "daily_remaining": q.daily_remaining,
                "minute_limit": q.minute_limit,
                "minute_remaining": q.minute_remaining,
                "requests_today": q.requests_today,
                "errors_today": q.errors_today,
                "can_request": q.can_request,
            }
    
    def all_status(self) -> list[dict]:
        with self._lock:
            return [self.status(p) for p in self._quotas]
    
    def _maybe_reset(self, q: QuotaState) -> None:
        now = time.time()
        if now >= q.reset_time:
            q.requests_today = 0
            q.errors_today = 0
            q.reset_time = now + 86400
        if now - q.minute_start >= 60:
            q.requests_this_minute = 0
            q.minute_start = now

# Priority levels for request scheduling
PRIORITY_CRITICAL = 1   # upcoming match odds (< 60min)
PRIORITY_HIGH = 2       # lineups, injuries (< 6h)
PRIORITY_MEDIUM = 3     # stats, H2H (< 24h)
PRIORITY_LOW = 4        # historical data
PRIORITY_BACKGROUND = 5 # validation, research
