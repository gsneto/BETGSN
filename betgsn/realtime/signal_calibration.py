"""BETGSN :: realtime.signal_calibration — thresholds calibrados por HISTORICO.

Por que existe
--------------
Os thresholds dos sinais realtime (OUTLIER 5%, DISPERSION 4%, BEST_GAP 4%)
nasceram como constantes de engenharia. Este modulo permite deriva-los de
uma amostra de referencia (historico/train), com metodologia explicita:

    threshold = quantil(q) da distribuicao historica da metrica

Nunca calibra no TEST: a amostra de calibracao e a referencia que o
chamador fornece (janela de treino). Sem amostra suficiente, o resultado e
`INSUFFICIENT_DATA` — o sinal continua RESEARCH, nunca PRODUCTION.

O resultado carrega threshold + metodo + tamanho da amostra + periodo +
fingerprint, para auditoria. Nenhum numero e inventado.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from typing import Sequence

#: Amostra minima para considerar uma calibracao valida.
MIN_CALIBRATION_SAMPLE = 200

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class CalibrationResult:
    """Threshold calibrado (ou INSUFFICIENT_DATA) com proveniencia."""

    metric: str
    status: str
    threshold: float | None
    quantile: float
    sample_size: int
    method: str
    period: tuple[str, str] = ("", "")
    fingerprint: str = ""

    @property
    def usable(self) -> bool:
        return self.status == STATUS_OK and self.threshold is not None

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "status": self.status,
            "threshold": self.threshold,
            "quantile": self.quantile,
            "sample_size": self.sample_size,
            "method": self.method,
            "period": {"start": self.period[0], "end": self.period[1]},
            "fingerprint": self.fingerprint,
        }


def _fingerprint(metric: str, quantile: float, values: Sequence[float]) -> str:
    h = hashlib.sha256()
    h.update(f"{metric}|{quantile}".encode())
    h.update(f"|n={len(values)}".encode())
    h.update(f"|sum={sum(values):.6f}".encode())
    h.update(f"|max={max(values):.6f}".encode() if values else b"|empty")
    return h.hexdigest()[:16]


def calibrate_threshold(
    values: Sequence[float],
    *,
    metric: str,
    quantile: float,
    min_sample: int = MIN_CALIBRATION_SAMPLE,
    period: tuple[str, str] = ("", ""),
    method: str = "historical_quantile",
) -> CalibrationResult:
    """Threshold = quantil historico de `values`.

    - `values` vazio ou curto demais -> INSUFFICIENT_DATA (threshold None).
    - quantil fora de (0,1) -> erro de contrato (nao inventa threshold).
    """
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile precisa estar em (0,1), recebi {quantile!r}")
    clean = [float(v) for v in values]
    if len(clean) < max(1, int(min_sample)):
        return CalibrationResult(
            metric=metric, status=STATUS_INSUFFICIENT, threshold=None,
            quantile=quantile, sample_size=len(clean), method=method,
            period=period,
        )
    ordered = sorted(clean)
    idx = min(len(ordered) - 1, int(quantile * len(ordered)))
    threshold = ordered[idx]
    return CalibrationResult(
        metric=metric, status=STATUS_OK, threshold=threshold,
        quantile=quantile, sample_size=len(ordered), method=method,
        period=period, fingerprint=_fingerprint(metric, quantile, ordered),
    )


def outlier_deviations(prices_by_book: Sequence[float]) -> list[float]:
    """|(preco - mediana dos demais) / mediana dos demais| por casa.

    Metrica do BOOKMAKER_OUTLIER, exposta para calibracao historica: mede
    a dispersao de cada casa contra as OUTRAS, nao contra a media global.
    """
    prices = [float(p) for p in prices_by_book]
    out: list[float] = []
    for i, price in enumerate(prices):
        others = [p for j, p in enumerate(prices) if j != i]
        if len(others) < 2:
            continue
        med = statistics.median(others)
        if med <= 0:
            continue
        out.append(abs((price - med) / med))
    return out


def dispersion_ratios(prices_by_book: Sequence[float]) -> list[float]:
    """stdev(precos)/mediana(precos) — metrica do DISPERSION_SPIKE."""
    prices = [float(p) for p in prices_by_book]
    if len(prices) < 2:
        return []
    med = statistics.median(prices)
    if med <= 0:
        return []
    return [statistics.pstdev(prices) / med]


def best_gap_ratios(prices_by_book: Sequence[float]) -> list[float]:
    """(melhor - mediana)/mediana — metrica do BEST_PRICE_GAP."""
    prices = [float(p) for p in prices_by_book]
    if len(prices) < 2:
        return []
    med = statistics.median(prices)
    if med <= 0:
        return []
    return [(max(prices) - med) / med]


__all__ = [
    "CalibrationResult",
    "calibrate_threshold",
    "outlier_deviations",
    "dispersion_ratios",
    "best_gap_ratios",
    "MIN_CALIBRATION_SAMPLE",
    "STATUS_OK",
    "STATUS_INSUFFICIENT",
]
