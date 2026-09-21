"""BETGSN :: portfolio.policy — apostar ou NAO APOSTAR, no nivel do portfolio.

`portfolio.simulation` mede o que acontece se voce apostar. Este modulo
responde a pergunta anterior: voce DEVE apostar? Quando a evidencia nao
sustenta a aposta, `NO_BET` e a resposta correta e fica registrada como
decisao — nao como ausencia de decisao.

Verificacoes (todas explicitas, todas bloqueantes):

1. `evidencia_confiavel` — odds com timestamp/validacao. Preco de CSV sem
   hora de publicacao nao prova que estava disponivel na decisao.
2. `carteira_nao_vazia` — sem apostas nao ha portfolio.
3. `ev_conservador_positivo` — EV medio apos haircut de probabilidade.
4. `crescimento_positivo` — crescimento log esperado da simulacao > 0.
5. `ruina_toleravel` — P(ruina) dentro da tolerancia.
6. `drawdown_toleravel` — P(queda de 50%) dentro da tolerancia.
7. `exposicao_dentro_dos_limites` — limites por jogo, liga e total.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .risk import ExposureLimits, check_exposure
from .simulation import PortfolioBet, apply_haircut, simulate_portfolio

#: Status de evidencia aceitos para dinheiro real.
TRUSTED_EVIDENCE = ("validated", "timestamped", "real")


@dataclass(frozen=True)
class PortfolioDecision:
    """Decisao do portfolio, com cada verificacao auditavel."""

    action: str                      # "BET" | "NO_BET"
    reason: str
    fraction_of_bankroll: float
    expected_log_growth: float
    probability_of_ruin: float
    probability_of_50pct_drawdown: float
    checks: tuple[tuple[str, bool, str], ...] = ()

    @property
    def should_bet(self) -> bool:
        return self.action == "BET"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "fraction_of_bankroll": self.fraction_of_bankroll,
            "expected_log_growth": self.expected_log_growth,
            "probability_of_ruin": self.probability_of_ruin,
            "probability_of_50pct_drawdown": self.probability_of_50pct_drawdown,
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in self.checks
            ],
        }


def decide_portfolio(
    bets: Sequence[PortfolioBet],
    *,
    bankroll: float = 1000.0,
    evidence_status: str = "exploratory",
    min_conservative_ev: float = 0.0,
    max_ruin: float = 0.05,
    max_drawdown: float = 0.50,
    haircut: float = 0.02,
    runs: int = 5000,
    seed: int = 6767,
    limits: ExposureLimits | None = None,
) -> PortfolioDecision:
    """Decide se o portfolio deve ser apostado. NO_BET e sempre uma opcao."""
    checks: list[tuple[str, bool, str]] = []

    trusted = evidence_status in TRUSTED_EVIDENCE
    checks.append((
        "evidencia_confiavel", trusted,
        f"status '{evidence_status}'"
        + ("" if trusted else ": preco sem timestamp de publicacao"),
    ))

    checks.append((
        "carteira_nao_vazia", len(bets) > 0,
        f"{len(bets)} aposta(s)",
    ))

    if not bets:
        return _decision(
            "NO_BET", checks, 0.0, 0.0, 0.0, 0.0,
            reason="falhou: carteira_nao_vazia",
        )

    effective_ev = [
        apply_haircut(b.probability, haircut) * b.odd - 1.0 for b in bets
    ]
    mean_ev = sum(effective_ev) / len(effective_ev)
    checks.append((
        "ev_conservador_positivo", mean_ev > min_conservative_ev,
        f"EV medio apos haircut {mean_ev:+.2%} (exige > {min_conservative_ev:+.2%})",
    ))

    total_staked = sum(b.stake for b in bets)
    simulation = simulate_portfolio(
        bets, initial_bankroll=bankroll, runs=runs, haircut=haircut, seed=seed,
    )
    checks.append((
        "crescimento_positivo", simulation.expected_log_growth > 0,
        f"crescimento log esperado {simulation.expected_log_growth:+.5f}",
    ))
    checks.append((
        "ruina_toleravel", simulation.probability_of_ruin <= max_ruin,
        f"P(ruina) {simulation.probability_of_ruin:.2%} (tolerancia {max_ruin:.0%})",
    ))
    checks.append((
        "drawdown_toleravel",
        simulation.probability_of_50pct_drawdown <= max_drawdown,
        f"P(queda 50%) {simulation.probability_of_50pct_drawdown:.2%} "
        f"(tolerancia {max_drawdown:.0%})",
    ))

    exposure = check_exposure(
        [
            {"match": b.match or b.label, "league": b.league or "-",
             "stake": b.stake, "type": "single"}
            for b in bets
        ],
        bankroll,
        limits or ExposureLimits(),
    )
    checks.append((
        "exposicao_dentro_dos_limites", exposure.within_limits,
        "dentro dos limites" if exposure.within_limits
        else "; ".join(exposure.violations[:3]),
    ))

    failed = [name for name, passed, _ in checks if not passed]
    fraction = total_staked / bankroll if bankroll > 0 else 0.0
    if failed:
        return _decision(
            "NO_BET", checks, fraction, simulation.expected_log_growth,
            simulation.probability_of_ruin, simulation.probability_of_50pct_drawdown,
            reason="falhou: " + ", ".join(failed),
        )
    return _decision(
        "BET", checks, fraction, simulation.expected_log_growth,
        simulation.probability_of_ruin, simulation.probability_of_50pct_drawdown,
        reason=f"portfolio dentro de todos os limites; {fraction:.2%} da banca",
    )


def _decision(action, checks, fraction, growth, ruin, dd50, reason=None) -> PortfolioDecision:
    if reason is None:
        reason = "todas as verificacoes passaram"
    return PortfolioDecision(
        action=action,
        reason=reason,
        fraction_of_bankroll=fraction,
        expected_log_growth=growth,
        probability_of_ruin=ruin,
        probability_of_50pct_drawdown=dd50,
        checks=tuple(checks),
    )
