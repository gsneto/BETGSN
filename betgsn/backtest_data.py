"""BETGSN :: backtest_data — acesso point-in-time ao historico.

Este modulo existe para tornar o data leakage IMPOSSIVEL de acontecer por
descuido. Toda leitura de historico passa por `HistoricalCorpus`, que so
devolve partidas com kickoff ESTRITAMENTE anterior ao corte pedido.

Regra central
-------------
Ao prever a partida M (kickoff T), o modelo recebe apenas partidas com
kickoff < T. Partidas simultaneas (mesmo kickoff) tambem ficam de fora:
em T voce ainda nao conhece o resultado delas.

Tambem ficam fora do contexto qualquer agregacao derivada do futuro:
medias da liga, xG medio, cantos medios e a "visao de mercado" sao todos
recalculados apenas sobre o historico anterior.

Sobre as odds do backtest
-------------------------
O BETGSN nao possui odds historicas reais. Gerar odds a partir das
probabilidades do proprio modelo seria CIRCULAR: o modelo pareceria ter
edge onde o ruido tivesse sido favoravel.

A solucao honesta adotada por padrao e um MERCADO INGENUO: um mercado que
so conhece as medias da liga (gols, cantos, cartoes) do historico anterior
e ignora a forca dos times. Isso reproduz a hipotese que ja sustenta o
`betgsn/backtest.py` legado e mede uma pergunta legitima:

    "o modelo agrega informacao sobre um mercado que nao olha forca de time?"

Esse mercado NAO e um bookmaker real. Os resultados NAO sao evidencia de
lucro no mercado real. Para conclusoes reais e preciso plugar odds
historicas de verdade — ver `RealHistoricalOddsSource`.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from .data import LEAGUE_HOME_ADVANTAGE, synthesize_odds
from .markets import ALL_MARKET_KEYS, all_markets
from .model import (
    Fixture,
    HistoricalMatch,
    ScoreMatrix,
    TeamRating,
    build_score_matrix,
    expected_goals,
)
from .providers import ProviderError
from .timeutil import KickoffError, day_of, utc_key
from .temporal import result_time

Outcome = Literal["win", "loss", "push"]

#: Versao do schema de contexto point-in-time. Muda quando a definicao de
#: "o que estava disponivel antes do kickoff" mudar — o que invalida
#: comparacoes entre backtests antigos e novos.
POINT_IN_TIME_SCHEMA = "pit-2"


# --------------------------------------------------------------------------
# Resultado real de uma partida
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchResult:
    """Resultado observado. Usado SOMENTE depois do sinal estar congelado."""

    home_goals: int
    away_goals: int
    home_corners: int | None = None
    away_corners: int | None = None
    home_cards: int | None = None
    away_cards: int | None = None

    @classmethod
    def from_match(cls, m: HistoricalMatch) -> "MatchResult":
        return cls(
            home_goals=m.home_goals,
            away_goals=m.away_goals,
            home_corners=m.home_corners,
            away_corners=m.away_corners,
            home_cards=m.home_cards,
            away_cards=m.away_cards,
        )


# --------------------------------------------------------------------------
# Liquidacao do sinal (settlement)
# --------------------------------------------------------------------------

_NUM = r"([+-]?\d+(?:\.\d+)?)"
_AH_RE = re.compile(rf"^AH (Casa|Fora) {_NUM}$")
_TOTAL_RE = re.compile(rf"^(Over|Under) {_NUM}$")
_TEAM_TOTAL_RE = re.compile(rf"^(Casa|Fora) (Over|Under) {_NUM}$")
_TEAM_CORNERS_RE = re.compile(rf"^(Casa|Fora) Cantos (Over|Under) {_NUM}$")
_MARKET_TOTAL_RE = re.compile(rf"^(?:Cantos|Cartoes) (Over|Under) {_NUM}$")


def _resolve_over_under(over: bool, value: float, line: float) -> Outcome:
    """Linhas do BETGSN sao quebradas (.5) — nao existe push."""
    return "win" if (value > line) == over else "loss"


def settle_outcome(
    market: str,
    outcome: str,
    result: MatchResult,
) -> Outcome | None:
    """Liquida um resultado de mercado contra o placar real.

    Devolve "win" | "loss" | "push", ou None quando o dado necessario nao
    existe (ex.: sem contagem de cantos). Devolver None e explicito de
    proposito: um dado ausente nunca pode virar "derrota" silenciosa, o
    que inflaria artificialmente as metricas.

    O handicap asiatico pode devolver "push" (stake devolvida).
    """
    hg, ag = result.home_goals, result.away_goals

    if market == "Resultado Final (1X2)":
        actual = "1" if hg > ag else ("X" if hg == ag else "2")
        return "win" if outcome == actual else "loss"

    if market == "Dupla Chance":
        if outcome == "1X":
            return "win" if hg >= ag else "loss"
        if outcome == "12":
            return "win" if hg != ag else "loss"
        if outcome == "X2":
            return "win" if ag >= hg else "loss"
        return None

    if market == "Draw No Bet":
        if hg == ag:
            return "push"
        if outcome == "DNB Casa":
            return "win" if hg > ag else "loss"
        if outcome == "DNB Fora":
            return "win" if ag > hg else "loss"
        return None

    if market == "Ambas Marcam":
        both = hg > 0 and ag > 0
        if outcome == "BTTS Sim":
            return "win" if both else "loss"
        if outcome == "BTTS Nao":
            return "win" if not both else "loss"
        return None

    if market == "Total de Gols":
        m = _TOTAL_RE.match(outcome)
        if not m:
            return None
        return _resolve_over_under(m.group(1) == "Over", hg + ag, float(m.group(2)))

    if market == "Total por Time":
        m = _TEAM_TOTAL_RE.match(outcome)
        if not m:
            return None
        side, direction, line = m.group(1), m.group(2), float(m.group(3))
        value = hg if side == "Casa" else ag
        return _resolve_over_under(direction == "Over", value, line)

    if market == "Handicap Asiatico":
        m = _AH_RE.match(outcome)
        if not m:
            return None
        side, line = m.group(1), float(m.group(2))
        margin = (hg - ag + line) if side == "Casa" else (ag - hg + line)
        if margin > 1e-9:
            return "win"
        if abs(margin) <= 1e-9:
            return "push"
        return "loss"

    if market == "Escanteios":
        if result.home_corners is None or result.away_corners is None:
            return None
        total = result.home_corners + result.away_corners
        m = _TEAM_CORNERS_RE.match(outcome)
        if m:
            side, direction, line = m.group(1), m.group(2), float(m.group(3))
            value = result.home_corners if side == "Casa" else result.away_corners
            return _resolve_over_under(direction == "Over", value, line)
        m = _MARKET_TOTAL_RE.match(outcome)
        if m:
            return _resolve_over_under(m.group(1) == "Over", total, float(m.group(2)))
        return None

    if market == "Cartoes":
        if result.home_cards is None or result.away_cards is None:
            return None
        total = result.home_cards + result.away_cards
        m = _MARKET_TOTAL_RE.match(outcome)
        if m:
            return _resolve_over_under(m.group(1) == "Over", total, float(m.group(2)))
        return None

    return None


def realized_return(outcome_result: Outcome, odd: float) -> float:
    """Retorno por unidade apostada. Push devolve o stake (retorno 0)."""
    if outcome_result == "win":
        return odd - 1.0
    if outcome_result == "push":
        return 0.0
    return -1.0


def profit_for(outcome_result: Outcome, odd: float, stake: float) -> float:
    """Lucro em dinheiro de uma aposta resolvida."""
    if outcome_result == "win":
        return stake * (odd - 1.0)
    if outcome_result == "push":
        return 0.0
    return -stake


# --------------------------------------------------------------------------
# Corpus historico com corte point-in-time
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusStats:
    n_matches: int
    first_kickoff: str
    last_kickoff: str
    competitions: tuple[str, ...]
    seasons: tuple[str, ...]


class HistoricalCorpus:
    """Historico ordenado cronologicamente, com acesso point-in-time.

    Construido uma vez e consultado muitas vezes pelo motor de backtest.

    Ordenacao e corte usam uma CHAVE CANONICA EM UTC (`timeutil.utc_key`),
    nao a string crua. Isso permite misturar fontes com fusos diferentes
    (dataset local, API-Football com offset, cache de odds em UTC) sem que
    a ordem cronologica se quebre — o que seria uma forma silenciosa de
    data leakage. `match.kickoff` continua sendo a string original, usada
    apenas para exibicao.
    """

    def __init__(self, matches: Sequence[HistoricalMatch]) -> None:
        if not matches:
            raise ValueError("corpus vazio: nao ha historico para backtest")
        sem_kickoff = [m for m in matches if not m.kickoff]
        if sem_kickoff:
            raise ValueError(
                f"{len(sem_kickoff)} partida(s) sem kickoff. Sem timestamp nao "
                "existe corte point-in-time confiavel e o backtest seria "
                "desonesto. Corrija a fonte de dados antes de continuar."
            )
        try:
            pairs = [(utc_key(m.kickoff, m.timezone), m) for m in matches]
        except KickoffError as exc:
            raise ValueError(f"kickoff invalido no corpus: {exc}") from exc
        pairs.sort(key=lambda pair: pair[0])
        self._keys: list[str] = [k for k, _ in pairs]
        self._matches: list[HistoricalMatch] = [m for _, m in pairs]
        available = sorted((result_time(m), i, m) for i, m in enumerate(self._matches))
        self._available_keys = [k for k, _, _ in available]
        self._available_matches = [m for _, _, m in available]

    # ------------------------------------------------------------ consulta

    @property
    def matches(self) -> tuple[HistoricalMatch, ...]:
        return tuple(self._matches)

    def all_kickoffs(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def utc_key_of(self, match: HistoricalMatch) -> str:
        """Chave canonica UTC de uma partida (para agrupar ou comparar)."""
        return utc_key(match.kickoff, match.timezone)

    def matches_before(self, cutoff: str, cutoff_tz: str = "") -> list[HistoricalMatch]:
        """Partidas com kickoff ESTRITAMENTE anterior a `cutoff`.

        `bisect_left` posiciona no primeiro kickoff >= cutoff, entao todas
        as partidas simultaneas ao cutoff ficam de fora. E o comportamento
        correto: no instante do kickoff o resultado delas e desconhecido.

        O cutoff e normalizado para UTC antes da comparacao, entao aceita
        qualquer formato suportado por `timeutil.parse_kickoff`.
        """
        try:
            key = utc_key(cutoff, cutoff_tz)
        except KickoffError as exc:
            raise ValueError(f"cutoff invalido: {exc}") from exc
        idx = bisect_left(self._keys, key)
        return self._matches[:idx]

    def available_before(self, cutoff: str, cutoff_tz: str = "") -> list[HistoricalMatch]:
        """Resultados publicados antes do corte; ausência aplica embargo explícito."""
        key = utc_key(cutoff, cutoff_tz)
        idx = bisect_left(self._available_keys, key)
        from dataclasses import replace
        out = []
        for m in sorted(self._available_matches[:idx], key=self.utc_key_of):
            # xG externo só está disponível se houver publicação anterior.
            # Dados de demonstração continuam explicitamente experimentais.
            if m.xg_status == "REAL" and (not m.xg_available_at or utc_key(m.xg_available_at) >= key):
                if m.home_xg is not None or m.away_xg is not None:
                    m = replace(m, home_xg=None, away_xg=None, home_xg_against=None,
                                away_xg_against=None, xg_status="UNAVAILABLE")
            out.append(m)
        return out

    def stats(self) -> CorpusStats:
        return CorpusStats(
            n_matches=len(self._matches),
            first_kickoff=self._keys[0],
            last_kickoff=self._keys[-1],
            competitions=tuple(sorted({m.league for m in self._matches if m.league})),
            seasons=tuple(sorted({m.season for m in self._matches if m.season})),
        )

    def filter(
        self,
        start: str | None = None,
        end: str | None = None,
        competitions: Sequence[str] | None = None,
    ) -> list[HistoricalMatch]:
        """Recorte do corpus por periodo (dia UTC) e competicao.

        `start`/`end` comparam apenas a data (YYYY-MM-DD), inclusive.
        """
        comps = set(competitions) if competitions else None
        out: list[HistoricalMatch] = []
        for m in self._matches:
            day = day_of(m.kickoff, m.timezone)
            if start and day < start:
                continue
            if end and day > end:
                continue
            if comps is not None and m.league not in comps:
                continue
            out.append(m)
        return out


# --------------------------------------------------------------------------
# Visao de mercado point-in-time (mercado ingenuo)
# --------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def league_average_rating(prior: Sequence[HistoricalMatch]) -> TeamRating:
    """Rating de um 'time medio' calculado so com o historico anterior.

    attack = defense = 1.0 (exatamente a media da liga). As contagens
    (gols, xG, cantos, cartoes, chutes) sao medias por time por jogo.
    """
    n = max(1, len(prior))
    gf = _mean([m.home_goals for m in prior] + [m.away_goals for m in prior])
    xg_vals = [m.home_xg for m in prior if m.home_xg is not None] + [
        m.away_xg for m in prior if m.away_xg is not None
    ]
    corners = [m.home_corners for m in prior if m.home_corners is not None] + [
        m.away_corners for m in prior if m.away_corners is not None
    ]
    cards = [m.home_cards for m in prior if m.home_cards is not None] + [
        m.away_cards for m in prior if m.away_cards is not None
    ]
    shots = [m.home_shots for m in prior if m.home_shots is not None] + [
        m.away_shots for m in prior if m.away_shots is not None
    ]
    pts = _mean(
        [3 if m.home_goals > m.away_goals else (1 if m.home_goals == m.away_goals else 0)
         for m in prior]
        + [3 if m.away_goals > m.home_goals else (1 if m.home_goals == m.away_goals else 0)
           for m in prior]
    )
    return TeamRating(
        name="__media_liga__",
        attack=1.0,
        defense=1.0,
        goals_for=gf,
        goals_against=gf,
        xg_for=_mean(xg_vals) if xg_vals else None,
        xg_against=_mean(xg_vals) if xg_vals else None,
        corners_for=_mean(corners) if corners else 5.0,
        corners_against=_mean(corners) if corners else 5.0,
        cards_for=_mean(cards) if cards else 2.3,
        cards_against=_mean(cards) if cards else 2.3,
        shots_for=_mean(shots) if shots else 11.5,
        shots_on_target_for=_mean(shots) * 0.35 if shots else 4.0,
        form_points=pts,
        matches_played=n,
    )


def league_goals_before(prior: Sequence[HistoricalMatch]) -> float:
    """Total medio de gols por jogo usando apenas o historico anterior."""
    if not prior:
        return 0.0
    return _mean([m.home_goals + m.away_goals for m in prior])


@dataclass(frozen=True)
class PointInTimeContext:
    """Tudo que o motor pode saber antes do kickoff. Nada alem disso."""

    cutoff: str
    n_prior_matches: int
    league_goals: float
    home_advantage: float
    league_average: TeamRating
    reference_probs: dict[str, dict[str, float]]


def build_context(
    prior: Sequence[HistoricalMatch],
    cutoff: str,
    market_keys: tuple[str, ...] = ALL_MARKET_KEYS,
    home_advantage: float = LEAGUE_HOME_ADVANTAGE,
    attack_blend: float = 0.5,
    with_reference_probs: bool = True,
) -> PointInTimeContext:
    """Monta o contexto disponivel antes do kickoff.

    Tudo aqui e derivado APENAS de `prior`. A `reference_probs` e a visao
    do mercado ingenuo — a contraparte com que o modelo vai competir.

    `with_reference_probs=False` pula esse calculo. Fonte de odds REAIS
    nao usa o mercado de referencia (ela traz as odds do bookmaker), e
    montar a matriz de placar + todos os mercados a cada refit era o
    gargalo dominante do backtest em corpus grande.
    """
    avg = league_average_rating(prior)
    lg = league_goals_before(prior)
    reference: dict[str, dict[str, float]] = {}
    if with_reference_probs:
        lam_h, lam_a = expected_goals(
            avg, avg, lg, home_advantage=home_advantage, attack_blend=attack_blend,
        )
        matrix: ScoreMatrix = build_score_matrix(lam_h, lam_a)
        reference = all_markets(matrix, avg, avg, include=market_keys)
    return PointInTimeContext(
        cutoff=cutoff,
        n_prior_matches=len(prior),
        league_goals=lg,
        home_advantage=home_advantage,
        league_average=avg,
        reference_probs=reference,
    )


# --------------------------------------------------------------------------
# Fontes de odds
# --------------------------------------------------------------------------


def match_seed(base_seed: int, match: HistoricalMatch) -> int:
    """Seed estavel por partida.

    Usa sha256 em vez de hash() porque o hash de strings do Python e
    salgado por processo — dois backtests identicos dariam odds diferentes.
    """
    raw = f"{base_seed}|{match.kickoff}|{match.home}|{match.away}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


class OddsSource(Protocol):
    """Fonte de odds historicas. Implementacoes devem ser point-in-time."""

    name: str
    #: True quando a fonte PRECISA do mercado de referencia (baseline
    #: sintetico). Fontes de odds reais trazem o preco do bookmaker e nao
    #: usam esse calculo — pulá-lo acelera muito o backtest.
    needs_reference_probs: bool

    def odds_for(
        self,
        match: HistoricalMatch,
        reference_probs: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, dict[str, float]]]:
        """Devolve {mercado: {casa: {resultado: odd}}} para a partida."""
        ...


class NaiveSyntheticOddsSource:
    """Mercado ingenuo sintetico (padrao offline).

    Constroi as odds a partir da visao de mercado (medias da liga) usando
    exatamente o mesmo gerador multi-casa do Scanner (`synthesize_odds`):
    mesmas margens por casa, mesmo vies de longshot, mesmo ruido.

    Nao conhece o resultado da partida. Nao conhece a forca dos times —
    e justamente por isso que o edge medido e informacao do modelo, e nao
    um artefato circular.
    """

    name = "naive_synthetic"
    needs_reference_probs = True

    def __init__(self, base_seed: int = 6767, market_error_sigma: float = 0.04) -> None:
        self.base_seed = base_seed
        self.market_error_sigma = market_error_sigma
        #: instante do mercado usado no ultimo calculo
        self.last_as_of: str | None = None

    def odds_for(
        self,
        match: HistoricalMatch,
        reference_probs: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, dict[str, float]]]:
        key = f"{match.home} vs {match.away}"
        seed = match_seed(self.base_seed, match)
        generated = synthesize_odds(
            {key: reference_probs},
            seed=seed,
            market_error_sigma=self.market_error_sigma,
        )
        self.last_as_of = utc_key(match.kickoff, match.timezone)
        return generated.get(key, {})


class RealHistoricalOddsSource:
    """Odds historicas reais, lidas do cache local point-in-time.

    As odds NAO sao geradas nem aproximadas: sao capturas reais da The Odds
    API, importadas por `backtest_sources.HistoricalOddsImporter` e
    guardadas com o timestamp de cada snapshot.

    Garantia point-in-time: para cada partida, este source pega o snapshot
    MAIS RECENTE cujo timestamp seja estritamente anterior ao kickoff e
    dentro da janela de validade (`max_age_hours`). Se nao houver snapshot
    valido, levanta `ProviderError` — nunca cai para o mercado sintetico em
    silencio, porque isso misturaria duas hipoteses diferentes no mesmo
    resultado.
    """

    name = "real_historical"
    needs_reference_probs = False

    def __init__(
        self,
        cache: "OddsHistoryCache | None" = None,
        sport_key: str = "soccer_brazil_campeonato",
        max_age_hours: float = 24.0,
    ) -> None:
        self.sport_key = sport_key
        self.max_age_hours = max_age_hours
        if cache is None:
            from .backtest_sources import OddsHistoryCache

            cache = OddsHistoryCache()
        self.cache = cache
        self._misses: list[str] = []
        #: timestamp do snapshot efetivamente usado no ultimo calculo
        self.last_as_of: str | None = None

    @property
    def misses(self) -> list[str]:
        """Partidas sem snapshot valido no cache (para diagnostico)."""
        return list(self._misses)

    def odds_for(
        self,
        match: HistoricalMatch,
        reference_probs: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, dict[str, float]]]:
        from .backtest_sources import OddsHistoryError, odds_from_snapshot

        try:
            snapshot = self.cache.find_before(
                self.sport_key,
                utc_key(match.kickoff, match.timezone),
                max_age_hours=self.max_age_hours,
            )
        except OddsHistoryError as exc:
            raise ProviderError(str(exc)) from exc

        if snapshot is None:
            label = f"{match.home} vs {match.away} ({match.kickoff})"
            self._misses.append(label)
            raise ProviderError(
                f"Sem odds historicas reais para {label}. "
                f"Importe a janela com `python betgsn.py --import-odds "
                f"--sport={self.sport_key}` (requer BETGSN_ODDS_API_KEY) ou "
                f"rode o backtest com odds_source='naive_synthetic'. "
                f"O BETGSN nao gera odds: isso seria inventar dado."
            )

        odds = odds_from_snapshot(snapshot, match)
        if not odds:
            raise ProviderError(
                f"Snapshot de {snapshot.timestamp} nao contem a partida "
                f"{match.home} vs {match.away}. Os nomes de time do provedor "
                f"podem divergir da fonte de resultados."
            )
        self.last_as_of = snapshot.utc_key
        return odds


#: Dados historicos que ainda faltam para um backtest com odds reais.
MISSING_DATA_NOTES: tuple[dict[str, str], ...] = (
    {
        "dado": "odds historicas reais importadas para o periodo testado",
        "por_que": "sem elas o backtest mede edge contra um baseline sintetico, "
                   "nao contra um bookmaker real",
        "quem_fornece": "The Odds API — /v4/historical/sports/{sport}/odds",
        "como_plugar": "definir BETGSN_ODDS_API_KEY e rodar "
                       "`python betgsn.py --import-odds --sport=... --start=... --end=...`",
    },
    {
        "dado": "resultados, xG, cantos e cartoes de temporadas reais",
        "por_que": "hoje o corpus local e sintetico, com 1 temporada e 1 "
                   "competicao, o que limita a segmentacao",
        "quem_fornece": "API-Football — /fixtures, /fixtures/statistics",
        "como_plugar": "definir BETGSN_APIFOOTBALL_KEY e rodar "
                       "`python betgsn.py --import-fixtures --league=71 --season=2024`",
    },
    {
        "dado": "fuso horario e horario exato de cada partida",
        "por_que": "define o corte point-in-time com precisao de minutos",
        "quem_fornece": "API-Football (/fixtures.date, ISO 8601 com offset)",
        "como_plugar": "ja suportado: o importador grava kickoff com offset e "
                       "o corpus normaliza tudo para UTC",
    },
)


def build_fixture(match: HistoricalMatch) -> Fixture:
    """Adapta uma partida historica ao tipo `Fixture` usado pelo motor."""
    return Fixture(
        home=match.home,
        away=match.away,
        league=match.league,
        kickoff=match.kickoff,
        round_label=match.season,
    )
