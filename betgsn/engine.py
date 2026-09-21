"""BETGSN :: engine — matematica de odds, valor, Kelly e consenso de mercado.

Convencoes:
  - Probabilidades: float em [0,1].
  - Odds: decimais (1.85 = retorno 1.85 por 1 apostado).
  - Dinheiro: float na moeda da banca.

Nenhuma funcao aqui promete lucro. Elas medem edge a partir das
probabilidades informadas (modelo) e das odds observadas (mercado).
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Mapping, Sequence

EPS = 1e-9


# --------------------------------------------------------------------------
# Conversoes
# --------------------------------------------------------------------------


def implied_prob(odd: float) -> float:
    """Probabilidade implicita bruta (com margem) de uma odd decimal."""
    if odd <= 1.0:
        raise ValueError(f"odd decimal precisa ser > 1.0, recebi {odd!r}")
    return 1.0 / odd


def decimal_from_prob(prob: float) -> float:
    """Odd justa (sem margem) para uma probabilidade. Inverse de implied_prob."""
    p = max(EPS, min(1.0 - EPS, prob))
    return 1.0 / p


def overround(odds: Sequence[float]) -> float:
    """Soma das implicitas de um mercado completo. >1 = margem da casa."""
    return sum(implied_prob(o) for o in odds)


def vig(odds: Sequence[float]) -> float:
    """Margem da casa em fracao. 0.048 = 4.8% de vig."""
    return overround(odds) - 1.0


def fair_probs(odds: Sequence[float]) -> list[float]:
    """Remove o vig proporcionalmente. Somam 1. Metodo multiplicativo."""
    raw = [implied_prob(o) for o in odds]
    total = sum(raw)
    return [p / total for p in raw]


def fair_odds(odds: Sequence[float]) -> list[float]:
    """Odds justas (sem margem) correspondentes a um mercado."""
    return [decimal_from_prob(p) for p in fair_probs(odds)]


def expected_value(prob: float, odd: float) -> float:
    """Retorno esperado por unidade apostada.

    EV=0.04 significa +4% por unidade no longo prazo se ``prob`` estiver
    calibrada. Nao significa lucro garantido nesta aposta.
    """
    if not 0.0 <= prob <= 1.0:
        raise ValueError("probabilidade precisa estar entre 0 e 1")
    if odd <= 1.0:
        raise ValueError("odd precisa ser maior que 1.0")
    return prob * odd - 1.0


# --------------------------------------------------------------------------
# Consenso de mercado multi-casa
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsensusLine:
    outcome: str
    best_odd: float
    best_book: str
    median_odd: float
    n_books: int
    fair_odd: float          # odd justa pelo consenso (mediana sem vig)
    model_prob: float        # probabilidade do modelo
    market_prob: float       # probabilidade justa pelo consenso
    edge: float              # model_prob - market_prob
    ev: float                # EV por unidade na melhor odd
    kelly: float             # fracao de Kelly completa
    book_spread: float       # dispersao das odds entre casas


def market_consensus(
    odds_by_book: Mapping[str, Mapping[str, float]],
) -> dict[str, tuple[float, str, float, int, float]]:
    """De {casa: {resultado: odd}} -> {resultado: (melhor_odd, casa, mediana, n, dispersao)}.

    A mediana entre casas e mais robusta que a media: uma casa com odd
    errada (ou atrasada) nao arrasta o consenso.
    """
    by_outcome: dict[str, list[tuple[str, float]]] = {}
    for book, outcomes in odds_by_book.items():
        for outcome, odd in outcomes.items():
            if odd and odd > 1.0:
                by_outcome.setdefault(outcome, []).append((book, odd))

    consensus: dict[str, tuple[float, float, str, float, int, float]] = {}
    for outcome, pairs in by_outcome.items():
        pairs_sorted = sorted(pairs, key=lambda p: p[1])
        values = [o for _, o in pairs_sorted]
        best_book, best_odd = pairs_sorted[-1]
        med = _median(values)
        spread = pstdev(values) if len(values) > 1 else 0.0
        consensus[outcome] = (best_odd, best_book, med, len(values), spread)
    return consensus


def market_groups(outcomes: Sequence[str]) -> list[tuple[str, ...]]:
    """Agrupa linhas completas; dupla chance é um grupo sobreposto."""
    available = set(outcomes)
    groups = []
    seen = set()
    for outcome in outcomes:
        if outcome in seen:
            continue
        if outcome in {"1", "X", "2"}:
            group = ("1", "X", "2")
        elif outcome in {"1X", "12", "X2"}:
            group = ("1X", "12", "X2")
        elif "Over " in outcome or "Under " in outcome:
            other = outcome.replace("Over ", "Under ") if "Over " in outcome else outcome.replace("Under ", "Over ")
            group = (outcome, other)
        elif outcome.startswith("AH "):
            _, side, line = outcome.split()
            other_side = "Fora" if side == "Casa" else "Casa"
            other = next((o for o in outcomes
                          if o.startswith(f"AH {other_side} ")
                          and float(o.split()[2]) == -float(line)), "")
            group = (outcome, other)
        elif outcome in ("BTTS Sim", "BTTS Nao"):
            group = ("BTTS Sim", "BTTS Nao")
        elif outcome in ("DNB Casa", "DNB Fora"):
            group = ("DNB Casa", "DNB Fora")
        else:
            group = tuple(outcomes)
        if set(group) <= available:
            groups.append(group)
        seen.update(group)
    return groups


def consensus_fair_probs(
    odds_by_book: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    """De-vig por linha completa. Dupla chance tem soma de probabilidades 2."""
    med = {o: row[2] for o, row in market_consensus(odds_by_book).items()}
    fair = {}
    for group in market_groups(list(med)):
        if len(group) < 2:
            continue
        raw = {o: implied_prob(med[o]) for o in group}
        target = 2 if set(group) == {"1X", "12", "X2"} else 1
        total = sum(raw.values())
        fair.update({o: target * p / total for o, p in raw.items()})
    return fair


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def evaluate_market(
    odds_by_book: Mapping[str, Mapping[str, float]],
    model_probs: Mapping[str, float],
) -> list[ConsensusLine]:
    """Cruza o consenso multi-casa com o modelo e devolve uma linha por resultado.

    market_prob vem das odds MEDIANAS sem vig (consenso de mercado).
    ev usa a MELHOR odd disponivel (onde voce de fato apostaria).
    edge = model_prob - market_prob (probabilidade, nao dinheiro).
    """
    cons = market_consensus(odds_by_book)
    # vig removal sobre as medianas do consenso
    fair_market_prob = consensus_fair_probs(odds_by_book)

    lines: list[ConsensusLine] = []
    for outcome, (best_odd, best_book, med, n, spread) in cons.items():
        mp = model_probs.get(outcome)
        if mp is None:
            continue
        if outcome not in fair_market_prob:
            continue
        mkt_p = fair_market_prob.get(outcome, implied_prob(med))
        ev = mp * best_odd - 1.0
        b = best_odd - 1.0
        kelly = max(0.0, (b * mp - (1.0 - mp)) / b) if b > 0 else 0.0
        lines.append(
            ConsensusLine(
                outcome=outcome,
                best_odd=best_odd,
                best_book=best_book,
                median_odd=med,
                n_books=n,
                fair_odd=decimal_from_prob(mkt_p),
                model_prob=mp,
                market_prob=mkt_p,
                edge=mp - mkt_p,
                ev=ev,
                kelly=kelly,
                book_spread=spread,
            )
        )
    return sorted(lines, key=lambda l: l.ev, reverse=True)


# --------------------------------------------------------------------------
# Kelly e stake
# --------------------------------------------------------------------------


def kelly_fraction(prob: float, odd: float) -> float:
    """f* = (b*p - q)/b, b = odd-1. Kelly completo. Fracione sempre."""
    b = odd - 1.0
    if b <= 0.0:
        return 0.0
    return max(0.0, (b * prob - (1.0 - prob)) / b)


def stake(bankroll: float, prob: float, odd: float, fraction: float = 0.25,
          cap: float = 0.05) -> float:
    """Stake com Kelly fracionado e teto por aposta (cap fracao da banca).

    O cap existe porque a estimativa de prob do modelo tem erro; Kelly
    completo com prob superestimada leva a ruina. cap=0.05 -> maximo 5%.
    """
    f = kelly_fraction(prob, odd) * fraction
    f = min(f, cap)
    return round(bankroll * f, 2)


@dataclass(frozen=True)
class StakePlan:
    """Plano percentual de uma aposta individual.

    ``full_kelly`` e a fracao matematica sem limite. ``recommended_pct`` e
    a fracao efetivamente usada depois de Kelly fracionado e teto de risco.
    ``expected_profit`` e EV monetario, nao promessa de resultado nessa
    aposta. O crescimento composto acontece porque a proxima stake usa a
    banca atualizada, nao a banca inicial.
    """

    bankroll: float
    probability: float
    odd: float
    ev: float
    full_kelly: float
    fractional_kelly: float
    recommended_pct: float
    stake: float
    expected_profit: float
    gross_profit_if_win: float
    loss_if_lose: float


def stake_plan(
    bankroll: float,
    prob: float,
    odd: float,
    fraction: float = 0.25,
    cap: float = 0.01,
) -> StakePlan:
    """Calcula stake progressiva pela banca atual.

    Metodo padrao conservador: quarter-Kelly limitado a ``cap`` da banca.
    Ex.: banca 1.000 e cap 1% -> nunca arrisca mais de 10 nessa aposta;
    banca 1.500 -> teto passa a 15. A stake cresce junto com a banca.
    """
    if bankroll <= 0:
        raise ValueError("banca precisa ser maior que zero")
    if not 0.0 <= prob <= 1.0:
        raise ValueError("probabilidade precisa estar entre 0 e 1")
    if odd <= 1.0:
        raise ValueError("odd precisa ser maior que 1.0")
    full = kelly_fraction(prob, odd)
    fractional = full * max(0.0, fraction)
    recommended_pct = min(max(0.0, cap), fractional)
    amount = round(bankroll * recommended_pct, 2)
    ev = expected_value(prob, odd)
    return StakePlan(
        bankroll=round(bankroll, 2),
        probability=prob,
        odd=odd,
        ev=ev,
        full_kelly=full,
        fractional_kelly=fractional,
        recommended_pct=recommended_pct,
        stake=amount,
        expected_profit=round(amount * ev, 2),
        gross_profit_if_win=round(amount * (odd - 1.0), 2),
        loss_if_lose=amount,
    )


# --------------------------------------------------------------------------
# Arbitragem
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ArbLeg:
    outcome: str
    book: str
    odd: float
    stake: float
    payout: float


@dataclass(frozen=True)
class ArbResult:
    arbitrage: bool
    margin: float
    legs: tuple[ArbLeg, ...] = ()


def scan_arbitrage(odds_by_book: Mapping[str, Mapping[str, float]],
                   total_stake: float = 1000.0) -> ArbResult:
    """Arb = melhor odd de cada resultado com soma das implicitas < 1."""
    best: dict[str, tuple[str, float]] = {}
    for book, outcomes in odds_by_book.items():
        for outcome, odd in outcomes.items():
            if outcome not in best or odd > best[outcome][1]:
                best[outcome] = (book, odd)
    if len(best) < 2:
        return ArbResult(False, 0.0)
    total_implied = sum(implied_prob(o) for _, o in best.values())
    margin = 1.0 - total_implied
    if margin <= 0.0:
        return ArbResult(False, margin)
    legs = []
    for outcome, (book, odd) in best.items():
        s = round(total_stake * implied_prob(odd) / total_implied, 2)
        legs.append(ArbLeg(outcome, book, odd, s, round(s * odd, 2)))
    return ArbResult(True, margin, tuple(legs))


# --------------------------------------------------------------------------
# Multiplas
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    label: str
    odd: float
    prob: float


@dataclass(frozen=True)
class MultipleResult:
    combined_odd: float
    combined_prob: float
    ev: float
    has_edge: bool


def analyse_multiple(legs: Sequence[Leg]) -> MultipleResult:
    """Multipla com hipotese de independencia. Quebra em pernas correlacionadas."""
    if not legs:
        raise ValueError("multipla precisa de ao menos 1 perna")
    odd = 1.0
    prob = 1.0
    for leg in legs:
        odd *= leg.odd
        prob *= leg.prob
    ev = prob * odd - 1.0
    return MultipleResult(round(odd, 4), prob, ev, ev > 0.0)
