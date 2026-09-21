"""BETGSN :: model — forca de ataque/defesa, Poisson bivariado e Dixon-Coles.

O modelo estima gols esperados de cada time a partir de:
  - ratings de ataque e defesa (por time, ajustados por forca da liga);
  - fator casa (home advantage);
  - forma recente (peso por jogo, decaimento temporal);
  - xG recente (expected goals) como sinal mais estavel que gol puro.

A partir dos lambdas (gols esperados) derivamos a matriz de placares e
todas as probabilidades de mercado: 1X2, over/under, BTTS, handicaps e
— extrapolando o mesmo motor — cantos, cartoes e chutes.

Referencias: Dixon & Coles (1997), Maher (1982), Karlis & Ntzoufras (2003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, factorial, log
from typing import Iterable, Sequence

RHO_DEFAULT = -0.05  # correcao Dixon-Coles para placares baixos


# --------------------------------------------------------------------------
# Estruturas
# --------------------------------------------------------------------------


@dataclass
class TeamRating:
    """Rating de um time. Todos os valores sao medias por jogo."""

    name: str
    attack: float          # forca de ataque relativa (1.0 = media da liga)
    defense: float         # fragilidade defensiva (1.0 = media; >1 = pior defesa)
    goals_for: float
    goals_against: float
    xg_for: float | None   # null quando indisponível; nunca gols renomeados
    xg_against: float | None
    corners_for: float
    corners_against: float
    cards_for: float
    cards_against: float
    shots_for: float
    shots_on_target_for: float
    form_points: float     # media de pontos nos ultimos jogos (0..3)
    matches_played: int = 0
    xg_status: str = "UNAVAILABLE"
    xg_source: str | None = None

    @property
    def strength(self) -> float:
        """Indice unico de qualidade: ataque ajustado pela defesa. Usado p/ ranking."""
        return self.attack / max(0.35, self.defense)


@dataclass
class Fixture:
    """Um jogo futuro."""

    home: str
    away: str
    league: str = ""
    kickoff: str = ""
    round_label: str = ""


@dataclass
class ScoreMatrix:
    """Matriz de probabilidades de placar e marginais derivadas."""

    lambdas: tuple[float, float]
    grid: list[list[float]] = field(default_factory=list)
    max_goals: int = 8

    def prob_home_win(self) -> float:
        return sum(self.grid[i][j] for i in range(self.max_goals + 1)
                   for j in range(self.max_goals + 1) if i > j)

    def prob_draw(self) -> float:
        return sum(self.grid[i][i] for i in range(self.max_goals + 1))

    def prob_away_win(self) -> float:
        return sum(self.grid[i][j] for i in range(self.max_goals + 1)
                   for j in range(self.max_goals + 1) if j > i)

    def prob_over(self, line: float) -> float:
        total = 0.0
        for i in range(self.max_goals + 1):
            for j in range(self.max_goals + 1):
                if i + j > line:
                    total += self.grid[i][j]
        return total

    def prob_under(self, line: float) -> float:
        return 1.0 - self.prob_over(line)

    def prob_btts(self) -> float:
        return sum(self.grid[i][j] for i in range(1, self.max_goals + 1)
                   for j in range(1, self.max_goals + 1))

    def prob_home_over(self, line: float) -> float:
        return sum(self.grid[i][j] for i in range(self.max_goals + 1)
                   for j in range(self.max_goals + 1) if i > line)

    def prob_away_over(self, line: float) -> float:
        return sum(self.grid[i][j] for i in range(self.max_goals + 1)
                   for j in range(self.max_goals + 1) if j > line)

    def prob_ah(self, handicap: float, side: str) -> tuple[float, float, float]:
        """Handicap asiatico. Devolve (prob_ganha, prob_push, prob_perde).

        handicap negativo = da vantagem ao time da casa (ex.: -1.5).
        """
        if side == "home":
            margin = lambda i, j: i - j + handicap
        else:
            margin = lambda i, j: j - i + handicap
        win = push = 0.0
        for i in range(self.max_goals + 1):
            for j in range(self.max_goals + 1):
                m = margin(i, j)
                if m > 1e-9:
                    win += self.grid[i][j]
                elif abs(m) <= 1e-9:
                    push += self.grid[i][j]
        return win, push, 1.0 - win - push

    def top_scorelines(self, n: int = 6) -> list[tuple[int, int, float]]:
        flat = [(i, j, self.grid[i][j])
                for i in range(self.max_goals + 1)
                for j in range(self.max_goals + 1)]
        flat.sort(key=lambda t: t[2], reverse=True)
        return flat[:n]


# --------------------------------------------------------------------------
# Ajuste de ratings a partir de historico
# --------------------------------------------------------------------------


@dataclass
class HistoricalMatch:
    home: str
    away: str
    home_goals: int
    away_goals: int
    home_xg: float | None = None
    away_xg: float | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_cards: int | None = None
    away_cards: int | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    weight: float = 1.0
    # Ordenacao temporal. Sem isso nao existe backtest honesto: o motor
    # precisa saber o que estava disponivel ANTES de cada partida.
    # `kickoff` aceita "YYYY-MM-DD HH:MM" ou ISO 8601 completo (com offset
    # ou 'Z'). Vazio = desconhecido, e o backtest recusa partidas sem
    # horario. `timezone` e um nome IANA (ex.: "America/Sao_Paulo") usado
    # apenas quando a string nao traz fuso explicito; vazio = UTC.
    kickoff: str = ""
    timezone: str = ""
    league: str = ""
    season: str = ""
    # Timestamp real de disponibilidade do resultado, quando fornecido.
    result_available_at: str | None = None
    home_xg_against: float | None = None
    away_xg_against: float | None = None
    xg_status: str = "UNAVAILABLE"
    xg_source: str | None = None
    xg_available_at: str | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None


def fit_ratings(
    matches: Iterable[HistoricalMatch],
    teams: Iterable[str],
    home_advantage: float = 1.18,
    decay: float = 0.0,
    use_xg: bool = True,
) -> dict[str, TeamRating]:
    """Estima ataque/defesa por time a partir do historico.

    Modelo multiplicativo simples (estilo Maher):
        lambda_casa = attack_casa * defense_fora * home_advantage
        lambda_fora = attack_fora * defense_casa

    attack ~ 1.0 e defense ~ 1.0 sao a media da liga. Um time com attack
    1.3 cria ~30% mais gols que a media. defense 1.2 concede ~20% mais.

    decay aplica peso exponencial por recencia (0 = sem decaimento).
    """
    matches = list(matches)
    if decay < 0:
        raise ValueError("decay deve ser não negativo")
    if decay and matches:
        from dataclasses import replace
        from .timeutil import parse_kickoff
        latest = max(parse_kickoff(m.kickoff, m.timezone) for m in matches)
        matches = [replace(m, weight=m.weight * exp(-decay * (latest - parse_kickoff(m.kickoff, m.timezone)).total_seconds()/86400)) for m in matches]
    if not use_xg:
        from dataclasses import replace
        matches = [replace(m, home_xg=None, away_xg=None, xg_status="UNAVAILABLE") for m in matches]
    teams = list(teams)
    if not matches or not teams:
        raise ValueError("fit_ratings precisa de jogos e times")

    # Indice time -> [(jogo, e_casa)] na ORDEM ORIGINAL do historico.
    # Sem isso o ajuste e O(times x jogos) por iteracao: num backtest com
    # milhares de partidas e centenas de times isso domina o tempo total.
    # O indice reduz para O(jogos) mantendo a mesma sequencia de somas —
    # logo o resultado e bit-identico ao do laco ingenuo.
    index: dict[str, list[tuple[HistoricalMatch, bool]]] = {}
    for m in matches:
        index.setdefault(m.home, []).append((m, True))
        index.setdefault(m.away, []).append((m, False))

    # medias da liga
    league_home_goals = _wmean([m.home_goals for m in matches], [m.weight for m in matches])
    league_away_goals = _wmean([m.away_goals for m in matches], [m.weight for m in matches])
    league_goals = max(0.6, (league_home_goals + league_away_goals) / 2.0)

    attack = {t: 1.0 for t in teams}
    defense = {t: 1.0 for t in teams}

    # duas passadas de refinamento (ponto fixo)
    for _ in range(6):
        new_attack, new_defense = {}, {}
        for t in teams:
            entries = index.get(t, ())

            # ataque: gols marcados / (defesa do adversario * fator casa)
            num, den = 0.0, 0.0
            for m, is_home in entries:
                if is_home:
                    num += m.weight * (m.home_xg if m.home_xg is not None else m.home_goals)
                    den += m.weight * (defense[m.away] * home_advantage)
                else:
                    num += m.weight * (m.away_xg if m.away_xg is not None else m.away_goals)
                    den += m.weight * defense[m.home]
            if den > 0:
                observed = num / den
                new_attack[t] = _clip(observed / league_goals, 0.45, 2.2)
            else:
                new_attack[t] = 1.0

            # defesa: gols sofridos / (ataque do adversario * fator casa)
            num, den = 0.0, 0.0
            for m, is_home in entries:
                if is_home:
                    num += m.weight * (m.away_xg if m.away_xg is not None else m.away_goals)
                    den += m.weight * attack[m.away]
                else:
                    num += m.weight * (m.home_xg if m.home_xg is not None else m.home_goals)
                    den += m.weight * (attack[m.home] * home_advantage)
            if den > 0:
                observed = num / den
                new_defense[t] = _clip(observed / league_goals, 0.45, 2.2)
            else:
                new_defense[t] = 1.0

        attack, defense = new_attack, new_defense
        # normaliza para media 1.0
        attack = _normalize(attack)
        defense = _normalize(defense)

    # estatisticas por jogo
    stats: dict[str, dict[str, float]] = {t: _blank_stats() for t in teams}
    for m in matches:
        w = m.weight
        for team, side in ((m.home, "home"), (m.away, "away")):
            s = stats[team]
            gf = m.home_goals if side == "home" else m.away_goals
            ga = m.away_goals if side == "home" else m.home_goals
            xgf = m.home_xg if side == "home" else m.away_xg
            xga = m.away_xg if side == "home" else m.home_xg
            cf = m.home_corners if side == "home" else m.away_corners
            ca = m.away_corners if side == "home" else m.home_corners
            kf = m.home_cards if side == "home" else m.away_cards
            ka = m.away_cards if side == "home" else m.home_cards
            sf = m.home_shots if side == "home" else m.away_shots
            s["n"] += w
            s["gf"] += w * gf
            s["ga"] += w * ga
            if xgf is not None:
                s["xg_for"] += w * xgf
                s["xgf_n"] += w
            if xga is not None:
                s["xg_against"] += w * xga
                s["xga_n"] += w
            s["cf"] += w * (cf if cf is not None else 5.0)
            s["ca"] += w * (ca if ca is not None else 5.0)
            s["kf"] += w * (kf if kf is not None else 2.0)
            s["ka"] += w * (ka if ka is not None else 2.0)
            s["sf"] += w * (sf if sf is not None else 12.0)
            s["pts"] += w * (3 if gf > ga else (1 if gf == ga else 0))

    ratings: dict[str, TeamRating] = {}
    for t in teams:
        s = stats[t]
        n = max(1.0, s["n"])
        ratings[t] = TeamRating(
            name=t,
            attack=attack[t],
            defense=defense[t],
            goals_for=s["gf"] / n,
            goals_against=s["ga"] / n,
            xg_for=s["xg_for"] / s["xgf_n"] if s["xgf_n"] else None,
            xg_against=s["xg_against"] / s["xga_n"] if s["xga_n"] else None,
            corners_for=s["cf"] / n,
            corners_against=s["ca"] / n,
            cards_for=s["kf"] / n,
            cards_against=s["ka"] / n,
            shots_for=s["sf"] / n,
            shots_on_target_for=s["sf"] / n * 0.35,
            form_points=s["pts"] / n,
            matches_played=int(n),
            xg_status=("REAL" if s["xgf_n"] and all(
                m.xg_status == "REAL" and m.xg_source for m, _ in index.get(t, ())
                if m.home_xg is not None or m.away_xg is not None
            ) else "ESTIMATED" if s["xgf_n"] else "UNAVAILABLE"),
            xg_source=", ".join(sorted({m.xg_source or "legacy-unverified"
                for m, _ in index.get(t, ()) if m.home_xg is not None or m.away_xg is not None})) or None,
        )
    return ratings


def _blank_stats() -> dict[str, float]:
    return {k: 0.0 for k in ("n", "gf", "ga", "xg_for", "xg_against", "xgf_n", "xga_n", "cf", "ca",
                             "kf", "ka", "sf", "pts")}


def _wmean(values: Sequence[float], weights: Sequence[float]) -> float:
    tw = sum(weights) or 1.0
    return sum(v * w for v, w in zip(values, weights)) / tw


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _normalize(d: dict[str, float]) -> dict[str, float]:
    if not d:
        return d
    m = sum(d.values()) / len(d)
    if m <= 0:
        return d
    return {k: v / m for k, v in d.items()}


# --------------------------------------------------------------------------
# Previsao de um jogo
# --------------------------------------------------------------------------


def expected_goals(
    home: TeamRating,
    away: TeamRating,
    league_goals: float,
    home_advantage: float = 1.18,
    attack_blend: float = 0.5,
) -> tuple[float, float]:
    """Gols esperados (lambda) de cada lado.

    league_goals e o TOTAL medio de gols por jogo na liga (os dois times).
    Decompoe-se em base neutra e vantagem de casa:
        base = league_goals / (1 + home_advantage)
        lambda_casa = att_casa * def_fora * base * home_advantage
        lambda_fora = att_fora * def_casa * base
    Com att=def=1.0 (times medios) a soma dos lambdas reproduz league_goals.

    attack_blend mistura a forca baseada em GOLS com a baseada em xG.
    0.0 = so gols. 1.0 = so xG. 0.5 = meio a meio. xG e mais estavel.
    """
    league_goals = max(1.2, league_goals)
    per_team_avg = league_goals / 2.0          # normaliza goals_for/xg_for
    base = league_goals / (1.0 + home_advantage)  # base neutra por lado

    home_att = _blend(home.attack, home.xg_for, home.goals_for, per_team_avg, attack_blend)
    away_att = _blend(away.attack, away.xg_for, away.goals_for, per_team_avg, attack_blend)

    lam_home = home_att * away.defense * base * home_advantage
    lam_away = away_att * home.defense * base
    return _clip(lam_home, 0.15, 5.5), _clip(lam_away, 0.15, 5.5)


def _blend(attack_rating: float, xg_for: float | None, goals_for: float,
           per_team_avg: float, blend: float) -> float:
    """Converte rating e xG numa unica forca de ataque centrada em 1.0."""
    goals_ratio = goals_for / max(0.4, per_team_avg)
    if xg_for is None:
        blend = 0.0
    xg_ratio = xg_for / max(0.4, per_team_avg) if xg_for is not None else 0.0
    ratio = (1.0 - blend) * goals_ratio + blend * xg_ratio
    return _clip(0.5 * attack_rating + 0.5 * ratio, 0.4, 2.4)


def build_score_matrix(
    lam_home: float,
    lam_away: float,
    max_goals: int = 8,
    rho: float = RHO_DEFAULT,
) -> ScoreMatrix:
    """Matriz de placares por Poisson bivariado com correcao Dixon-Coles.

    rho ajusta placares baixos (0-0, 1-0, 0-1, 1-1), onde Poisson
    independente superestima levemente. rho tipico: -0.05.
    """
    grid: list[list[float]] = []
    for i in range(max_goals + 1):
        row: list[float] = []
        p_home = _poisson(i, lam_home)
        for j in range(max_goals + 1):
            p_away = _poisson(j, lam_away)
            p = p_home * p_away * _dc_tau(i, j, lam_home, lam_away, rho)
            row.append(p)
        grid.append(row)

    total = sum(sum(row) for row in grid) or 1.0
    grid = [[p / total for p in row] for row in grid]
    return ScoreMatrix((lam_home, lam_away), grid, max_goals)


def _poisson(k: int, lam: float) -> float:
    return exp(-lam) * (lam ** k) / factorial(k)


def _dc_tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    """Fator de correcao Dixon-Coles para placares baixos."""
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


# --------------------------------------------------------------------------
# Mercados secundarios (cantos, cartoes, chutes) via Poisson de contagem
# --------------------------------------------------------------------------


def expected_corners(home: TeamRating, away: TeamRating) -> tuple[float, float]:
    """Cantos esperados por lado. Media das taxas ofensiva e defensiva."""
    lam_home = (home.corners_for + away.corners_against) / 2.0
    lam_away = (away.corners_for + home.corners_against) / 2.0
    return max(1.5, lam_home), max(1.5, lam_away)


def expected_cards(home: TeamRating, away: TeamRating, ref_strictness: float = 1.0) -> tuple[float, float]:
    """Cartoes esperados por lado, ajustados por rigor do arbitro."""
    lam_home = (home.cards_for + away.cards_against) / 2.0 * ref_strictness
    lam_away = (away.cards_for + home.cards_against) / 2.0 * ref_strictness
    return max(0.8, lam_home), max(0.8, lam_away)


def count_prob_over(lam_a: float, lam_b: float, line: float, max_n: int = 30) -> float:
    """P(total de duas Poisson independentes > line). Para cantos/cartoes."""
    total = 0.0
    for i in range(max_n + 1):
        pa = _poisson(i, lam_a)
        if pa < 1e-12:
            continue
        for j in range(max_n + 1):
            if i + j > line:
                total += pa * _poisson(j, lam_b)
    return min(1.0, max(0.0, total))


def count_prob_team_over(lam: float, line: float, max_n: int = 30) -> float:
    """P(time especifico faz mais que `line` contagens)."""
    total = sum(_poisson(i, lam) for i in range(int(line) + 1, max_n + 1))
    return min(1.0, max(0.0, total))
