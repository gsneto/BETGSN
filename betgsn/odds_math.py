"""BETGSN :: odds_math — probabilidade implicita, overround, de-vig e fair odds.

Convencoes (as mesmas de `engine.py`):
  - odds decimais (1.85 = retorno 1.85 por 1 apostado);
  - probabilidades em [0, 1].

O que este modulo adiciona a `engine.py`
-----------------------------------------
`engine.fair_probs` remove a margem de forma PROPORCIONAL (metodo
multiplicativo). Isso e o suficiente na maioria dos casos, mas subestima a
probabilidade de favoritos em mercados com margem alta. Aqui ficam metodos
adicionais:

  - multiplicative: p_i / sum(p)          (o mesmo do engine);
  - shin:            modelo de Shin (1993), que modela a margem como
                     resultado de apostadores informados;
  - power:           p_i^k normalizado, corrige assimetria da margem.

Nenhum metodo "descobre" a probabilidade verdadeira: os tres sao modelos.
Por isso `devig` devolve tambem o metodo usado e se o mercado estava
COMPLETO — sem o conjunto completo de resultados, remover vig e uma
aproximacao, nao um fato.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Mapping, Sequence

from .engine import decimal_from_prob, implied_prob, overround

#: Metodos de de-vig suportados.
DEVIG_METHODS = ("multiplicative", "shin", "power")

#: Abaixo disso consideramos que nao ha margem a remover.
_MIN_OVERROUND = 1.0 + 1e-9


@dataclass(frozen=True)
class MarketProbabilities:
    """Resultado do de-vig de um mercado.

    `fair_probabilities` soma 1 (ou 2, para mercados de dupla chance).
    `overround` e a soma BRUTA das implicitas, antes de remover margem.
    `complete` diz se o conjunto de resultados cobre o mercado inteiro:
    sem isso, o de-vig e uma normalizacao local, nao uma probabilidade.
    """

    fair_probabilities: dict[str, float]
    overround: float
    method: str
    complete: bool

    @property
    def margin(self) -> float:
        """Margem da casa em fracao (0.048 = 4,8%)."""
        return self.overround - 1.0

    def to_dict(self) -> dict:
        return {
            "fair_probabilities": {
                k: round(v, 8) for k, v in self.fair_probabilities.items()
            },
            "overround": round(self.overround, 6),
            "margin": round(self.margin, 6),
            "method": self.method,
            "complete": self.complete,
        }


def _as_list(odds: Mapping[str, float] | Sequence[float]) -> tuple[list[str], list[float]]:
    if isinstance(odds, Mapping):
        labels = list(odds.keys())
        values = [float(odds[k]) for k in labels]
    else:
        values = [float(o) for o in odds]
        labels = [str(i) for i in range(len(values))]
    if not values:
        raise ValueError("mercado sem odds")
    for label, odd in zip(labels, values):
        if not isfinite(odd) or odd <= 1.0:
            raise ValueError(
                f"odd decimal precisa ser finita e > 1.0 em {label!r}, recebi {odd!r}"
            )
    return labels, values


def implied_probabilities(odds: Mapping[str, float]) -> dict[str, float]:
    """Probabilidade implicita bruta (com margem) de cada resultado."""
    return {label: implied_prob(odd) for label, odd in odds.items()}


def market_overround(odds: Sequence[float] | Mapping[str, float]) -> float:
    """Soma das implicitas. > 1 indica margem da casa."""
    values = list(odds.values()) if isinstance(odds, Mapping) else list(odds)
    return overround(values)


def devig_multiplicative(raw: Sequence[float]) -> list[float]:
    """De-vig proporcional: p_i / sum(p). Estavel e sempre valido."""
    total = sum(raw)
    if total <= 0.0:
        raise ValueError("soma das implicitas precisa ser positiva")
    return [p / total for p in raw]


def shin_fair_probs(raw: Sequence[float]) -> list[float]:
    """De-vig pelo modelo de Shin (1993).

    p_i(z) = ( sqrt(z^2 + 4(1-z) * p_i^2 / S) - z ) / (2(1-z)),  S = sum(p_i)

    z e a fracao estimada de apostadores informados e e encontrado por
    bisseccao para que sum(p_i) = 1. Se a margem e nula, devolve as
    implicitas normalizadas (nao ha o que remover).

    Referencia: Shin, H. S. (1993), "Measuring the Incidence of Insider
    Trading in a Market for State-Contingent Claims".
    """
    s = sum(raw)
    if s <= _MIN_OVERROUND:
        return devig_multiplicative(raw)

    def probs_at(z: float) -> list[float]:
        if z <= 0.0:
            return [p / sqrt(s) for p in raw]
        denominator = 2.0 * (1.0 - z)
        out: list[float] = []
        for p in raw:
            inner = z * z + 4.0 * (1.0 - z) * (p * p) / s
            out.append((sqrt(inner) - z) / denominator)
        return out

    lo, hi = 0.0, 1.0 - 1e-9
    if sum(probs_at(hi)) > 1.0:
        # Mercado degenerado (margem absurda): o modelo nao tem raiz.
        return devig_multiplicative(raw)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        total = sum(probs_at(mid))
        if total > 1.0:
            lo = mid
        else:
            hi = mid
    result = probs_at((lo + hi) / 2.0)
    total = sum(result)
    if total <= 0.0:
        return devig_multiplicative(raw)
    return [p / total for p in result]


def power_fair_probs(raw: Sequence[float], tolerance: float = 1e-12) -> list[float]:
    """De-vig por potencia: p_i^k, com k escolhido para sum = 1.

    k > 1 encolhe as probabilidades altas (tira mais margem dos favoritos),
    k < 1 faz o contrario. Resolve por bisseccao em k.
    """
    s = sum(raw)
    if s <= _MIN_OVERROUND:
        return devig_multiplicative(raw)

    def total_at(k: float) -> float:
        return sum(p ** k for p in raw)

    lo, hi = 0.5, 4.0
    # total_at e decrescente em k; procura o intervalo que contem 1.
    while total_at(hi) > 1.0 and hi < 64.0:
        hi *= 2.0
    while total_at(lo) < 1.0 and lo > 1e-6:
        lo /= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if total_at(mid) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tolerance:
            break
    k = (lo + hi) / 2.0
    powered = [p ** k for p in raw]
    total = sum(powered)
    return [p / total for p in powered]


def devig(
    odds: Mapping[str, float] | Sequence[float],
    method: str = "multiplicative",
    complete: bool = True,
) -> MarketProbabilities:
    """Remove a margem de um mercado e devolve probabilidades justas.

    `odds` pode ser {resultado: odd} ou uma sequencia de odds.
    `complete=False` sinaliza que o mercado nao esta inteiro (ex.: so o
    favorito), caso em que a normalizacao e local e nao deve ser tratada
    como probabilidade real.
    """
    labels, values = _as_list(odds)
    if method not in DEVIG_METHODS:
        raise ValueError(f"metodo de de-vig desconhecido: {method!r}")
    raw = [implied_prob(o) for o in values]
    if method == "shin":
        fair = shin_fair_probs(raw)
    elif method == "power":
        fair = power_fair_probs(raw)
    else:
        fair = devig_multiplicative(raw)
    return MarketProbabilities(
        fair_probabilities={label: p for label, p in zip(labels, fair)},
        overround=sum(raw),
        method=method,
        complete=complete,
    )


def fair_decimal_odds(probabilities: Mapping[str, float]) -> dict[str, float]:
    """Odds justas (sem margem) a partir de probabilidades."""
    return {label: decimal_from_prob(p) for label, p in probabilities.items()}


def fair_odds_for(odds: Mapping[str, float], method: str = "multiplicative") -> dict[str, float]:
    """Atalho: odds justas de um mercado a partir das odds observadas."""
    result = devig(odds, method=method)
    return fair_decimal_odds(result.fair_probabilities)


def edge_vs_fair(price: float, fair_probability: float) -> float:
    """Edge percentual da odd observada contra a probabilidade justa.

    > 0 significa preco melhor que o justo (mesmo com margem removida).
    """
    if fair_probability <= 0.0 or fair_probability >= 1.0:
        raise ValueError("probabilidade justa precisa estar em (0, 1)")
    if not isfinite(price) or price <= 1.0:
        raise ValueError("odd precisa ser finita e > 1.0")
    return price * fair_probability - 1.0
