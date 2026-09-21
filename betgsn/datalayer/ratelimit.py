"""BETGSN :: datalayer.ratelimit — limite de ritmo por fonte.

Complementa `quota.QuotaManager` (que conta a cota do dia/minuto) com o
espaçamento mínimo entre chamadas. O `QuotaManager` diz "ainda posso
chamar?"; o `RateLimiter` diz "posso chamar AGORA?".

Tempo e `sleep` são injetáveis para os testes nunca dormirem de verdade.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

__all__ = ["RateLimiter"]


@dataclass
class RateLimiter:
    """Espaçamento mínimo e teto por minuto para uma fonte."""

    min_interval_seconds: float = 0.0
    max_per_minute: int = 0
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    _last_call: float = field(default=0.0, init=False)
    _window_start: float = field(default=0.0, init=False)
    _window_count: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def _roll_window(self, now: float) -> None:
        if self._window_start == 0.0 or now - self._window_start >= 60.0:
            self._window_start = now
            self._window_count = 0

    def delay_for(self, now: float | None = None) -> float:
        """Segundos a esperar antes da próxima chamada (0 = pode já)."""
        moment = self.clock() if now is None else now
        delay = 0.0
        if self.min_interval_seconds > 0 and self._last_call:
            elapsed = moment - self._last_call
            if elapsed < self.min_interval_seconds:
                delay = self.min_interval_seconds - elapsed
        if self.max_per_minute > 0:
            self._roll_window(moment)
            if self._window_count >= self.max_per_minute:
                delay = max(delay, 60.0 - (moment - self._window_start))
        return max(0.0, delay)

    def can_request(self, now: float | None = None) -> bool:
        return self.delay_for(now) <= 0.0

    def acquire(self, now: float | None = None) -> float:
        """Bloqueia (via `sleeper`) até ser permitido e registra a chamada.

        Devolve quantos segundos foram efetivamente esperados.
        """
        with self._lock:
            moment = self.clock() if now is None else now
            delay = self.delay_for(moment)
            if delay > 0:
                self.sleeper(delay)
                moment = moment + delay
            self._last_call = moment
            self._roll_window(moment)
            self._window_count += 1
            return delay

    def record(self, now: float | None = None) -> None:
        """Registra uma chamada que já aconteceu (sem esperar)."""
        with self._lock:
            moment = self.clock() if now is None else now
            self._last_call = moment
            self._roll_window(moment)
            self._window_count += 1
