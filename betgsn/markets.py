"""BETGSN :: markets — geracao de probabilidades por mercado a partir do modelo.

Cada funcao devolve um dicionario {rotulo_resultado: probabilidade_modelo}.
Os rotulos seguem o padrao do mercado para casar com as odds das casas.

Convencao de rotulos:
  1X2      -> "1" (casa), "X" (empate), "2" (fora)
  O/U      -> "Over 2.5", "Under 2.5", "Over 1.5", ...
  BTTS     -> "BTTS Sim", "BTTS Nao"
  Cantos   -> "Cantos Over 9.5", "Cantos Under 9.5", "Casa Cantos Over 5.5", ...
  Cartoes  -> "Cartoes Over 3.5", ...
  Handicap -> "AH Casa -1.5", "AH Fora +1.5"
  Chutes   -> "Chutes Over 21.5", ...
"""

from __future__ import annotations

from .model import (
    ScoreMatrix,
    TeamRating,
    count_prob_over,
    count_prob_team_over,
    expected_cards,
    expected_corners,
)


def market_1x2(m: ScoreMatrix) -> dict[str, float]:
    return {"1": m.prob_home_win(), "X": m.prob_draw(), "2": m.prob_away_win()}


def market_double_chance(m: ScoreMatrix) -> dict[str, float]:
    return {
        "1X": m.prob_home_win() + m.prob_draw(),
        "12": m.prob_home_win() + m.prob_away_win(),
        "X2": m.prob_draw() + m.prob_away_win(),
    }


def market_dnb(m: ScoreMatrix) -> dict[str, float]:
    """Draw No Bet: aposta devolvida em empate."""
    h = m.prob_home_win()
    a = m.prob_away_win()
    total = h + a
    if total < 1e-9:
        return {"DNB Casa": 0.5, "DNB Fora": 0.5}
    return {"DNB Casa": h / total, "DNB Fora": a / total}


def market_totals(m: ScoreMatrix, lines: tuple[float, ...] = (1.5, 2.5, 3.5)) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in lines:
        over = m.prob_over(line)
        out[f"Over {line}"] = over
        out[f"Under {line}"] = 1.0 - over
    return out


def market_btts(m: ScoreMatrix) -> dict[str, float]:
    p = m.prob_btts()
    return {"BTTS Sim": p, "BTTS Nao": 1.0 - p}


def market_team_totals(m: ScoreMatrix, lines: tuple[float, ...] = (0.5, 1.5)) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in lines:
        ph = m.prob_home_over(line)
        pa = m.prob_away_over(line)
        out[f"Casa Over {line}"] = ph
        out[f"Casa Under {line}"] = 1.0 - ph
        out[f"Fora Over {line}"] = pa
        out[f"Fora Under {line}"] = 1.0 - pa
    return out


def market_asian_handicap(m: ScoreMatrix,
                          lines: tuple[float, ...] = (-1.5, -1.0, -0.5, 0.5, 1.0, 1.5)) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in lines:
        win_h, push_h, lose_h = m.prob_ah(line, "home")
        # prob de "nao perder" tratando push como devolucao
        eff_h = win_h / max(1e-9, (win_h + lose_h))
        out[f"AH Casa {line:+g}"] = eff_h
        out[f"AH Fora {-line:+g}"] = 1.0 - eff_h
    return out


def market_corners(home: TeamRating, away: TeamRating,
                   total_lines: tuple[float, ...] = (8.5, 9.5, 10.5),
                   team_lines: tuple[float, ...] = (4.5, 5.5)) -> dict[str, float]:
    lam_h, lam_a = expected_corners(home, away)
    out: dict[str, float] = {}
    for line in total_lines:
        over = count_prob_over(lam_h, lam_a, line)
        out[f"Cantos Over {line}"] = over
        out[f"Cantos Under {line}"] = 1.0 - over
    for line in team_lines:
        ph = count_prob_team_over(lam_h, line)
        pa = count_prob_team_over(lam_a, line)
        out[f"Casa Cantos Over {line}"] = ph
        out[f"Casa Cantos Under {line}"] = 1.0 - ph
        out[f"Fora Cantos Over {line}"] = pa
        out[f"Fora Cantos Under {line}"] = 1.0 - pa
    return out


