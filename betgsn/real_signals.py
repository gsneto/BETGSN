"""BETGSN :: real_signals — sinais a partir de JOGOS FUTUROS REAIS.

Por que este modulo existe
--------------------------
A tela SINAIS usava `pipeline.run()`, que gera o dataset sintetico em
memoria: jogos inventados, com datas fixas no codigo e odds que o proprio
modelo produziu. Isso serve para testar o pipeline, mas nao para decidir
aposta — e o backtest provou que medir o modelo contra o proprio espelho
produz resultado falso (+219.348%).

Aqui os sinais vem de:
  - JOGOS FUTUROS REAIS, do arquivo `fixtures.csv` do football-data.co.uk;
  - ODDS REAIS de varios bookmakers (Bet365, Betfair Exchange, Paddy
    Power, SkyBet, Betfred, BetVictor, Bet&Win, Pinnacle...);
  - RATINGS ajustados no historico REAL (259 mil partidas, 2000-2026).

O motor e exatamente o mesmo: `pipeline.analyze_fixture` +
`signals.generate_signals`. Nada de logica paralela — so a FONTE muda.

Limitacao que precisa ficar clara
---------------------------------
As odds dos jogos futuros sao de ABERTURA (o mercado acabou de abrir) e o
arquivo e atualizado 1-2x por semana. Nao sao a linha de fechamento, e nao
sao ao vivo. Servem para achar candidatos; a odd final voce confere na
casa antes de apostar.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Sequence

from .data import LEAGUE_HOME_ADVANTAGE
from .football_data_uk import (
    FootballDataClient,
    UpcomingFixture,
)
from .markets import ALL_MARKET_KEYS, MARKET_LABELS, validate_market_keys
from .model import Fixture, TeamRating, fit_ratings
from .pipeline import analyze_fixture
from .signals import MIN_BOOKS, SignalReport, build_report
from .timeutil import day_of, now_utc, utc_key

#: Ratings sao ajustados com o historico destes ultimos anos. Janela
#: recente: um time de 2010 nao ajuda a prever 2026.
RATING_WINDOW_YEARS = 3

#: Minimo de partidas no historico para o fit ser confiavel.
MIN_HISTORY = 300

#: Calibracao MEDIDA do modelo no backtest com odds reais.
#: Nao e estimativa: e o resultado de 13.334 partidas do top-5 europeu
#: (2018-2025) comparadas contra a linha de fechamento.
#:
#:   EV medio previsto : +22,4%
#:   retorno realizado : -1,7%
#:   GAP               : +24,1 pp
#:   ROI simulado      : -5,5%
#:
#: Consequencia pratica: o EV mostrado nos sinais esta INFLADO em cerca de
#: 24 pontos percentuais. Um sinal com EV +80% provavelmente tem EV real
#: proximo de zero — ou negativo.
MODEL_CALIBRATION = {
    "measured_on": "13.334 partidas (top-5 europeu, 2018-2025)",
    "ev_predicted": 0.224,
    "return_realized": -0.017,
    "gap_pp": 0.241,
    "simulated_roi": -0.055,
    "verdict": (
        "O modelo e sistematicamente superconfiante. O EV exibido esta "
        "inflado em ~24 pontos percentuais e NAO deve ser usado como "
        "estimativa de retorno."
    ),
}


def calibrate_ev(raw_ev: float, gap_pp: float = MODEL_CALIBRATION["gap_pp"]) -> float:
    """Desconta o vies medido do EV bruto do modelo.

    E uma correcao de primeira ordem: subtrai o gap medido. Nao torna o
    modelo bom — apenas evita que o numero exibido minta na mesma direcao
    sempre. Um EV de +80% vira ~+56%, que ainda e otimista, mas nao
    absurdo.

    CONTRATO — diagnostico, NAO entrada de decisao (I-08):

    Esta funcao NAO entra no caminho de decisao de producao, e isso e
    deliberado:

    1. Quem decide e `staking.decide_bet`, sobre a vantagem VALIDADA da
       regra de mercado (`value_strategy`) — ROI medido em odds reais,
       nao o EV do modelo. O EV inflado do modelo nao contamina a
       decisao porque a decisao nao o consome.
    2. O gap e uma MEDIA de populacao (top-5 europeu, 2018-2025) medida
       contra odds de fechamento SEM timestamp de publicacao. Aplica-lo
       por sinal como se fosse calibracao por sinal trocaria um vies
       conhecido por outro desconhecido: a dispersao do gap por
       mercado/liga/faixa de odd nunca foi medida.
    3. Integrar calibracao de verdade exige: fonte com timestamp
       (OddsSnapshotStore), recalibracao por segmento fora do cutoff
       (PIT), validacao OOS e verificacao via CLV prospectivo. Nada
       disso existe ainda para o EV por sinal.

    Enquanto esses requisitos nao existirem, o EV exibido nos sinais e
    o BRUTO e o aviso de inflacao viaja separado
    (`ModelCalibrationInfo` -> API -> UI). Ocultar o bruto e aplicar
    esta correcao por baixo seria trocar um numero mentiroso por um
    numero que finge ser calibrado.
    """
    return raw_ev - gap_pp


class RealDataError(RuntimeError):
    """Dados reais indisponiveis. Sempre explicito: nunca cai no sintetico."""


class RecalculateCancelled(RuntimeError):
    """Cancelamento cooperativo pedido durante o recalculo."""


#: Protocolo de progresso: (fase, feitas, total, mensagem). As fases sao
#: etapas REAIS do pipeline — nenhuma e inventada para animar a UI.
ProgressCallback = Callable[[str, int, int, str], None]
#: Retorna True quando o usuario pediu cancelamento.
CancelCheck = Callable[[], bool]


@dataclass
class RealSnapshot:
    """Estado calculado a partir de dados reais."""

    fixtures: list[UpcomingFixture]
    ratings: dict[str, TeamRating]
    league_goals: float
    teams: list[str]
    n_history: int
    history_window: tuple[str, str]
    generated_at: str
    computed_in_ms: float
    sources: list[str]
    #: quantos jogos futuros ficaram sem rating (time fora da janela)
    skipped_no_rating: int = 0
    #: jogos futuros com odds mas sem consenso minimo de casas (MIN_BOOKS)
    skipped_insufficient_books: int = 0
    #: calibracao medida do modelo, para a UI poder avisar
    calibration: dict = field(default_factory=lambda: dict(MODEL_CALIBRATION))
    history: list = field(default_factory=list)
    analyses: list = field(default_factory=list)
    cutoff: str = ""

    def fixture_of(self, match: str) -> UpcomingFixture | None:
        for fx in self.fixtures:
            if fx.match == match:
                return fx
        return None


def _fit_on_real_history(
    client: FootballDataClient,
    window_years: int = RATING_WINDOW_YEARS,
    cutoff: str | None = None,
    *,
    matches: Sequence | None = None,
) -> tuple[dict[str, TeamRating], float, list[str], int, tuple[str, str]]:
    """Ajusta os ratings usando apenas o historico real recente.

    `matches` permite reaproveitar o historico ja carregado/convertido
    pelo chamador: parsear ~600 CSVs custa dezenas de segundos e o
    snapshot precisa do MESMO corpus duas vezes (fit + prior) — carregar
    uma vez so nao muda o resultado, so o tempo.
    """
    from .backtest_data import HistoricalCorpus
    from .timeutil import parse_kickoff

    cutoff = cutoff or datetime.now(timezone.utc).isoformat()
    if matches is None:
        matches = HistoricalCorpus(
            [m.to_historical() for m in client.load_matches()]
        ).available_before(cutoff)
    if len(matches) < MIN_HISTORY:
        raise RealDataError(
            f"historico real insuficiente: {len(matches)} partidas "
            f"(minimo {MIN_HISTORY}). Rode `python betgsn.py --import-fduk`."
        )

    # janela recente: descarta o que for mais antigo que o corte
    corte = (parse_kickoff(cutoff) - timedelta(days=365.25 * window_years)).isoformat()
    recentes = [m for m in matches if utc_key(m.kickoff, m.timezone) >= utc_key(corte)]
    if len(recentes) < MIN_HISTORY:
        recentes = list(matches)

    teams = sorted({m.home for m in recentes} | {m.away for m in recentes})
    history = recentes
    ratings = fit_ratings(history, teams, home_advantage=LEAGUE_HOME_ADVANTAGE)
    league_goals = sum(m.home_goals + m.away_goals for m in recentes) / len(recentes)

    datas = sorted(utc_key(m.kickoff, m.timezone) for m in recentes)
    return ratings, league_goals, teams, len(recentes), (datas[0], datas[-1])


class RealSignalsService:
    """Gera sinais a partir de jogos futuros reais. Cacheia o caro."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: RealSnapshot | None = None
        self._snapshot_key: tuple[int, int, str] | None = None

    # ------------------------------------------------------------- cache

    def _cache_key(self, client: FootballDataClient) -> tuple[int, int, str]:
        """Assinatura dos dados: muda quando historico ou fixtures mudam.

        Inclui o mtime do cache do fallback (`oddsapi.json`): o corpus
        signature do cliente so cobre CSVs, e um fallback novo precisa
        invalidar o snapshot cacheado.
        """
        from .fixtures_odds_api import oddsapi_cache_path

        try:
            fallback_mtime = int(
                oddsapi_cache_path(client._fixtures_dir).stat().st_mtime
            )
        except OSError:
            fallback_mtime = 0
        return (
            client.corpus_signature(),
            fallback_mtime,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H"),
        )

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None
            self._snapshot_key = None

    def snapshot(
        self,
        force: bool = False,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> RealSnapshot:
        """Constroi (ou reaproveita) o snapshot de dados reais.

        `progress` recebe as fases REAIS da construcao (fixtures ->
        history -> ratings); `cancel` e consultado entre fases — o
        cancelamento e cooperativo e nunca deixa snapshot pela metade:
        ou o snapshot completo substitui o anterior, ou nada muda.
        """
        client = FootballDataClient()
        key = self._cache_key(client)
        with self._lock:
            if not force and self._snapshot is not None and self._snapshot_key == key:
                if progress is not None:
                    progress("cache", 1, 1, "Snapshot em cache reutilizado.")
                return self._snapshot

        def _cancelled() -> None:
            if cancel is not None and cancel():
                raise RecalculateCancelled(
                    "recalculo cancelado pelo usuario; snapshot anterior preservado"
                )

        started = time.perf_counter()
        _cancelled()
        if progress is not None:
            progress("fixtures", 0, 1, "Carregando jogos futuros…")
        fixtures = client.load_fixtures()
        if not fixtures:
            raise RealDataError(
                "nenhum jogo futuro em cache. Rode "
                "`python betgsn.py --import-fixtures-live` para baixar os "
                "jogos da rodada com odds reais."
            )
        com_odds = [f for f in fixtures if f.has_odds]
        if not com_odds:
            raise RealDataError(
                f"{len(fixtures)} jogos futuros em cache, mas nenhum com odds. "
                "Os bookmakers ainda nao publicaram, ou o arquivo esta velho. "
                "Rode `python betgsn.py --import-fixtures-live` de novo."
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Fixture sem horario publicado nao tem instante — fica fora do
        # corte temporal (nao fabricamos 00:00). Ver UpcomingFixture.kickoff.
        com_odds = [
            f for f in com_odds
            if f.has_kickoff and utc_key(f.kickoff, f.timezone) > now
        ]
        if not com_odds:
            raise RealDataError(
                "nenhum jogo futuro após o instante atual; atualize com "
                "--import-fixtures-live (football-data.co.uk, com fallback "
                "The Odds API quando o CSV da rodada não tem jogos futuros)"
            )
        # Um único corte conservador, anterior a TODOS os jogos do snapshot.
        cutoff = min(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     min(utc_key(f.kickoff, f.timezone) for f in com_odds))
        from .backtest_data import HistoricalCorpus
        _cancelled()
        if progress is not None:
            progress("history", 0, 1, "Lendo histórico real (CSVs)…")
        # O corpus historico e carregado/convertido UMA vez e reutilizado
        # pelo fit e pelo prior — antes eram dois parses completos dos
        # ~600 CSVs, com o mesmo resultado.
        historical = [m.to_historical() for m in client.load_matches()]
        matches = HistoricalCorpus(historical).available_before(cutoff)

        _cancelled()
        if progress is not None:
            progress("ratings", 0, 1,
                     f"Ajustando ratings em {len(matches)} partidas…")
        ratings, league_goals, teams, n_hist, window = _fit_on_real_history(
            client, cutoff=cutoff, matches=matches)
        prior = [m for m in matches
                 if window[0] <= utc_key(m.kickoff, m.timezone) <= window[1]]
        elapsed = (time.perf_counter() - started) * 1000.0

        inv = client.inventory()
        sources = [f"football-data.co.uk ({inv['total_mb']} MB de historico)"]
        if inv["extras"]:
            sources.append(f"{len(inv['extras'])} ligas extras")
        # proveniencia explicita: fixtures servidas pelo fallback
        n_fallback = sum(1 for f in com_odds if f.source == "the_odds_api")
        if n_fallback:
            sources.append(f"The Odds API (fallback, {n_fallback} jogos futuros)")

        snap = RealSnapshot(
            fixtures=com_odds,
            ratings=ratings,
            league_goals=league_goals,
            teams=teams,
            n_history=n_hist,
            history_window=window,
            generated_at=now_utc(),
            computed_in_ms=round(elapsed, 2),
            sources=sources,
            cutoff=cutoff,
            history=prior,
        )
        with self._lock:
            self._snapshot = snap
            self._snapshot_key = key
        return snap

    # ------------------------------------------------------------ sinais

    def report(
        self,
        bankroll: float = 1000.0,
        kelly_frac: float = 0.25,
        stake_cap: float = 0.01,
        min_ev: float = 0.02,
        max_exposure: float = 0.25,
        use_xg: bool = True,
        market_keys: Sequence[str] | None = None,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> tuple[SignalReport, RealSnapshot]:
        """Gera o relatorio de sinais sobre os jogos futuros reais.

        `progress` recebe a fase `analyzing` com contagem REAL de
        fixtures processadas (i/n); `cancel` e consultado a cada fixture.
        """
        snap = self.snapshot(progress=progress, cancel=cancel)
        keys = validate_market_keys(tuple(market_keys or ()))
        blend = 0.5 if use_xg else 0.0

        fixtures: list[Fixture] = []
        model_by_fixture: dict[str, dict[str, dict[str, float]]] = {}
        odds_by_fixture: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
        skipped = 0
        skipped_books = 0
        analyses = []

        total = len(snap.fixtures)
        for i, fx in enumerate(snap.fixtures):
            if cancel is not None and cancel():
                raise RecalculateCancelled(
                    "recalculo cancelado pelo usuario; snapshot anterior preservado"
                )
            if progress is not None and (i % 10 == 0 or i == total - 1):
                progress("analyzing", i, total,
                         f"Analisando fixtures ({i}/{total})…")
            hr = snap.ratings.get(fx.home)
            ar = snap.ratings.get(fx.away)
            if hr is None or ar is None:
                # time sem rating: estreante na janela, ou nome divergente.
                # Pular e mais honesto que inventar um rating medio.
                skipped += 1
                continue

            fixture = Fixture(
                home=fx.home,
                away=fx.away,
                league=fx.league,
                kickoff=utc_key(fx.kickoff, fx.timezone),
                round_label=fx.division,
            )
            analysis = analyze_fixture(
                fixture, hr, ar, snap.league_goals, LEAGUE_HOME_ADVANTAGE,
                attack_blend=blend, market_keys=keys,
            )
            fixtures.append(fixture)
            analysis.odds = fx.odds
            analyses.append(analysis)
            model_by_fixture[fx.match] = analysis.markets
            odds_by_fixture[fx.match] = fx.odds

            # Diagnostico: jogos cujo melhor mercado tem menos casas que o
            # MIN_BOOKS nao produzem sinal (o classify descarta). Conta-se
            # para a UI explicar o estado vazio — ligas menores so trazem 2
            # casas no arquivo de fixtures, e nao ha consenso.
            max_books = max((len(b) for b in fx.odds.values()), default=0)
            if max_books < MIN_BOOKS:
                skipped_books += 1

        snap.skipped_no_rating = skipped
        snap.skipped_insufficient_books = skipped_books
        snap.analyses = analyses

        # Relatorio de sinais: pode ser vazio sem que isso seja erro. Quando
        # so sobram jogos de ligas menores (2 casas), nao ha consenso e a UI
        # explica o motivo com o contador de descartes acima.
        if progress is not None:
            progress("signals", 0, 1, "Construindo relatório de sinais…")
        report = build_report(
            fixtures, model_by_fixture, odds_by_fixture, bankroll,
            kelly_frac=kelly_frac, min_ev=min_ev, stake_cap=stake_cap,
            max_exposure_frac=max_exposure,
        )
        return report, snap


real_signals_service = RealSignalsService()
