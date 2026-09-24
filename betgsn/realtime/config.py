"""BETGSN :: realtime.config — configuracao do engine em tempo real.

Tudo o que afeta cadencia/custo e configuravel por ambiente — nada de
intervalo fixo escondido no codigo. As variaveis (todas opcionais):

  BETGSN_REALTIME            "0" desliga o engine (default: ligado
                             quando ha provider configurado)
  BETGSN_REALTIME_INTERVAL   segundos entre ticks (default 300)
  BETGSN_REALTIME_PROVIDERS  lista separada por virgula (default: os
                             tres providers operacionais)
  BETGSN_REALTIME_SPORTS     lista separada por virgula (default:
                             derivada das divisoes dos fixtures)
  BETGSN_REALTIME_MARKETS    mercados (default: h2h,totals,btts)
  BETGSN_REALTIME_REGIONS    regioes (default: eu)
  BETGSN_REALTIME_FRESH_SECONDS    limite FRESH (default 300)
  BETGSN_REALTIME_RECENT_SECONDS   limite RECENT (default 900)
  BETGSN_REALTIME_STALE_SECONDS     limite STALE (default 3600)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..envconfig import resolve_env_file
from ..providers import load_env_file

#: Providers da operacao ATUAL. Odds-API.io e OpticOdds existem no
#: registry mas NAO fazem parte da operacao — o engine nao os consulta.
OPERATIONAL_PROVIDERS: tuple[str, ...] = ("The Odds API", "ParlayAPI", "OddsPapi")

DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_MARKETS = "h2h,totals,btts"
DEFAULT_REGIONS = "eu"


def _env(name: str, default: str = "") -> str:
    import os

    return (os.environ.get(name) or default).strip()


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return default
    values = tuple(v.strip() for v in raw.split(",") if v.strip())
    return values or default


@dataclass(frozen=True)
class RealtimeConfig:
    """Parametros operacionais do loop de captura em tempo real."""

    enabled: bool = True
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    provider_names: tuple[str, ...] = OPERATIONAL_PROVIDERS
    sport_keys: tuple[str, ...] = ()
    markets: str = DEFAULT_MARKETS
    regions: str = DEFAULT_REGIONS
    fresh_seconds: float = 300.0
    recent_seconds: float = 900.0
    stale_seconds: float = 3600.0
    #: janela (s) em que um movimento conta como RAPID
    rapid_move_seconds: float = 180.0
    #: casas minimas para CONSENSUS_MOVE
    consensus_min_books: int = 3
    #: mudanca relativa minima p/ movimento relevante
    move_threshold_pct: float = 0.01
    #: idade maxima de um sinal antes de EXPIRED (s)
    signal_ttl_seconds: float = 1800.0

    def validate(self) -> None:
        if self.interval_seconds < 30.0:
            raise ValueError(
                "BETGSN_REALTIME_INTERVAL abaixo de 30s: a operacao nao "
                "pode sair gastando credits sem controle"
            )
        if not self.provider_names:
            raise ValueError("engine realtime exige ao menos um provider")

    @classmethod
    def from_env(cls) -> "RealtimeConfig":
        load_env_file(resolve_env_file())
        config = cls(
            enabled=_env("BETGSN_REALTIME", "1") != "0",
            interval_seconds=float(_env("BETGSN_REALTIME_INTERVAL", str(DEFAULT_INTERVAL_SECONDS))),
            provider_names=_env_list("BETGSN_REALTIME_PROVIDERS", OPERATIONAL_PROVIDERS),
            sport_keys=_env_list("BETGSN_REALTIME_SPORTS", ()),
            markets=_env("BETGSN_REALTIME_MARKETS", DEFAULT_MARKETS),
            regions=_env("BETGSN_REALTIME_REGIONS", DEFAULT_REGIONS),
            fresh_seconds=float(_env("BETGSN_REALTIME_FRESH_SECONDS", "300")),
            recent_seconds=float(_env("BETGSN_REALTIME_RECENT_SECONDS", "900")),
            stale_seconds=float(_env("BETGSN_REALTIME_STALE_SECONDS", "3600")),
        )
        config.validate()
        return config
