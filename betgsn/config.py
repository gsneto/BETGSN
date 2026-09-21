"""BETGSN :: config — Configuração centralizada.

Evita hardcode espalhado. Carrega de arquivo JSON quando disponível,
senão usa defaults sensatos. Nunca expõe API keys.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ModelConfig:
    """Configuração do modelo estatístico."""
    home_advantage: float = 1.18
    league_avg_goals: float = 2.55
    rho_dixon_coles: float = -0.05
    max_goals_grid: int = 8
    attack_blend: float = 0.5  # blend entre gols e xG
    decay: float = 0.0         # exponential decay por dia


@dataclass
class KellyConfig:
    """Configuração do staking."""
    fraction: float = 0.25
    cap: float = 0.01
    max_exposure: float = 0.25
    max_match_exposure: float = 0.05
    max_league_exposure: float = 0.10
    max_daily_bets: int = 50


@dataclass
class BacktestConfig:
    """Configuração do backtest."""
    min_history: int = 300
    refit_every_days: int = 1
    train_window_days: int = 1095
    min_ev: float = 0.02
    min_books: int = 3
    max_spread: float = 0.25


@dataclass
class CacheConfig:
    """TTLs de cache em segundos."""
    historical: int = 0        # infinite
    fixtures: int = 21600      # 6h
    h2h: int = 86400           # 24h
    team_stats: int = 43200    # 12h
    injuries: int = 21600      # 6h
    lineups: int = 1800        # 30min
    odds: int = 900            # 15min
    live: int = 120            # 2min


@dataclass
class QuotaConfig:
    """Limites de quota por provider."""
    api_football_daily: int = 100
    api_football_minute: int = 30
    odds_api_daily: int = 500
    odds_api_minute: int = 30
    football_data_org_daily: int = 10
    football_data_org_minute: int = 10


@dataclass
class ProviderConfig:
    """Configuração de providers."""
    enabled: list[str] = field(default_factory=lambda: [
        "football_data_uk", "api_football", "odds_api", "football_data_org"
    ])
    priority: list[str] = field(default_factory=lambda: [
        "football_data_uk", "api_football", "odds_api"
    ])


@dataclass
class SignalConfig:
    """Configuração de sinais."""
    ev_forte: float = 0.08
    ev_media: float = 0.045
    ev_fraca: float = 0.02
    min_books: int = 3
    max_spread: float = 0.25


@dataclass
class PortfolioConfig:
    """Configuração do motor de portfólio."""
    max_parlay_legs: int = 4
    min_parlay_ev: float = 0.0
    max_same_match_legs: int = 2
    max_parlays: int = 20
    parlay_kelly_fraction: float = 0.10
    parlay_stake_cap: float = 0.01


@dataclass
class BetgsnConfig:
    """Configuração raiz do sistema."""
    model: ModelConfig = field(default_factory=ModelConfig)
    kelly: KellyConfig = field(default_factory=KellyConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    providers: ProviderConfig = field(default_factory=ProviderConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    
    # Runtime
    seed: int = 6767
    debug: bool = False
    data_dir: str = ""
    
    def __post_init__(self):
        if not self.data_dir:
            self.data_dir = str(_ROOT / "output")


def _deep_update(target: dict, source: dict) -> dict:
    """Merge recursivo de dicionários."""
    for key, value in source.items():
        if isinstance(value, dict) and key in target and isinstance(target[key], dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def load_config(path: Optional[str | Path] = None) -> BetgsnConfig:
    """Carrega configuração de arquivo JSON ou usa defaults.
    
    Procura em ordem:
    1. path explícito
    2. BETGSN_CONFIG env var
    3. config/betgsn.json
    4. defaults
    """
    config = BetgsnConfig()
    
    candidates = []
    if path:
        candidates.append(Path(path))
    env_path = os.environ.get("BETGSN_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(_ROOT / "config" / "betgsn.json")
    
    for candidate in candidates:
        if candidate.exists():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                # Apply overrides to defaults
                if "model" in raw:
                    for k, v in raw["model"].items():
                        if hasattr(config.model, k):
                            setattr(config.model, k, v)
                if "kelly" in raw:
                    for k, v in raw["kelly"].items():
                        if hasattr(config.kelly, k):
                            setattr(config.kelly, k, v)
                if "backtest" in raw:
                    for k, v in raw["backtest"].items():
                        if hasattr(config.backtest, k):
                            setattr(config.backtest, k, v)
                if "cache" in raw:
                    for k, v in raw["cache"].items():
                        if hasattr(config.cache, k):
                            setattr(config.cache, k, v)
                if "quota" in raw:
                    for k, v in raw["quota"].items():
                        if hasattr(config.quota, k):
                            setattr(config.quota, k, v)
                if "signals" in raw:
                    for k, v in raw["signals"].items():
                        if hasattr(config.signals, k):
                            setattr(config.signals, k, v)
                if "portfolio" in raw:
                    for k, v in raw["portfolio"].items():
                        if hasattr(config.portfolio, k):
                            setattr(config.portfolio, k, v)
                if "seed" in raw:
                    config.seed = raw["seed"]
                if "debug" in raw:
                    config.debug = raw["debug"]
                if "data_dir" in raw:
                    config.data_dir = raw["data_dir"]
            except (json.JSONDecodeError, TypeError, KeyError):
                pass  # fallback to defaults
            break
    
    return config


# Singleton — carregado uma vez
_config: Optional[BetgsnConfig] = None

def get_config() -> BetgsnConfig:
    """Retorna a configuração global (cached)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config

def reset_config() -> None:
    """Força recarga da configuração."""
    global _config
    _config = None
