"""BETGSN :: realtime — terminal de mercado de odds em tempo real.

O que este pacote e
-------------------
Um motor que observa o mercado continuamente e alimenta o MARKET
TERMINAL: captura periodica multi-provider -> normalizacao ->
event matching -> snapshot store append-only -> movimento ->
market view (best/mediana/fair) -> sinais EXPLICAVEIS -> broadcast
de eventos (SSE) para o frontend.

O que este pacote NAO e
----------------------
- NAO e um bot de apostas: nenhum sinal vira aposta automatica.
- NAO substitui o pipeline batch (backtest, CLV sweep, captura diaria):
  ele REUSA o mesmo `LiveOddsCapture` e o mesmo `OddsSnapshotStore`,
  escrevendo no MESMO banco append-only e deduplicado.
- NAO fabrica dado: quote sem timestamp real nao entra; evento sem
  fixture fica UNMATCHED; sinal sem evidencia fica INSUFFICIENT_DATA.
"""

from .config import RealtimeConfig
from .engine import RealtimeOddsEngine
from .events import EventBus, RealtimeEvent
from .freshness import FreshnessState, FreshnessThresholds

__all__ = [
    "EventBus",
    "FreshnessState",
    "FreshnessThresholds",
    "RealtimeConfig",
    "RealtimeEvent",
    "RealtimeOddsEngine",
]
