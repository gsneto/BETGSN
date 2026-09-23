"""BETGSN :: probability_contract — separação estrutural MARKET vs MODEL.

POR QUE ESTE MODULO EXISTE
--------------------------
A arquitetura conceitual do BETGSN é:

    MARKET -> MODEL -> MODEL VS MARKET -> LINE SHOPPING -> EXECUTION
           -> CLV -> PROMOTION GATE -> NO BET

Cada etapa precisa saber QUAL fonte de probabilidade sustenta o número
que consome. Misturar MARKET_RAW com MARKET_FAIR com MODEL já foi a
origem de "melhoras" que eram artefato de população. Este contrato
tipado torna a origem PARTE DO TIPO: um ProbabilityBreakdown carrega
as três fontes em campos distintos, com invariantes verificadas.

    MARKET_RAW  p = 1/odd (probabilidade implícita da odd escolhida)
    MARKET_FAIR p = de-vig do consenso de medianas (probabilidade
                  "justa" do mercado)
    MODEL       p = probabilidade do modelo (ratings congelados no
                  instante da decisão)

Invariantes:
    - cada probabilidade em (0, 1);
    - implied_probability == 1/chosen_odd quando ambos existem;
    - model_minus_market só existe quando as duas pontas existem;
    - NENHUM campo é preenchido a partir de outro: market nunca vira
      model e vice-versa (a construção é explícita, campo a campo).

O contrato não decide nada: é evidência estruturada para auditoria,
API e relatórios. O promotion gate continua consumindo as vias próprias
(cached_oos_evidence / prospective_clv_evidence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


def _check_probability(value: float | None, name: str) -> None:
    if value is None:
        return
    if not (0.0 < value < 1.0):
        raise ValueError(
            f"{name} precisa estar em (0, 1) exclusivo, recebi {value!r}"
        )


@dataclass(frozen=True)
class ProbabilityBreakdown:
    """As três fontes de probabilidade de UMA linha, separadas por tipo.

    Campos:
        market_raw_probability  p = 1/odd da odd escolhida (com margem).
        market_fair_probability p = de-vig do consenso de medianas.
        model_probability       p = modelo congelado no instante T.
        implied_probability     1/chosen_odd (espelho de market_raw; o
                                campo existe para o contrato ser
                                auto-verificável).
        chosen_odd / bookmaker / timestamp  proveniência do preço que
                                sustenta market_raw.
        origin                  de onde o breakdown foi construído —
                                rastreabilidade declarada, nunca deduzida.
    """

    market_raw_probability: Optional[float] = None
    market_fair_probability: Optional[float] = None
    model_probability: Optional[float] = None
    implied_probability: Optional[float] = None
    chosen_odd: Optional[float] = None
    bookmaker: Optional[str] = None
    timestamp: Optional[str] = None
    origin: str = ""

    def __post_init__(self) -> None:
        _check_probability(self.market_raw_probability, "market_raw")
        _check_probability(self.market_fair_probability, "market_fair")
        _check_probability(self.model_probability, "model")
        _check_probability(self.implied_probability, "implied")
        if self.chosen_odd is not None and self.chosen_odd <= 1.0:
            raise ValueError(
                f"chosen_odd precisa ser > 1.0, recebi {self.chosen_odd!r}"
            )
        if (
            self.implied_probability is not None
            and self.chosen_odd is not None
            and abs(self.implied_probability - 1.0 / self.chosen_odd) > 1e-9
        ):
            raise ValueError(
                "implied_probability diverge de 1/chosen_odd: o contrato "
                "carrega a MESMA odd que sustenta market_raw"
            )

    # ------------------------------------------------- diferenças tipadas

    @property
    def model_minus_market(self) -> Optional[float]:
        """MODEL - MARKET_RAW. None quando falta qualquer ponta.

        Zero é um valor legítimo (modelo e mercado concordam) — None é
        'pergunta sem resposta'. O contrato nunca fabrica zero.
        """
        if self.model_probability is None or self.market_raw_probability is None:
            return None
        return self.model_probability - self.market_raw_probability

    @property
    def model_minus_fair(self) -> Optional[float]:
        """MODEL - MARKET_FAIR. None quando falta qualquer ponta."""
        if self.model_probability is None or self.market_fair_probability is None:
            return None
        return self.model_probability - self.market_fair_probability

    @property
    def fair_minus_raw(self) -> Optional[float]:
        """MARKET_FAIR - MARKET_RAW: o tamanho da margem na odd escolhida."""
        if self.market_fair_probability is None or self.market_raw_probability is None:
            return None
        return self.market_fair_probability - self.market_raw_probability

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_raw_probability": self.market_raw_probability,
            "market_fair_probability": self.market_fair_probability,
            "model_probability": self.model_probability,
            "implied_probability": self.implied_probability,
            "model_minus_market": self.model_minus_market,
            "model_minus_fair": self.model_minus_fair,
            "fair_minus_raw": self.fair_minus_raw,
            "chosen_odd": self.chosen_odd,
            "bookmaker": self.bookmaker,
            "timestamp": self.timestamp,
            "origin": self.origin,
        }


def breakdown_from_bet(bet: Mapping[str, Any]) -> ProbabilityBreakdown:
    """Breakdown a partir de uma linha canônica (`collect_bets`).

    CAMPO A CAMPO, sem cruzamento de fontes:
        market_raw  = 1/odd (a odd melhor usada pela regra);
        market_fair = campo `fair` (de-vig do consenso — calculado na
                      coleta a partir das medianas, nunca do modelo);
        model       = campo `p_model` QUANDO EXISTIR (harness de
                      modelo); sem modelo no dicionário, fica None —
                      nunca preenchido com market.

    `origin` declara a fonte: 'canonical_bet' (+ modelo quando houver).
    """
    odd = bet.get("odd")
    fair = bet.get("fair")
    p_model = bet.get("p_model")
    chosen = float(odd) if odd is not None else None
    origin = "canonical_bet"
    if p_model is not None:
        origin += "+model"
    return ProbabilityBreakdown(
        market_raw_probability=(1.0 / chosen if chosen else None),
        market_fair_probability=(
            float(fair) if fair is not None and float(fair) > 0 else None),
        model_probability=(
            float(p_model) if p_model is not None else None),
        implied_probability=(1.0 / chosen if chosen else None),
        chosen_odd=chosen,
        timestamp=str(bet.get("d")) or None,
        origin=origin,
    )


def breakdown_from_signal(signal: Mapping[str, Any]) -> ProbabilityBreakdown:
    """Breakdown a partir de um sinal (API/`signals.CoreSignal`-like).

    Campos esperados: best_odd, fair_odd, model_prob, best_book,
    kickoff. market_raw = 1/best_odd; market_fair = 1/fair_odd quando
    disponível; model = model_prob. Nenhuma fonte substitui outra.
    """
    best_odd = signal.get("best_odd")
    fair_odd = signal.get("fair_odd")
    model_prob = signal.get("model_prob")
    chosen = float(best_odd) if best_odd is not None else None
    fair = float(fair_odd) if fair_odd is not None else None
    return ProbabilityBreakdown(
        market_raw_probability=(1.0 / chosen if chosen else None),
        market_fair_probability=(1.0 / fair if fair and fair > 1.0 else None),
        model_probability=(
            float(model_prob) if model_prob is not None else None),
        implied_probability=(1.0 / chosen if chosen else None),
        chosen_odd=chosen,
        bookmaker=signal.get("best_book"),
        timestamp=signal.get("kickoff"),
        origin="signal",
    )
