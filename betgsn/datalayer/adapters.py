"""BETGSN :: datalayer.adapters — fontes reais plugadas na camada.

Três fontes:

  - **football_data_uk** (CSV público, sem chave): resultados, estatísticas
    (cantos/cartões/chutes), odds reais de fechamento/abertura e jogos
    FUTUROS. Não fornece xG. Roda offline do cache local.
  - **api_football** (chave): temporadas, estatísticas e xG real quando a
    liga publica `expected_goals`.
  - **football_data_org** (chave): jogos agendados e resultados de 12
    competições. NÃO fornece odds, xG, cantos ou cartões.

Contrato de `records`: listas de dicionários JSON-nativos (as fontes de API
devolvem o payload cru) ou dataclasses já normalizadas no caso do
football-data.co.uk. Fontes que devolvem dataclasses são registradas com
`supports_cache=False`, porque o CSV já tem cache próprio em disco.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..backtest_sources import FINISHED_STATUSES
from ..football_data_uk import FootballDataClient
from .source import BaseSource, Capabilities, RawFetch

__all__ = [
    "FootballDataUkSource",
    "ApiFootballSource",
    "FootballDataOrgSource",
]


def _mtime_stamp(paths: list[Path]) -> str:
    """Carimbo UTC do arquivo mais recente. Vazio se nenhum existir."""
    newest = 0.0
    for path in paths:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    if newest <= 0:
        return ""
    return datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FootballDataUkSource(BaseSource):
    """Adapter do football-data.co.uk (CSV público, sem chave)."""

    name = "football_data_uk"
    capabilities = frozenset(
        {
            Capabilities.FIXTURES,
            Capabilities.RESULTS,
            Capabilities.STATS,
            Capabilities.ODDS,
        }
    )

    def __init__(self, client: FootballDataClient | None = None, root: Path | None = None) -> None:
        self.client = client if client is not None else FootballDataClient(root=root)

    def available(self) -> bool:
        for folder in (self.client.main_dir, self.client.extra_dir, self.client._fixtures_dir):
            try:
                if next(folder.glob("*.csv"), None) is not None:
                    return True
            except OSError:
                continue
        return False

    def fetch(self, kind: str, **params: Any) -> RawFetch:
        if kind == Capabilities.FIXTURES:
            fixtures = self.client.load_fixtures()
            stamp = _mtime_stamp([
                self.client._fixtures_dir / "main.csv",
                self.client._fixtures_dir / "extra.csv",
            ])
            coverage = tuple(sorted({f.league for f in fixtures}))
            return RawFetch(
                records=fixtures,
                source=self.name,
                source_timestamp=stamp,
                coverage=coverage,
                note=f"{len(fixtures)} jogos futuros do CSV",
            )

        matches = self.client.load_matches(divisions=params.get("divisions"))
        stamp = _mtime_stamp(sorted(self.client.main_dir.glob("*.csv")))
        if kind == Capabilities.RESULTS:
            records = [m for m in matches if m.home_goals is not None]
        elif kind == Capabilities.STATS:
            records = [
                m for m in matches
                if m.home_corners is not None or m.home_shots is not None
            ]
        elif kind == Capabilities.ODDS:
            records = [m for m in matches if m.odds_closing]
        else:
            records = matches
        coverage = tuple(sorted({m.league for m in records}))
        return RawFetch(
            records=records,
            source=self.name,
            source_timestamp=stamp,
            coverage=coverage,
            note=f"{len(records)} partidas de {kind}",
        )

    #: Utilidades para quem quer o tipo forte em vez de dicionário.
    @staticmethod
    def normalize_results(records: list[Any]) -> list[Any]:
        return [r for r in records if r is not None]

    @staticmethod
    def as_dict(record: Any) -> dict[str, Any]:
        if hasattr(record, "__dataclass_fields__"):
            return asdict(record)
        return dict(record)


class ApiFootballSource(BaseSource):
    """Adapter da API-Football. Sem chave, `available()` é False."""

    name = "api_football"
    capabilities = frozenset(
        {
            Capabilities.FIXTURES,
            Capabilities.RESULTS,
            Capabilities.STATS,
            Capabilities.XG,
        }
    )

    def __init__(self, provider: Any | None) -> None:
        self.provider = provider

    @classmethod
    def from_env(cls) -> "ApiFootballSource":
        from ..providers import ApiFootballProvider

        return cls(ApiFootballProvider.from_env())

    def available(self) -> bool:
        return self.provider is not None

    def fetch(self, kind: str, **params: Any) -> RawFetch:
        if self.provider is None:
            return RawFetch(records=[], source=self.name, complete=False,
                            note="API-Football sem chave configurada")
        if kind in (Capabilities.FIXTURES, Capabilities.RESULTS):
            league = int(params["league"])
            season = int(params["season"])
            payload = self.provider.fixtures_by_season(league, season)
            if kind == Capabilities.RESULTS:
                payload = [item for item in payload if _is_finished(item)]
            coverage = (f"league:{league}:season:{season}",)
            return RawFetch(
                records=list(payload), source=self.name, coverage=coverage,
                note=f"{len(payload)} partidas (league={league}, season={season})",
            )
        if kind == Capabilities.STATS:
            fixture_id = int(params["fixture_id"])
            payload = self.provider.fixture_statistics(fixture_id)
            complete = isinstance(payload, list) and len(payload) == 2
            return RawFetch(
                records=list(payload or []), source=self.name,
                coverage=(f"fixture:{fixture_id}",), complete=complete,
                note="estatísticas por time" if complete else "resposta incompleta",
            )
        raise ValueError(f"api_football não cobre {kind!r}")


class FootballDataOrgSource(BaseSource):
    """Adapter do Football-Data.org. Sem odds, sem xG, sem estatísticas."""

    name = "football_data_org"
    capabilities = frozenset({Capabilities.FIXTURES, Capabilities.RESULTS})

    def __init__(self, provider: Any | None) -> None:
        self.provider = provider

    @classmethod
    def from_env(cls) -> "FootballDataOrgSource":
        from ..providers import FootballDataProvider

        return cls(FootballDataProvider.from_env())

    def available(self) -> bool:
        return self.provider is not None

    def fetch(self, kind: str, **params: Any) -> RawFetch:
        if self.provider is None:
            return RawFetch(records=[], source=self.name, complete=False,
                            note="Football-Data.org sem chave configurada")
        competition = params.get("competition", "BSA")
        status = "SCHEDULED" if kind == Capabilities.FIXTURES else "FINISHED"
        matches = self.provider.matches(competition=competition, status=status)
        return RawFetch(
            records=list(matches), source=self.name,
            coverage=(f"competition:{competition}",),
            note=f"{len(matches)} partidas ({status})",
        )


def _is_finished(item: dict[str, Any]) -> bool:
    fixture = item.get("fixture") or {}
    status = ((fixture.get("status") or {}).get("short") or "").upper()
    goals = item.get("goals") or {}
    return status in FINISHED_STATUSES and goals.get("home") is not None and goals.get("away") is not None
