"""BETGSN :: pipeline — orquestra dados -> modelo -> mercados -> sinais.

Uma chamada (run) devolve tudo que a interface precisa:
  - ratings por time;
  - matriz de placar e mercados de cada fixture;
  - odds multi-casa de cada fixture;
  - relatorio de sinais ranqueado;
  - dicas em texto.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import BOOKMAKERS, LeagueDataset, build_dataset, synthesize_odds
from .markets import ALL_MARKET_KEYS, all_markets
from .model import Fixture, ScoreMatrix, TeamRating, build_score_matrix, expected_goals, fit_ratings
from .signals import SignalReport, build_report, top_tips


@dataclass
class FixtureAnalysis:
    fixture: Fixture
    ratings_home: TeamRating
    ratings_away: TeamRating
    lambdas: tuple[float, float]
    matrix: ScoreMatrix
    markets: dict[str, dict[str, float]]
    odds: dict[str, dict[str, dict[str, float]]]
    top_scorelines: list[tuple[int, int, float]]


@dataclass
class RunResult:
    dataset: LeagueDataset
    ratings: dict[str, TeamRating]
    analyses: list[FixtureAnalysis] = field(default_factory=list)
    report: SignalReport | None = None
    tips: list[str] = field(default_factory=list)
    league_goals: float = 2.55


def analyze_fixture(
    fixture: Fixture,
    home_rating: TeamRating,
    away_rating: TeamRating,
    league_goals: float,
    home_advantage: float,
    attack_blend: float = 0.5,
    market_keys: tuple[str, ...] = ALL_MARKET_KEYS,
    ref_strictness: float = 1.0,
) -> FixtureAnalysis:
    """Analisa UM jogo: lambdas -> matriz de placar -> mercados do modelo.

    Este e o caminho unico de analise do BETGSN. O Scanner (via `run`) e o
    Backtest chamam exatamente esta funcao, o que garante que os dois
    produzem os mesmos numeros para o mesmo contexto. Nenhuma regra
    estatistica e duplicada em outro lugar.

    `home_rating`/`away_rating`/`league_goals`/`home_advantage` sao
    injetados por quem chama — e isso que permite ao backtest passar um
    contexto cortado no tempo (point-in-time) sem mudar o motor.
    """
    lam_h, lam_a = expected_goals(
        home_rating, away_rating, league_goals,
        home_advantage=home_advantage, attack_blend=attack_blend,
    )
    matrix = build_score_matrix(lam_h, lam_a)
    markets = all_markets(
        matrix, home_rating, away_rating,
        ref_strictness=ref_strictness, include=market_keys,
    )
    return FixtureAnalysis(
        fixture=fixture,
        ratings_home=home_rating,
        ratings_away=away_rating,
        lambdas=(lam_h, lam_a),
        matrix=matrix,
        markets=markets,
        odds={},
        top_scorelines=matrix.top_scorelines(6),
    )


def run(
    bankroll: float = 1000.0,
    kelly_frac: float = 0.25,
    stake_cap: float = 0.01,
    min_ev: float = 0.02,
    use_xg: bool = True,
    rounds: int = 3,
    max_exposure_frac: float = 0.25,
) -> RunResult:
    """Executa o pipeline completo com o dataset local.

    Ordem: fit dos ratings -> lambdas e mercados por jogo -> odds multi-casa
    sintetizadas a partir dos mercados -> sinais. As odds sao geradas a
    partir do proprio modelo com margem + ruido por casa, de modo que os
    edges encontrados sao pequenos e realistas (fruto de desalinho entre
    casas), nao artefatos de formula.
    """
    ds = build_dataset(n_rounds=rounds)
    ratings = fit_ratings(ds.history, ds.teams, home_advantage=ds.home_advantage, use_xg=use_xg)

    result = RunResult(dataset=ds, ratings=ratings, league_goals=ds.league_goals)
    model_by_fixture: dict[str, dict[str, dict[str, float]]] = {}

    blend = 0.5 if use_xg else 0.0
    for fx in ds.fixtures:
        hr = ratings.get(fx.home)
        ar = ratings.get(fx.away)
        if hr is None or ar is None:
            continue
        analysis = analyze_fixture(
            fx, hr, ar, ds.league_goals, ds.home_advantage, attack_blend=blend,
        )
        key = f"{fx.home} vs {fx.away}"
        model_by_fixture[key] = analysis.markets
        result.analyses.append(analysis)

    odds = synthesize_odds(model_by_fixture)
    for a in result.analyses:
        a.odds = odds.get(f"{a.fixture.home} vs {a.fixture.away}", {})

    report = build_report(ds.fixtures, model_by_fixture, odds, bankroll,
                          kelly_frac=kelly_frac, min_ev=min_ev,
                          stake_cap=stake_cap,
                          max_exposure_frac=max_exposure_frac)
    result.report = report
    result.tips = top_tips(report, n=8)
    return result


def bookmaker_list() -> list[str]:
    return list(BOOKMAKERS)