def market_cards(home: TeamRating, away: TeamRating,
                 total_lines: tuple[float, ...] = (2.5, 3.5, 4.5),
                 ref_strictness: float = 1.0) -> dict[str, float]:
    lam_h, lam_a = expected_cards(home, away, ref_strictness)
    out: dict[str, float] = {}
    for line in total_lines:
        over = count_prob_over(lam_h, lam_a, line)
        out[f"Cartoes Over {line}"] = over
        out[f"Cartoes Under {line}"] = 1.0 - over
    return out


def market_shots(home: TeamRating, away: TeamRating,
                 total_lines: tuple[float, ...] = (19.5, 21.5, 23.5)) -> dict[str, float]:
    lam_h = home.shots_for + away.shots_for
    lam_a = lam_h  # Poisson unico aproximado para o total do jogo
    out: dict[str, float] = {}
    for line in total_lines:
        over = count_prob_over(lam_h, lam_a, line * 2.0, max_n=80)
        out[f"Chutes Over {line}"] = over
        out[f"Chutes Under {line}"] = 1.0 - over
    return out


def all_markets(
    m: ScoreMatrix,
    home: TeamRating,
    away: TeamRating,
    ref_strictness: float = 1.0,
    include: tuple[str, ...] = ("1x2", "dc", "dnb", "ou", "btts", "tt", "ah", "corners", "cards"),
) -> dict[str, dict[str, float]]:
    """Agrupa todos os mercados pedidos num unico dicionario por mercado."""
    result: dict[str, dict[str, float]] = {}
    if "1x2" in include:
        result["Resultado Final (1X2)"] = market_1x2(m)
    if "dc" in include:
        result["Dupla Chance"] = market_double_chance(m)
    if "dnb" in include:
        result["Draw No Bet"] = market_dnb(m)
    if "ou" in include:
        result["Total de Gols"] = market_totals(m)
    if "btts" in include:
        result["Ambas Marcam"] = market_btts(m)
    if "tt" in include:
        result["Total por Time"] = market_team_totals(m)
    if "ah" in include:
        result["Handicap Asiatico"] = market_asian_handicap(m)
    if "corners" in include:
        result["Escanteios"] = market_corners(home, away)
    if "cards" in include:
        result["Cartoes"] = market_cards(home, away, ref_strictness=ref_strictness)
    return result


# --------------------------------------------------------------------------
# Grupos de mercado: fonte unica de verdade para UI, API e backtest.
# O par e (chave usada em `include`, rotulo exibido). Mantido em sincronia
# com `all_markets` — se um mercado novo for adicionado la, entra aqui.
# --------------------------------------------------------------------------

MARKET_GROUPS: tuple[tuple[str, str], ...] = (
    ("1x2", "Resultado Final (1X2)"),
    ("dc", "Dupla Chance"),
    ("dnb", "Draw No Bet"),
    ("ou", "Total de Gols"),
    ("btts", "Ambas Marcam"),
    ("tt", "Total por Time"),
    ("ah", "Handicap Asiatico"),
    ("corners", "Escanteios"),
    ("cards", "Cartoes"),
)

ALL_MARKET_KEYS: tuple[str, ...] = tuple(key for key, _ in MARKET_GROUPS)
MARKET_LABELS: dict[str, str] = dict(MARKET_GROUPS)
LABEL_TO_KEY: dict[str, str] = {label: key for key, label in MARKET_GROUPS}


def validate_market_keys(keys: tuple[str, ...]) -> tuple[str, ...]:
    """Normaliza e valida as chaves de mercado. Levanta erro explicito.

    Impede que um typo de configuracao vire silenciosamente um backtest
    vazio (pior tipo de bug: parece que rodou).
    """
    if not keys:
        return ALL_MARKET_KEYS
    unknown = [k for k in keys if k not in MARKET_LABELS]
    if unknown:
        raise ValueError(
            f"mercado(s) desconhecido(s): {unknown}. "
            f"validos: {list(ALL_MARKET_KEYS)}"
        )
    return tuple(k for k in ALL_MARKET_KEYS if k in set(keys))
