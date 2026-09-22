"""BETGSN :: staking — alavancagem, risco de ruina e plano faseado.

O que "alavancagem" significa em apostas
----------------------------------------
Nao existe emprestimo. A unica alavancagem possivel e apostar uma fracao
maior da banca. Isso nao multiplica o retorno: troca crescimento por risco
de ruina, de forma mecanica e mensuravel.

O teto e matematico
-------------------
Existe uma fracao otima (Kelly). Abaixo dela, apostar mais aumenta o
crescimento. ACIMA dela, apostar mais DIMINUI o crescimento — e acima do
dobro dela o crescimento fica negativo, ou seja, quanto mais voce aposta,
mais rapido perde. Nao ha "alavancagem" que escape disso.

Crescimento log por aposta:
    g(f) = p*ln(1 + f*(odd-1)) + (1-p)*ln(1 - f)

Com a vantagem validada (ROI +1,60% a odd media 1,21), o maximo fica em
f = 7,62% por aposta e rende ~15,6% ao ano. Acima de ~15% por aposta o
crescimento vira negativo.

Risco de ruina
--------------
Com Kelly fracionado f (fracao do Kelly completo), a probabilidade de
alguma vez cair para uma fracao alfa da banca e:

    P(queda ate alfa) = alfa ** ((2 - f) / f)

Com f = 1 (Kelly completo): P(cair a metade) = 50%. Esse e o preco real
do crescimento maximo.

Estimativa incerta
------------------
O ponto critico: a vantagem estimada tem erro. O intervalo de confianca
de 95% do ROI e [+0,54%, +2,72%]. Apostar Kelly cheio sobre a estimativa
pontual significa apostar ~3x demais se a vantagem verdadeira for a borda
inferior — e isso e ruina garantida.

Por isso a fracao usada deve se basear na estimativa CONSERVADORA, nao na
pontual. O plano padrao deste modulo faz isso.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, replace
from typing import Sequence

# --------------------------------------------------------------------------
# Parametros da vantagem validada (ver value_strategy.py)
# --------------------------------------------------------------------------

#: ROI por aposta, validado em 195.672 jogos (2000-2026).
EDGE_ROI = 0.0160
#: Erro-padrao do ROI (t = +2,97 em 6.748 apostas).
EDGE_SE = 0.0054
#: Odd media da regra (favoritos curtos).
EDGE_ODD = 1.21
#: Apostas por ano que a regra gera (38 competicoes).
BETS_PER_YEAR = 250


# --------------------------------------------------------------------------
# Matematica
# --------------------------------------------------------------------------


def win_prob(roi: float, odd: float) -> float:
    """Probabilidade de acerto implicita num ROI a uma odd.

    roi = p*odd - 1  =>  p = (1 + roi) / odd
    """
    return min(0.99, max(0.01, (1.0 + roi) / odd))


def bet_variance(p: float, odd: float) -> float:
    """Variancia do retorno por unidade apostada."""
    b = odd - 1.0
    mean = p * b - (1.0 - p)
    second = p * b * b + (1.0 - p)
    return max(1e-12, second - mean * mean)


def full_kelly(roi: float = EDGE_ROI, odd: float = EDGE_ODD) -> float:
    """Fracao de Kelly completa: f* = (b*p - q) / b.

    Esta e a solucao EXATA para o crescimento logaritmico, nao a
    aproximacao EV/variancia (que da ~6% a mais e desloca o pico). Reusa
    `engine.kelly_fraction` para existir uma unica formula de Kelly no
    projeto.
    """
    from .engine import kelly_fraction

    if roi <= 0:
        return 0.0
    return kelly_fraction(win_prob(roi, odd), odd)


def growth_rate(fraction: float, roi: float = EDGE_ROI,
                odd: float = EDGE_ODD) -> float:
    """Crescimento log esperado por aposta.

    Negativo quando a fracao passa do dobro do Kelly: apostar mais faz
    perder mais rapido, nao ganhar mais rapido.
    """
    p = win_prob(roi, odd)
    b = odd - 1.0
    win = 1.0 + fraction * b
    lose = 1.0 - fraction
    if win <= 0 or lose <= 0:
        return float("-inf")
    return p * math.log(win) + (1.0 - p) * math.log(lose)


def annual_growth(fraction: float, roi: float = EDGE_ROI,
                  odd: float = EDGE_ODD,
                  bets: int = BETS_PER_YEAR) -> float:
    """Crescimento log anual (multiplique por 100 para %)."""
    return growth_rate(fraction, roi, odd) * bets


def drawdown_probability(kelly_fraction: float, alpha: float) -> float:
    """P(alguma vez cair para `alpha` da banca) com Kelly fracionado.

    kelly_fraction = 1.0 -> Kelly completo (50% de chance de cair a metade).
    kelly_fraction = 0.5 -> meio Kelly (12,5%).
    kelly_fraction = 0.25 -> quarto de Kelly (0,78%).
    """
    if kelly_fraction <= 0:
        return 0.0
    if kelly_fraction >= 2.0:
        return 1.0
    return alpha ** ((2.0 - kelly_fraction) / kelly_fraction)


# --------------------------------------------------------------------------
# Planos
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Phase:
    """Faixa de banca em que uma fracao se aplica."""

    until_multiple: float   # vale enquanto banca < multiple * inicial
    fraction: float         # fracao da banca por aposta


@dataclass(frozen=True)
class StakingPlan:
    """Plano de stake. `phases` sao avaliadas em ordem."""

    name: str
    phases: tuple[Phase, ...]
    drawdown_cut: float = 0.0    # corta a fracao pela metade ao atingir
    stop_loss: float = 0.0       # para tudo abaixo deste multiplo da inicial
    note: str = ""

    def fraction_for(self, bankroll: float, start: float) -> float:
        mult = bankroll / start if start > 0 else 1.0
        for phase in self.phases:
            if mult < phase.until_multiple:
                return phase.fraction
        return self.phases[-1].fraction

    @property
    def max_fraction(self) -> float:
        return max(p.fraction for p in self.phases)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "phases": [asdict(p) for p in self.phases],
            "drawdown_cut": self.drawdown_cut,
            "stop_loss": self.stop_loss,
            "note": self.note,
        }


def flat_plan(name: str, fraction: float) -> StakingPlan:
    return StakingPlan(name=name, phases=(Phase(until_multiple=1e9,
                                                fraction=fraction),))


#: Planos comparados na simulacao.
PLANS: dict[str, StakingPlan] = {
    "conservador": flat_plan("Conservador 1%", 0.01),
    "moderado": flat_plan("Moderado 2%", 0.02),
    "agressivo": flat_plan("Agressivo 4%", 0.04),
    "kelly": flat_plan("Kelly cheio", full_kelly()),
    "sobrekelly": flat_plan("Sobre-Kelly 15% (erro comum)", 0.15),
    "faseado": StakingPlan(
        name="Faseado (agride no inicio, protege depois)",
        phases=(
            Phase(until_multiple=2.0, fraction=0.05),
            Phase(until_multiple=5.0, fraction=0.03),
            Phase(until_multiple=1e9, fraction=0.015),
        ),
        note="5% ate dobrar, 3% ate 5x, 1,5% depois.",
    ),
    "faseado_disjuntor": StakingPlan(
        name="Faseado + disjuntor (RECOMENDADO)",
        phases=(
            Phase(until_multiple=2.0, fraction=0.04),
            Phase(until_multiple=5.0, fraction=0.025),
            Phase(until_multiple=1e9, fraction=0.012),
        ),
        drawdown_cut=0.25,   # cai 25% do topo -> metade da fracao
        stop_loss=0.50,      # cai 50% -> para e reavalia
        note="4% ate dobrar, 2,5% ate 5x, 1,2% depois. "
             "Disjuntor aos 25% de queda; parada total aos 50%.",
    ),
}


# --------------------------------------------------------------------------
# Simulacao
# --------------------------------------------------------------------------


@dataclass
class SimulationResult:
    plan: str
    years: float
    median_multiple: float
    mean_multiple: float
    p_profit: float
    p_double: float
    p_halve: float
    p_ruin: float
    p_stopped: float
    p5: float
    p95: float
    median_annual_pct: float
    max_fraction: float


def simulate(
    plan: StakingPlan,
    *,
    start: float = 1000.0,
    years: float = 3.0,
    bets_per_year: int = BETS_PER_YEAR,
    edge: float = EDGE_ROI,
    edge_se: float = EDGE_SE,
    odd: float = EDGE_ODD,
    n_paths: int = 20000,
    seed: int = 6767,
) -> SimulationResult:
    """Monte Carlo da banca, incluindo a INCERTEZA da vantagem.

    Cada caminho sorteia uma vantagem verdadeira em torno da estimativa.
    E isso que torna a alavancagem perigosa: se a vantagem real for a
    borda inferior do intervalo, apostar pela estimativa pontual vira
    sobre-aposta e a banca quebra.
    """
    rng = random.Random(seed)
    n_bets = int(bets_per_year * years)
    finals: list[float] = []
    ruin = doubled = halved = stopped = 0
    profit = 0

    for _ in range(n_paths):
        path_edge = rng.gauss(edge, edge_se)
        p = win_prob(path_edge, odd)
        bank = start
        peak = start
        hit_ruin = hit_stop = False

        for _ in range(n_bets):
            if plan.stop_loss and bank <= start * plan.stop_loss:
                hit_stop = True
                break
            frac = plan.fraction_for(bank, start)
            if plan.drawdown_cut and peak > 0:
                if (peak - bank) / peak >= plan.drawdown_cut:
                    frac *= 0.5
            stake = bank * frac
            if stake <= 0:
                break
            if rng.random() < p:
                bank += stake * (odd - 1.0)
            else:
                bank -= stake
            if bank > peak:
                peak = bank
            if bank <= start * 0.10:
                hit_ruin = True
                break

        finals.append(bank)
        if hit_ruin:
            ruin += 1
        if hit_stop:
            stopped += 1
        mult = bank / start
        if mult >= 2.0:
            doubled += 1
        if mult <= 0.5:
            halved += 1
        if bank > start:
            profit += 1

    finals.sort()
    n = len(finals)
    median = finals[n // 2]
    mean = sum(finals) / n
    return SimulationResult(
        plan=plan.name,
        years=years,
        median_multiple=median / start,
        mean_multiple=mean / start,
        p_profit=profit / n,
        p_double=doubled / n,
        p_halve=halved / n,
        p_ruin=ruin / n,
        p_stopped=stopped / n,
        p5=finals[int(0.05 * n)] / start,
        p95=finals[int(0.95 * n)] / start,
        median_annual_pct=((median / start) ** (1.0 / years) - 1.0) * 100.0,
        max_fraction=plan.max_fraction,
    )


def kelly_table() -> list[dict]:
    """Curva crescimento x risco para varias fracoes."""
    fk = full_kelly()
    out = []
    for mult in (0.10, 0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00):
        f = fk * mult
        out.append({
            "fraction": f,
            "kelly_multiple": mult,
            "annual_pct": math.expm1(annual_growth(f)) * 100.0,
            "p_halve": drawdown_probability(mult, 0.5),
            "p_ruin": drawdown_probability(mult, 0.10),
        })
    return out


def recommend() -> StakingPlan:
    """Plano recomendado: agressivo no inicio, com disjuntor e parada."""
    return PLANS["faseado_disjuntor"]


# --------------------------------------------------------------------------
# NO BET — a decisao que o sistema tem que saber tomar
# --------------------------------------------------------------------------
#
# Um sistema de apostas que so sabe dizer "aposte" nao e um sistema de
# apostas: e um gerador de apostas. A vantagem estimada tem erro, e quando
# o limite inferior da vantagem nao e positivo a resposta correta e NAO
# APOSTAR. Isso nao e covardia, e a unica leitura honesta do intervalo.

#: Nivel do limite inferior da vantagem (one-sided 95%).
Z_CONSERVATIVE = 1.6448536269514722
#: Fracao do Kelly usada quando a vantagem e confiavel.
DEFAULT_KELLY_FRACTION = 0.25
#: Amostra minima de apostas para a vantagem ser considerada medida.
MIN_EVIDENCE_BETS = 1000
#: Status de evidencia aceitos para apostar dinheiro real.
TRUSTED_EVIDENCE = ("validated", "timestamped", "real")

#: Vocabulario CANONICO de status de evidencia do dominio. Existe para
#: que data/source -> sinal -> decisao -> API falem a MESMA lingua: um
#: status novo precisa entrar aqui (e no Literal da API) por decisao de
#: contrato, nunca ser string solta. "synthetic" e o dataset de
#: demonstracao; "exploratory" e dado real sem timestamp de publicacao.
EVIDENCE_STATUSES: tuple[str, ...] = (
    "exploratory", "validated", "timestamped", "real", "synthetic",
)


def conservative_roi(roi: float, se: float, z: float = Z_CONSERVATIVE) -> float:
    """Limite inferior da vantagem (ROI - z*erro-padrao).

    Dimensionar pela estimativa pontual e o erro que quebra bancas: se a
    vantagem verdadeira for a borda inferior, voce apostou varias vezes
    demais.
    """
    if se < 0:
        raise ValueError("erro-padrao nao pode ser negativo")
    return roi - z * se


@dataclass(frozen=True)
class BetDecision:
    """Decisao explicita de apostar ou nao apostar, com o motivo.

    Contrato (I-13): NO_BET e resultado de primeira classe. Uma decisao
    NO_BET com fracao de banca positiva e uma INCONSISTENCIA, nao um
    estado intermediario — o construtor recusa, em vez de normalizar em
    silencio. Quem precisa da forma normalizada usa `with_zero_stake`.
    """

    action: str                      # "BET" | "NO_BET"
    reason: str
    fraction: float = 0.0
    conservative_roi: float | None = None
    kelly_full: float | None = None
    checks: tuple[tuple[str, bool, str], ...] = ()

    def __post_init__(self) -> None:
        if self.action not in ("BET", "NO_BET"):
            raise ValueError(
                f"action precisa ser BET ou NO_BET, recebi {self.action!r}"
            )
        if self.action == "NO_BET" and self.fraction != 0.0:
            raise ValueError(
                "NO_BET nao cria stake: fraction precisa ser 0.0 "
                f"(recebi {self.fraction})"
            )

    @property
    def should_bet(self) -> bool:
        return self.action == "BET"

    def with_zero_stake(self) -> "BetDecision":
        """A mesma decisao com a fracao normalizada para zero.

        Para chamadores que recebem a decisao de fora (serializacao,
        transporte) e precisam garantir o contrato sem recusar o dado.
        Para NO_BET a fracao JA deveria ser zero; se nao for, isso e
        bug do produtor — normalizar aqui e a rede de seguranca, nao
        a regra.
        """
        if self.action == "NO_BET" and self.fraction != 0.0:
            return replace(self, fraction=0.0)
        return self

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "fraction": self.fraction,
            "conservative_roi": self.conservative_roi,
            "kelly_full": self.kelly_full,
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in self.checks
            ],
        }


def decide_bet(
    roi: float,
    roi_se: float,
    odd: float,
    *,
    evidence_status: str = "exploratory",
    n_bets: int | None = None,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    min_lower_bound: float = 0.0,
    ruin_tolerance: float = 0.10,
    min_bets: int = MIN_EVIDENCE_BETS,
) -> BetDecision:
    """Decide se vale apostar, dado o intervalo de confianca da vantagem.

    NO_BET e a resposta sempre que qualquer verificacao falha. As
    verificacoes sao explicitas e ficam registradas na decisao.
    """
    lower = conservative_roi(roi, roi_se)
    kelly = full_kelly(roi, odd) if roi > 0 else 0.0
    checks: list[tuple[str, bool, str]] = []

    trusted = evidence_status in TRUSTED_EVIDENCE
    checks.append((
        "evidencia_confiavel", trusted,
        f"status '{evidence_status}'"
        + ("" if trusted else ": odds sem timestamp/validacao nao sustentam dinheiro real"),
    ))

    checks.append((
        "limite_inferior_positivo", lower > min_lower_bound,
        f"ROI conservador {lower:+.2%} (exige > {min_lower_bound:+.2%}); "
        f"ponto {roi:+.2%} +/- {Z_CONSERVATIVE:.2f}*{roi_se:.2%}",
    ))

    if n_bets is None:
        checks.append(("amostra_suficiente", True, "numero de apostas nao informado"))
    else:
        checks.append((
            "amostra_suficiente", n_bets >= min_bets,
            f"{n_bets} apostas (minimo {min_bets})",
        ))

    ruin = drawdown_probability(kelly_fraction, 0.5) if kelly > 0 else 0.0
    checks.append((
        "ruina_toleravel", ruin <= ruin_tolerance,
        f"P(cair a metade) {ruin:.2%} (tolerancia {ruin_tolerance:.0%})",
    ))

    failed = [name for name, passed, _ in checks if not passed]
    if failed:
        return BetDecision(
            action="NO_BET",
            reason="falhou: " + ", ".join(failed),
            fraction=0.0,
            conservative_roi=lower,
            kelly_full=kelly,
            checks=tuple(checks),
        )

    fraction = min(kelly * kelly_fraction, 0.05)
    return BetDecision(
        action="BET",
        reason=(
            f"vantagem conservadora {lower:+.2%}, Kelly {kelly:.2%}, "
            f"fracao {fraction:.2%}"
        ),
        fraction=fraction,
        conservative_roi=lower,
        kelly_full=kelly,
        checks=tuple(checks),
    )


#: Plano explicito de NAO apostar. Existe para que "no bet" seja uma opcao
#: de primeira classe, e nao a ausencia de uma opcao.
PLANS["no_bet"] = StakingPlan(
    name="NO BET (sem vantagem confiavel)",
    phases=(Phase(until_multiple=1e9, fraction=0.0),),
    note="Fracao zero. Quando o limite inferior da vantagem nao e positivo, "
         "apostar e pagar a margem da casa por uma vantagem que voce nao "
         "consegue medir.",
)
