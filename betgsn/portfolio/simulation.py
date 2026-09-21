"""BETGSN :: portfolio.simulation — Monte Carlo de portfolio e erro de modelo.

Simula o portfolio inteiro, nao uma aposta isolada. Duas perguntas distintas:

1. Dado que as probabilidades do modelo estao CORRETAS, qual a distribuicao
   de resultados? (variancia pura)
2. E se elas estiverem ERRADAS? (risco de modelo)

A segunda e a que quebra bancas. Um modelo superconfiante gera EV positivo
no papel e negativo na realidade. Por isso o haircut e obrigatorio, nao
opcional.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

DEFAULT_RUNS = 20_000
DEFAULT_SEED = 6767

#: Cortes aplicados a probabilidade do modelo no teste de robustez.
#: 0.0 = confia plenamente. 0.10 = assume que o modelo erra 10% para cima.
DEFAULT_HAIRCUTS: tuple[float, ...] = (0.0, 0.01, 0.02, 0.03, 0.05, 0.10)

@dataclass(frozen=True)
class PortfolioBet:
    """Uma aposta do portfolio."""

    label: str
    probability: float
    odd: float
    stake: float
    match: str = ""
    league: str = ""
    market: str = ""
    correlation_group: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probabilidade precisa estar entre 0 e 1")
        if self.odd <= 1.0:
            raise ValueError("odd precisa ser maior que 1.0")
        if self.stake < 0:
            raise ValueError("stake nao pode ser negativa")

    @property
    def ev(self) -> float:
        return self.probability * self.odd - 1.0

@dataclass
class PortfolioSimulation:
    """Distribuicao de resultados do portfolio."""

    runs: int
    initial_bankroll: float
    n_bets: int
    total_staked: float
    mean_final_bankroll: float
    median_final_bankroll: float
    p05: float
    p25: float
    p75: float
    p95: float
    probability_of_profit: float
    probability_of_loss: float
    probability_of_ruin: float
    probability_of_25pct_drawdown: float
    probability_of_50pct_drawdown: float
    median_max_drawdown: float
    expected_log_growth: float
    haircut: float = 0.0
    n_unique_matches: int = 0
    effective_number_of_bets: float = 0.0

    def to_dict(self) -> dict:
        return {
            "runs": self.runs,
            "initial_bankroll": self.initial_bankroll,
            "n_bets": self.n_bets,
            "total_staked": round(self.total_staked, 2),
            "mean_final_bankroll": round(self.mean_final_bankroll, 2),
            "median_final_bankroll": round(self.median_final_bankroll, 2),
            "p05": round(self.p05, 2),
            "p25": round(self.p25, 2),
            "p75": round(self.p75, 2),
            "p95": round(self.p95, 2),
            "probability_of_profit": round(self.probability_of_profit, 4),
            "probability_of_loss": round(self.probability_of_loss, 4),
            "probability_of_ruin": round(self.probability_of_ruin, 4),
            "probability_of_25pct_drawdown": round(self.probability_of_25pct_drawdown, 4),
            "probability_of_50pct_drawdown": round(self.probability_of_50pct_drawdown, 4),
            "median_max_drawdown": round(self.median_max_drawdown, 4),
            "expected_log_growth": round(self.expected_log_growth, 6),
            "haircut": self.haircut,
            "n_unique_matches": self.n_unique_matches,
            "effective_number_of_bets": round(self.effective_number_of_bets, 2),
        }

def effective_number_of_bets(bets: Sequence[PortfolioBet]) -> float:
    """Numero efetivo de apostas independentes (inverso de Herfindahl).

    10 apostas iguais no MESMO jogo nao sao 10 apostas: sao ~1 aposta com
    10x a stake. Esta metrica expoe concentracao que o total de apostas
    esconde.
    """
    if not bets:
        return 0.0
    exposure: dict[str, float] = {}
    for bet in bets:
        key = bet.correlation_group or bet.match or bet.label
        exposure[key] = exposure.get(key, 0.0) + bet.stake
    total = sum(exposure.values())
    if total <= 0:
        return 0.0
    weights = [value / total for value in exposure.values()]
    herfindahl = sum(w * w for w in weights)
    return 1.0 / herfindahl if herfindahl > 0 else 0.0

def apply_haircut(probability: float, haircut: float) -> float:
    """Reduz a probabilidade multiplicativamente. haircut=0.05 -> -5% relativo."""
    if not 0.0 <= haircut < 1.0:
        raise ValueError("haircut precisa estar em [0, 1)")
    return max(0.0, min(1.0, probability * (1.0 - haircut)))

def simulate_portfolio(
    bets: Sequence[PortfolioBet],
    initial_bankroll: float = 1000.0,
    runs: int = DEFAULT_RUNS,
    haircut: float = 0.0,
    seed: int = DEFAULT_SEED,
    ruin_threshold: float = 0.10,
) -> PortfolioSimulation:
    """Monte Carlo do portfolio inteiro.

    Apostas do mesmo `correlation_group` compartilham o sorteio: e assim que
    correlacao intra-jogo entra na simulacao sem inventar matriz de
    covariancia. Independencia entre grupos e o baseline declarado.
    """
    if not bets:
        raise ValueError("portfolio precisa de ao menos 1 aposta")
    if initial_bankroll <= 0:
        raise ValueError("banca inicial precisa ser positiva")
    if runs <= 0:
        raise ValueError("runs precisa ser positivo")

    rng = random.Random(seed)
    total_staked = sum(b.stake for b in bets)
    finals: list[float] = []
    drawdowns: list[float] = []
    ruined = 0
    dd25 = 0
    dd50 = 0

    effective_probs = [apply_haircut(b.probability, haircut) for b in bets]
    groups = [b.correlation_group for b in bets]

    for _ in range(runs):
        draws: dict[str, float] = {}
        bankroll = initial_bankroll
        peak = initial_bankroll
        max_dd = 0.0
        for bet, prob, group in zip(bets, effective_probs, groups):
            if group:
                if group not in draws:
                    draws[group] = rng.random()
                u = draws[group]
            else:
                u = rng.random()
            if u < prob:
                bankroll += bet.stake * (bet.odd - 1.0)
            else:
                bankroll -= bet.stake
            peak = max(peak, bankroll)
            if peak > 0:
                max_dd = max(max_dd, (peak - bankroll) / peak)
        finals.append(bankroll)
        drawdowns.append(max_dd)
        if bankroll <= initial_bankroll * ruin_threshold:
            ruined += 1
        if max_dd >= 0.25:
            dd25 += 1
        if max_dd >= 0.50:
            dd50 += 1

    finals.sort()
    growth = [
        math.log(max(1e-9, value) / initial_bankroll) for value in finals
    ]
    return PortfolioSimulation(
        runs=runs,
        initial_bankroll=initial_bankroll,
        n_bets=len(bets),
        total_staked=total_staked,
        mean_final_bankroll=statistics.fmean(finals),
        median_final_bankroll=statistics.median(finals),
        p05=_percentile(finals, 0.05),
        p25=_percentile(finals, 0.25),
        p75=_percentile(finals, 0.75),
        p95=_percentile(finals, 0.95),
        probability_of_profit=sum(1 for f in finals if f > initial_bankroll) / runs,
        probability_of_loss=sum(1 for f in finals if f < initial_bankroll) / runs,
        probability_of_ruin=ruined / runs,
        probability_of_25pct_drawdown=dd25 / runs,
        probability_of_50pct_drawdown=dd50 / runs,
        median_max_drawdown=statistics.median(drawdowns),
        expected_log_growth=statistics.fmean(growth),
        haircut=haircut,
        n_unique_matches=len({b.match for b in bets if b.match}),
        effective_number_of_bets=effective_number_of_bets(bets),
    )

@dataclass
class RobustnessReport:
    """Como o portfolio se comporta se o modelo estiver errado.

    ROBUSTNESS_SCORE e definido explicitamente como a fracao dos haircuts
    testados em que o crescimento logaritmico esperado permanece positivo.
    Score 1.0 = sobrevive a todos os cortes testados. Score 0.0 = ja perde
    dinheiro mesmo sem corte. Nao ha ponderacao escondida.
    """

    scenarios: list[PortfolioSimulation] = field(default_factory=list)
    haircuts: tuple[float, ...] = ()

    @property
    def robustness_score(self) -> float:
        if not self.scenarios:
            return 0.0
        survived = sum(1 for s in self.scenarios if s.expected_log_growth > 0)
        return survived / len(self.scenarios)

    @property
    def breakeven_haircut(self) -> Optional[float]:
        """Menor haircut em que o crescimento esperado vira negativo."""
        for scenario in sorted(self.scenarios, key=lambda s: s.haircut):
            if scenario.expected_log_growth <= 0:
                return scenario.haircut
        return None

    def to_dict(self) -> dict:
        return {
            "robustness_score": round(self.robustness_score, 4),
            "robustness_definition": (
                "fracao dos haircuts testados com expected_log_growth > 0"
            ),
            "breakeven_haircut": self.breakeven_haircut,
            "haircuts_tested": list(self.haircuts),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

def model_error_simulation(
    bets: Sequence[PortfolioBet],
    initial_bankroll: float = 1000.0,
    runs: int = 5_000,
    haircuts: Sequence[float] = DEFAULT_HAIRCUTS,
    seed: int = DEFAULT_SEED,
) -> RobustnessReport:
    """Roda o portfolio sob varios niveis de erro do modelo."""
    scenarios = [
        simulate_portfolio(
            bets, initial_bankroll=initial_bankroll, runs=runs,
            haircut=haircut, seed=seed,
        )
        for haircut in haircuts
    ]
    return RobustnessReport(scenarios=scenarios, haircuts=tuple(haircuts))

def apply_slippage(odd: float, slippage: float) -> float:
    """Preco realmente executado. slippage=0.02 -> recebe 2% menos de odd."""
    if not 0.0 <= slippage < 1.0:
        raise ValueError("slippage precisa estar em [0, 1)")
    degraded = odd * (1.0 - slippage)
    return max(1.0001, degraded)

def slippage_scenarios(
    bets: Sequence[PortfolioBet],
    slippages: Sequence[float] = (0.0, 0.01, 0.02, 0.05),
    initial_bankroll: float = 1000.0,
    runs: int = 5_000,
    seed: int = DEFAULT_SEED,
) -> list[dict]:
    """Impacto de degradacao de preco na execucao real."""
    results = []
    for slip in slippages:
        degraded = [
            PortfolioBet(
                label=b.label, probability=b.probability,
                odd=apply_slippage(b.odd, slip), stake=b.stake,
                match=b.match, league=b.league, market=b.market,
                correlation_group=b.correlation_group,
            )
            for b in bets
        ]
        sim = simulate_portfolio(
            degraded, initial_bankroll=initial_bankroll, runs=runs, seed=seed,
        )
        results.append({
            "slippage": slip,
            "mean_ev": round(statistics.fmean([b.ev for b in degraded]), 6),
            "expected_log_growth": round(sim.expected_log_growth, 6),
            "median_final_bankroll": round(sim.median_final_bankroll, 2),
            "probability_of_profit": round(sim.probability_of_profit, 4),
        })
    return results

def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(q * (len(sorted_values) - 1))
    return sorted_values[idx]
