"""BETGSN :: signals — gerador de sinais e dicas.

Recebe as probabilidades do modelo e as odds reais de varias casas e
devolve uma lista ranqueada de sinais. Cada sinal carrega:
  - o jogo, o mercado e o resultado sugerido;
  - a odd (melhor casa) e a casa;
  - a probabilidade do modelo e a do mercado (sem vig);
  - o edge, o EV e o stake sugerido por Kelly fracionado;
  - um nivel de confianca (forca do sinal).

O ranking usa EV como criterio principal e a confianca como desempate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Sequence

from .engine import ConsensusLine, evaluate_market, stake_plan
from .model import Fixture, ScoreMatrix, TeamRating
from .timeutil import now_utc
from .config import production_thresholds
from .production_policy import ProductionGate, finite_number, selection_reasons


class Confidence(str, Enum):
    FORTE = "FORTE"
    MEDIA = "MEDIA"
    FRACA = "FRACA"
    DESCARTE = "DESCARTE"


# limiares de EV (fracao) para classificar o sinal
EV_FORTE = 0.08
EV_MEDIA = 0.045
EV_FRACA = 0.02
MIN_BOOKS = 3          # exige consenso de pelo menos N casas
MAX_SPREAD = production_thresholds()['max_spread']


@dataclass
class Signal:
    match: str
    kickoff: str
    market: str
    outcome: str
    best_odd: float
    best_book: str
    median_odd: float
    n_books: int
    model_prob: float
    market_prob: float
    edge: float
    ev: float
    kelly: float
    stake: float
    confidence: Confidence
    bankroll: float
    rationale: str = ""

    @property
    def fair_odd(self) -> float:
        return 1.0 / max(1e-9, self.market_prob)

    def tip(self) -> str:
        """Dica legivel no formato pedido: ACAO no MERCADO, TIME, DIA."""
        return (
            f"{self.outcome} | {self.market} | odd {self.best_odd:.2f} em {self.best_book} "
            f"| prob modelo {self.model_prob * 100:.1f}% vs mercado {self.market_prob * 100:.1f}% "
            f"| EV {self.ev * 100:+.1f}% | stake {self.stake:.2f} "
            f"({self.stake_pct * 100:.2f}% banca)"
        )

    @property
    def stake_pct(self) -> float:
        return self.stake / self.bankroll if self.bankroll > 0 else 0.0

    @property
    def expected_profit(self) -> float:
        return self.stake * self.ev

    @property
    def expected_profit_pct(self) -> float:
        return self.expected_profit / self.bankroll if self.bankroll > 0 else 0.0

    @property
    def gross_profit_if_win(self) -> float:
        return self.stake * (self.best_odd - 1.0)

    @property
    def loss_if_lose(self) -> float:
        return self.stake


@dataclass
class SignalReport:
    generated_at: str
    bankroll: float
    signals: list[Signal] = field(default_factory=list)
    exposure_scaled_by: float = 1.0

    def by_confidence(self, level: Confidence) -> list[Signal]:
        return [s for s in self.signals if s.confidence == level]

    def total_exposure(self) -> float:
        return sum(s.stake for s in self.signals)

    @property
    def total_exposure_pct(self) -> float:
        return self.total_exposure() / self.bankroll if self.bankroll > 0 else 0.0

    @property
    def expected_profit(self) -> float:
        return sum(s.expected_profit for s in self.signals)

    @property
    def expected_profit_pct(self) -> float:
        return self.expected_profit / self.bankroll if self.bankroll > 0 else 0.0

    @property
    def worst_case_loss(self) -> float:
        return self.total_exposure()

    @property
    def gross_profit_if_all_win(self) -> float:
        return sum(s.gross_profit_if_win for s in self.signals)


def classify(ev: float, n_books: int, spread: float, *, edge: float | None = None,
             market: str = '', production_gate: ProductionGate | None = None,
             quote_valid: bool = False) -> Confidence:
    """FORTE requer política operacional completa; legado permanece pesquisa."""
    if type(n_books) is not int or n_books < MIN_BOOKS:
        return Confidence.DESCARTE
    if not finite_number(ev) or not finite_number(spread) or not 0 <= spread <= MAX_SPREAD:
        return Confidence.DESCARTE
    if not selection_reasons(edge=edge, ev=ev, spread=spread, books_count=n_books,
                             market=market, gate=production_gate, quote_valid=quote_valid):
        return Confidence.FORTE
    if ev >= EV_MEDIA:
        return Confidence.MEDIA
    if ev >= EV_FRACA:
        return Confidence.FRACA
    return Confidence.DESCARTE


def _rationale(line: ConsensusLine, n_books: int) -> str:
    parts: list[str] = []
    if line.edge > 0.03:
        parts.append(f"modelo ve {line.edge * 100:.1f}pp acima do mercado")
    elif line.edge < -0.03:
        parts.append(f"mercado precifica {abs(line.edge) * 100:.1f}pp acima do modelo")
    if line.book_spread < 0.08:
        parts.append("casas concordam (baixa dispersao)")
    elif line.book_spread > 0.18:
        parts.append("casas divergem (odd pode estar atrasada)")
    if line.n_books >= 8:
        parts.append("consenso amplo")
    return "; ".join(parts) if parts else "edge dentro da margem de erro"


def generate_signals(
    fixture: Fixture,
    model_markets: dict[str, dict[str, float]],
    odds_by_market: dict[str, dict[str, dict[str, float]]],
    bankroll: float,
    kelly_frac: float = 0.25,
    stake_cap: float = 0.05,
    min_ev: float = EV_FRACA,
) -> list[Signal]:
    """Gera os sinais de um jogo cruzando modelo x odds multi-casa.

    model_markets: {mercado: {resultado: prob_modelo}}
    odds_by_market: {mercado: {casa: {resultado: odd}}}
    """
    match = f"{fixture.home} vs {fixture.away}"
    signals: list[Signal] = []

    for market, model_probs in model_markets.items():
        books = odds_by_market.get(market)
        if not books:
            continue
        lines = evaluate_market(books, model_probs)
        for line in lines:
            if line.ev < min_ev:
                continue
            conf = classify(line.ev, line.n_books, line.book_spread)
            if conf == Confidence.DESCARTE:
                continue
            signals.append(
                Signal(
                    match=match,
                    kickoff=fixture.kickoff,
                    market=market,
                    outcome=line.outcome,
                    best_odd=line.best_odd,
                    best_book=line.best_book,
                    median_odd=line.median_odd,
                    n_books=line.n_books,
                    model_prob=line.model_prob,
                    market_prob=line.market_prob,
                    edge=line.edge,
                    ev=line.ev,
                    kelly=line.kelly,
                    stake=stake_plan(bankroll, line.model_prob, line.best_odd,
                                     fraction=kelly_frac, cap=stake_cap).stake,
                    confidence=conf,
                    bankroll=bankroll,
                    rationale=_rationale(line, line.n_books),
                )
            )
    return sorted(signals, key=lambda s: (s.ev, s.confidence.value), reverse=True)


def build_report(
    fixtures: list[Fixture],
    model_by_fixture: dict[str, dict[str, dict[str, float]]],
    odds_by_fixture: dict[str, dict[str, dict[str, dict[str, float]]]],
    bankroll: float,
    kelly_frac: float = 0.25,
    min_ev: float = EV_FRACA,
    stake_cap: float = 0.01,
    max_exposure_frac: float = 0.25,
) -> SignalReport:
    """Consolida os sinais de todos os jogos num relatorio unico.

    max_exposure_frac limita a exposicao TOTAL: se a soma das stakes de
    todos os sinais passar dessa fracao da banca, as stakes sao escaladas
    proporcionalmente. Sem esse teto, muitos sinais pequenos somam mais que
    a banca inteira (ex.: 167 sinais -> 262% da banca), o que e ruina certa.
    """
    # generated_at e um INSTANTE (o momento da previsao): carimbo canonico
    # em UTC, nunca hora local sem offset que depois seria lida como UTC.
    report = SignalReport(generated_at=now_utc(), bankroll=bankroll)
    for fx in fixtures:
        key = f"{fx.home} vs {fx.away}"
        model_markets = model_by_fixture.get(key)
        odds_markets = odds_by_fixture.get(key)
        if not model_markets or not odds_markets:
            continue
        report.signals.extend(
            generate_signals(fx, model_markets, odds_markets, bankroll,
                             kelly_frac=kelly_frac, stake_cap=stake_cap,
                             min_ev=min_ev)
        )
    report.signals.sort(key=lambda s: s.ev, reverse=True)
    _cap_total_exposure(report, max_exposure_frac)
    return report


def scale_stakes_to_cap(stakes: Sequence[float], cap: float) -> tuple[list[float], float]:
    """Escala stakes proporcionalmente para que a soma nao passe de `cap`.

    Devolve (stakes_escaladas, fator). Fator 1.0 significa que nada foi
    escalado. A escala proporcional preserva o ranking relativo entre os
    sinais; cortar o excedente de forma arbitraria distorceria a proporcao.

    Extraido de `_cap_total_exposure` para que o Scanner e o Backtest usem
    exatamente a mesma regra de teto de exposicao.
    """
    stakes = list(stakes)
    total = sum(stakes)
    if cap <= 0 or total <= 0 or total <= cap:
        return stakes, 1.0
    scale = cap / total
    scaled = [round(s * scale, 2) for s in stakes]
    # a soma dos arredondamentos pode exceder o teto por centavos; corrige
    # o residual de tras para frente, sem deixar stake negativo
    residual = round(sum(scaled) - cap, 2)
    if residual > 0:
        for i in range(len(scaled) - 1, -1, -1):
            reduction = min(residual, scaled[i])
            scaled[i] = round(scaled[i] - reduction, 2)
            residual = round(residual - reduction, 2)
            if residual <= 0:
                break
    return scaled, scale


def _cap_total_exposure(report: SignalReport, frac: float) -> None:
    """Escala as stakes para que a exposicao total nao passe de frac da banca."""
    scaled, scale = scale_stakes_to_cap(
        [s.stake for s in report.signals], report.bankroll * frac,
    )
    for s, value in zip(report.signals, scaled):
        s.stake = value
    report.exposure_scaled_by = scale


def gate_report(report: SignalReport, *, bet_allowed: bool) -> SignalReport:
    """Devolve o relatorio com stakes zeradas quando a decisao NAO autoriza
    aposta. NAO muta o original: o snapshot cacheado preserva as stakes
    computadas para quando a evidencia virar GREEN.

    Este e o ponto unico de gating: qualquer serializer (API, adapter,
    CLI) que apresente `stake`, `stake_pct`, `expected_profit` ou
    exposicao deve derivar do relatorio gated — nunca das stakes cruas.
    """
    if bet_allowed:
        return report
    return SignalReport(
        generated_at=report.generated_at,
        bankroll=report.bankroll,
        signals=[replace(s, stake=0.0) for s in report.signals],
        exposure_scaled_by=0.0,
    )


def top_tips(report: SignalReport, n: int = 5, *,
             bet_allowed: bool = True) -> list[str]:
    """As N melhores dicas em texto, formato direto pro bilhete.

    `bet_allowed=False` (decisao global NAO autoriza aposta) devolve lista
    VAZIA: a superficie nao pode carregar linguagem operacional ("APOSTAR")
    quando a decisao e NO_BET. A decisao e unica e upstream; aqui apenas
    respeitamos.
    """
    if not bet_allowed:
        return []
    tips: list[str] = []
    for i, s in enumerate(report.signals[:n], 1):
        tips.append(
            f"{i}. [{s.confidence.value}] {s.match} ({s.kickoff}) -> "
            f"APOSTAR {s.outcome} em {s.market} @ {s.best_odd:.2f} ({s.best_book}). "
            f"Prob {s.model_prob * 100:.1f}% | EV {s.ev * 100:+.1f}% | "
            f"stake {s.stake:.2f} ({s.stake_pct * 100:.2f}% banca) | "
            f"lucro esp. {s.expected_profit:+.2f}."
        )
    return tips
