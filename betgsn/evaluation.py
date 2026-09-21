"""Métricas multiclasse, CLV e drift com definições explícitas."""
import math
from statistics import mean
from .timeutil import utc_key


def rps(probabilities, actual_index):
    if len(probabilities) < 2 or not 0 <= actual_index < len(probabilities):
        raise ValueError("RPS requer classes ordenadas e resultado válido")
    if any(not math.isfinite(p) or p < 0 for p in probabilities) or abs(sum(probabilities)-1) > 1e-6:
        raise ValueError("distribuição inválida")
    cumulative = 0
    score = 0
    for i, p in enumerate(probabilities[:-1]):
        cumulative += p
        score += (cumulative - int(actual_index <= i)) ** 2
    return score / (len(probabilities)-1)


def calibration_metrics(pairs, n_bins=10):
    if n_bins < 1:
        raise ValueError("bins deve ser positivo")
    bins = [[] for _ in range(n_bins)]
    for p, y in pairs:
        if not math.isfinite(p) or not 0 <= p <= 1 or y not in (0, 1):
            raise ValueError("par probabilístico inválido")
        bins[min(int(p*n_bins), n_bins-1)].append((p, y))
    curve = [{"predicted": mean(p for p, _ in b), "observed": mean(y for _, y in b), "n": len(b)}
             for b in bins if b]
    errors = [abs(b["predicted"]-b["observed"]) for b in curve]
    return {"ece": sum(e*b["n"] for e, b in zip(errors, curve))/len(pairs) if pairs else None,
            "mce": max(errors) if errors else None, "reliability_curve": curve}


def score_predictions(p, y):
    if len(p) != len(y) or not len(y):
        raise ValueError("previsões vazias/desalinhadas")
    pairs = [(float(row[c]), int(c == a)) for row, a in zip(p, y) for c in range(len(row))]
    result = calibration_metrics(pairs)
    result.update(n=len(y), brier=mean(sum((float(v)-int(c == a))**2 for c,v in enumerate(row)) for row,a in zip(p,y)),
                  logloss=mean(-math.log(max(1e-12, float(row[a]))) for row,a in zip(p,y)),
                  rps=mean(rps(row,a) for row,a in zip(p,y)))
    return result


def clv(entry_odd, closing_odd, prediction_timestamp, bet_timestamp, closing_timestamp):
    if closing_odd is None or closing_timestamp is None:
        return {"absolute": None, "percentage": None}
    if entry_odd <= 1 or closing_odd <= 1:
        raise ValueError("CLV requer odds decimais válidas")
    if not utc_key(prediction_timestamp) <= utc_key(bet_timestamp) < utc_key(closing_timestamp):
        raise ValueError("timestamps CLV fora de ordem")
    return {"absolute": entry_odd-closing_odd, "percentage": entry_odd/closing_odd-1}


def drift(reference, current, threshold, bins=10):
    """PSI em bins fixados no treino; threshold fornecido pelo operador."""
    import numpy as np
    if not reference or not current or threshold <= 0:
        raise ValueError("drift requer duas amostras e threshold positivo")
    edges = np.unique(np.quantile(reference, np.linspace(0,1,bins+1)))
    if len(edges) < 2:
        edges = np.array([min(reference)-.5, max(reference)+.5])
    edges[0], edges[-1] = -np.inf, np.inf
    a = np.histogram(reference, edges)[0] / len(reference)
    b = np.histogram(current, edges)[0] / len(current)
    a, b = np.maximum(a,1e-6), np.maximum(b,1e-6)
    psi = float(np.sum((b-a)*np.log(b/a)))
    return {"psi": psi, "threshold": threshold, "drift": psi > threshold,
            "n_reference": len(reference), "n_current": len(current)}
