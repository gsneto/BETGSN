"""BETGSN :: data — base local de times, historico sintetico e odds de exemplo.

Sem chave de API o app roda com este dataset local (deterministico, seed
fixo) para que o pipeline inteiro seja testavel offline. Com chave, os
providers reais substituem estes dados.

Os ratings-base refletem a hierarquia plausivel da Serie A brasileira. O
historico e gerado por Poisson a partir desses ratings — ou seja, e
"sintetico honesto": o modelo consegue recuperar a estrutura, e serve
para validar o pipeline. NAO use para apostar dinheiro real.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from .model import HistoricalMatch, Fixture

SEED = 6767

# Calendario do dataset local. O historico e sintetico, mas precisa de
# datas coerentes para que o backtest possa ordenar as partidas e cortar
# o contexto no kickoff. Turno e returno da mesma temporada, rodadas
# semanais.
HISTORY_TURNO_START = "2025-01-26"
HISTORY_RETURNO_START = "2025-08-10"
ROUND_INTERVAL_DAYS = 7
DEFAULT_LEAGUE = "Serie A"
DEFAULT_SEASON = "2025"

# (nome, ataque, defesa, forca_relativa) — base para gerar o historico
BASE_TEAMS: list[tuple[str, float, float]] = [
    ("Palmeiras", 1.42, 0.72),
    ("Flamengo", 1.40, 0.78),
    ("Botafogo", 1.34, 0.74),
    ("Atletico-MG", 1.22, 0.88),
    ("Fluminense", 1.10, 0.92),
    ("Internacional", 1.14, 0.90),
    ("Sao Paulo", 1.06, 0.86),
    ("Corinthians", 1.02, 0.94),
    ("Cruzeiro", 1.08, 0.96),
    ("Gremio", 1.04, 0.98),
    ("Bahia", 1.00, 0.95),
    ("Fortaleza", 0.98, 0.97),
    ("Vasco", 0.96, 1.02),
    ("Atletico-PR", 0.94, 1.00),
    ("Bragantino", 0.92, 1.04),
    ("Cuiaba", 0.86, 1.08),
    ("Juventude", 0.84, 1.12),
    ("Criciuma", 0.82, 1.10),
    ("Vitoria", 0.88, 1.16),
    ("Atletico-GO", 0.78, 1.20),
]

LEAGUE_HOME_ADVANTAGE = 1.18
LEAGUE_AVG_GOALS = 2.55

BOOKMAKERS: list[str] = [
    "Bet365", "Betano", "Pinnacle", "Betfair", "Sportingbet",
    "KTO", "Novibet", "EstrelaBet", "Betnacional", "Superbet",
]


def _poisson(rng: random.Random, lam: float) -> int:
    """Amostra Poisson por algoritmo de Knuth."""
    import math
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


@dataclass
class LeagueDataset:
    teams: list[str]
    history: list[HistoricalMatch]
    fixtures: list[Fixture]
    league_goals: float
    home_advantage: float


def build_dataset(n_rounds: int = 1, seed: int = SEED) -> LeagueDataset:
    """Gera um historico (todos contra todos, turno e returno) e fixtures futuras."""
    rng = random.Random(seed)
    teams = [t[0] for t in BASE_TEAMS]
    att = {t[0]: t[1] for t in BASE_TEAMS}
    dfn = {t[0]: t[2] for t in BASE_TEAMS}
    league_half = LEAGUE_AVG_GOALS / 2.0

    history: list[HistoricalMatch] = []

    turno_start = date.fromisoformat(HISTORY_TURNO_START)
    returno_start = date.fromisoformat(HISTORY_RETURNO_START)

    # turno + returno
    for double in range(2):
        order = teams[:]
        base_day = turno_start if double == 0 else returno_start
        for rnd in range(len(teams) - 1):
            kickoff_day = base_day + timedelta(days=ROUND_INTERVAL_DAYS * rnd)
            # horario fixo por rodada: mantem a ordem dentro da rodada e
            # torna o corte point-in-time reproduzivel
            kickoff = f"{kickoff_day.isoformat()} 16:00"
            # rotacao circular (Round-robin)
            for i in range(len(teams) // 2):
                a, b = order[i], order[len(teams) - 1 - i]
                if double == 0:
                    home, away = (a, b) if rnd % 2 == 0 else (b, a)
                else:
                    home, away = (b, a) if rnd % 2 == 0 else (a, b)
                lam_h = att[home] * dfn[away] * league_half * LEAGUE_HOME_ADVANTAGE
                lam_a = att[away] * dfn[home] * league_half
                gh = _poisson(rng, lam_h)
                ga = _poisson(rng, lam_a)
                # xG com ruido em torno do lambda real
                xgh = round(max(0.1, rng.gauss(lam_h, 0.55)), 2)
                xga = round(max(0.1, rng.gauss(lam_a, 0.55)), 2)
                history.append(
                    HistoricalMatch(
                        home=home, away=away, home_goals=gh, away_goals=ga,
                        home_xg=xgh, away_xg=xga,
                        xg_status="ESTIMATED", xg_source="synthetic-demo",
                        home_corners=_poisson(rng, 5.4), away_corners=_poisson(rng, 4.6),
                        home_cards=_poisson(rng, 2.1), away_cards=_poisson(rng, 2.5),
                        home_shots=_poisson(rng, 13.0), away_shots=_poisson(rng, 10.5),
                        weight=1.0 + 0.02 * rnd,
                        kickoff=kickoff, league=DEFAULT_LEAGUE, season=DEFAULT_SEASON,
                    )
                )
            order = [order[0]] + [order[-1]] + order[1:-1]

    fixtures = build_fixtures(teams, n_rounds=n_rounds, seed=seed + 1)
    league_goals = sum(m.home_goals + m.away_goals for m in history) / len(history)
    return LeagueDataset(teams, history, fixtures, round(league_goals, 3), LEAGUE_HOME_ADVANTAGE)


def build_fixtures(teams: list[str], n_rounds: int = 1, seed: int = SEED + 1) -> list[Fixture]:
    """Cria rodadas futuras (sem resultado) para a UI listar jogos do dia."""
    rng = random.Random(seed)
    rounds = [
        ("Rodada 21", "2026-09-19", ["Palmeiras", "Flamengo", "Botafogo", "Corinthians",
                                     "Sao Paulo", "Internacional", "Bahia", "Vasco",
                                     "Cruzeiro", "Gremio"]),
        ("Rodada 22", "2026-09-26", ["Atletico-MG", "Fluminense", "Fortaleza", "Atletico-PR",
                                     "Bragantino", "Cuiaba", "Juventude", "Criciuma",
                                     "Vitoria", "Atletico-GO"]),
        ("Rodada 23", "2026-10-03", ["Palmeiras", "Botafogo", "Flamengo", "Atletico-MG",
                                     "Internacional", "Fluminense", "Corinthians", "Bahia",
                                     "Gremio", "Cruzeiro"]),
    ]
    fixtures: list[Fixture] = []
    pool = teams[:]
    for label, date, head in rounds[:n_rounds]:
        rng.shuffle(pool)
        used: set[str] = set()
        homes = [t for t in head if t in teams]
        rng.shuffle(homes)
        for h in homes[:5]:
            if h in used:
                continue
            away = next((t for t in pool if t != h and t not in used and t not in homes[:5]), None)
            if away is None:
                away = next((t for t in teams if t != h and t not in used), None)
            if away is None:
                continue
            used.add(h)
            used.add(away)
            fixtures.append(Fixture(home=h, away=away, league="Serie A", kickoff=date, round_label=label))
    return fixtures


def synthesize_odds(
    truth_by_match: dict[str, dict[str, dict[str, float]]],
    seed: int = SEED + 2,
    market_error_sigma: float = 0.04,
) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """Gera odds multi-casa a partir das probabilidades do modelo (verdade).

    Duas camadas de imperfeicao, como no mercado real:
      1. ERRO DE MERCADO (comum a todas as casas): o mercado tem sua propria
         estimativa, p_mercado = p_verdade * (1 + eps), eps ~ N(0, sigma).
         E aqui que nasce o valor: quando o mercado subprecifica um
         resultado (eps negativo), o modelo ve edge positivo.
      2. RUIDO POR CASA: cada casa aplica margem propria (Pinnacle fina,
         casas BR gordas), vies de longshot e ruido gaussiano por resultado.

    Sem a camada 1 o modelo seria identico ao mercado e nao haveria edge
    nenhum (so ruido). A camada 1 e a hipotese central de qualquer
    apostador profissional: o mercado erra em alguns resultados.

    truth_by_match: {"Casa vs Fora": {mercado: {resultado: prob}}}
    Devolve: {"Casa vs Fora": {mercado: {casa: {resultado: odd}}}}
    """
    rng = random.Random(seed)
    margins = {
        "Pinnacle": 0.018, "Betfair": 0.020, "Bet365": 0.045,
        "Betano": 0.052, "Sportingbet": 0.058, "KTO": 0.050,
        "Novibet": 0.055, "EstrelaBet": 0.060, "Betnacional": 0.062,
        "Superbet": 0.048,
    }
    out: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for match_key, markets in truth_by_match.items():
        out[match_key] = {}
        for market, probs in markets.items():
            out[match_key][market] = {}
            # erro de mercado: um eps por resultado, comum a todas as casas
            market_view = {
                oc: max(0.004, min(0.996, p * (1.0 + rng.gauss(0.0, market_error_sigma))))
                for oc, p in probs.items()
            }
            for book in BOOKMAKERS:
                margin = margins.get(book, 0.05)
                book_odds: dict[str, float] = {}
                for outcome, p_mkt in market_view.items():
                    p_book = p_mkt
                    if p_mkt < 0.15:                       # vies de longshot
                        p_book *= 1.0 + 0.03
                    p_book = min(0.985, p_book * (1.0 + margin))
                    fair = 1.0 / p_book
                    noise = rng.gauss(0.0, 0.010) * fair  # desalinho entre casas
                    book_odds[outcome] = round(max(1.01, fair + noise), 2)
                out[match_key][market][book] = book_odds
    return out
