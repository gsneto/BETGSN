"""BETGSN :: datalayer.xg — xG real com proveniência e ausência explícita.

xG nunca é fabricado nem inferido de chutes. Se a liga não publica
`expected_goals`, a resposta é `UNAVAILABLE` com o motivo — jamais um zero.
Quando há xG real, a orientação casa/fora é determinada pelo NOME do time
(não pela ordem do payload), para não trocar os lados.
"""

from __future__ import annotations

from typing import Any, Callable

from ..backtest_sources import normalize_team
from ..xg_sources import UnavailableXGSource, XGObservation, XGStatus, unavailable

__all__ = [
    "xg_from_statistics",
    "ApiFootballXGSource",
    "UnavailableXGSource",
]


def _stat_value(entry: dict[str, Any], stat_type: str) -> float | None:
    for item in entry.get("statistics", []):
        if item.get("type") != stat_type:
            continue
        value = item.get("value")
        if value is None:
            return None
        if isinstance(value, str) and value.endswith("%"):
            value = value[:-1]
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def xg_from_statistics(
    payload: Any,
    home: str,
    away: str,
    *,
    source: str = "API-Football",
    available_at: str = "",
) -> XGObservation:
    """Extrai xG real de `/fixtures/statistics`, orientando por nome de time.

    Devolve ausência explícita quando o payload é incompleto, os times não
    são identificáveis ou a liga não publica xG.
    """
    if not isinstance(payload, list) or len(payload) != 2:
        return unavailable("estatísticas incompletas para xG", source=source)

    by_name: dict[str, dict[str, Any]] = {}
    for entry in payload:
        name = ((entry.get("team") or {}).get("name") or "")
        by_name[normalize_team(name)] = entry

    home_entry = by_name.get(normalize_team(home))
    away_entry = by_name.get(normalize_team(away))
    if home_entry is None or away_entry is None:
        return unavailable(
            "não foi possível identificar os times para orientar o xG",
            source=source,
        )

    home_xg = _stat_value(home_entry, "expected_goals")
    away_xg = _stat_value(away_entry, "expected_goals")
    if home_xg is None or away_xg is None:
        return unavailable("liga/temporada sem xG publicado", source=source)

    return XGObservation(
        home_xg=home_xg,
        away_xg=away_xg,
        home_xg_against=away_xg,
        away_xg_against=home_xg,
        status=XGStatus.REAL,
        source=source,
        available_at=available_at or None,
        note="xG real do provedor",
    )


class ApiFootballXGSource:
    """Fonte de xG apoiada em API-Football.

    Precisa saber o mando da partida (`team_names`): um mapa
    `fixture_id -> (home, away)` ou uma função `match_id -> (home, away)`.
    Sem esse contexto, declara ausência em vez de chutar a orientação.
    """

    name = "api_football"

    def __init__(
        self,
        provider: Any | None,
        team_names: dict[Any, tuple[str, str]] | Callable[[str], tuple[str, str] | None] | None = None,
    ) -> None:
        self.provider = provider
        self.team_names = team_names

    @classmethod
    def from_env(cls) -> "ApiFootballXGSource":
        from ..providers import ApiFootballProvider

        return cls(ApiFootballProvider.from_env())

    def _names(self, match_id: str) -> tuple[str, str] | None:
        if self.team_names is None:
            return None
        if callable(self.team_names):
            return self.team_names(match_id)
        return self.team_names.get(match_id) or self.team_names.get(str(match_id))

    def fetch(self, match_id: str) -> XGObservation:
        if self.provider is None:
            return unavailable("API-Football sem chave configurada", source=self.name)
        names = self._names(match_id)
        if names is None:
            return unavailable(
                "sem contexto de mando para orientar o xG", source=self.name
            )
        home, away = names
        payload = self.provider.fixture_statistics(int(match_id))
        return xg_from_statistics(payload, home, away, source=self.name)
