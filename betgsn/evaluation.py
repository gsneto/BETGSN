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


# ==========================================================================
# Comparacao honesta entre modelos
# ==========================================================================
#
# O problema central deste modulo: decidir se uma melhora de metrica e REAL
# ou apenas ruido amostral. Uma melhora de +0,5% em LogLoss pode ser
# indistinguivel de zero; chama-la de vantagem e o erro que transforma um
# resultado inconclusivo em "edge comprovado".
#
# A resposta nao e olhar a metrica agregada. E comparar os dois modelos nas
# MESMAS partidas (teste pareado) e resamplear respeitando a dependencia
# temporal (block bootstrap por mes/temporada). Sem isso, o erro-padrao
# esta subestimado e quase tudo "parece" significativo.

#: Numero de reamostragens bootstrap padrao.
BOOTSTRAP_RESAMPLES = 5000
#: Seed fixa: o resultado tem que ser reproduzivel, nao "sorteado".
BOOTSTRAP_SEED = 6767


def logloss_terms(probabilities, actual_index):
    """Perda log-loss POR OBSERVACAO. Menor = melhor.

    Devolver o vetor (e nao a media) e o que permite o bootstrap pareado:
    cada partida contribui com sua propria perda, e as duas previsoes sao
    comparadas na MESMA partida.
    """
    import numpy as np
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(actual_index, dtype=int)
    if p.ndim != 2 or len(p) != len(y):
        raise ValueError("previsoes/resultados desalinhados")
    if (y < 0).any() or (y >= p.shape[1]).any():
        raise ValueError("indice de resultado fora das classes")
    return -np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1.0))


def brier_terms(probabilities, actual_index):
    """Perda Brier multiclasse POR OBSERVACAO: soma_c (p_c - y_c)^2."""
    import numpy as np
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(actual_index, dtype=int)
    if p.ndim != 2 or len(p) != len(y):
        raise ValueError("previsoes/resultados desalinhados")
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    return ((p - onehot) ** 2).sum(axis=1)


def rps_terms(probabilities, actual_index):
    """Ranked Probability Score POR OBSERVACAO (classes ordenadas)."""
    import numpy as np
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(actual_index, dtype=int)
    out = np.empty(len(y), dtype=float)
    for i, (row, a) in enumerate(zip(p, y)):
        out[i] = rps(list(map(float, row)), int(a))
    return out


#: Perda por observacao, por metrica. Chave = nome publico da metrica.
LOSS_FUNCTIONS = {"logloss": logloss_terms, "brier": brier_terms, "rps": rps_terms}


def _block_indices(block_keys):
    """Agrupa posicoes em blocos contiguos por chave (ex.: mes)."""
    import numpy as np
    keys = np.asarray(block_keys)
    if len(keys) == 0:
        return []
    order = np.argsort(keys, kind="stable")
    blocks, start = [], 0
    for i in range(1, len(order) + 1):
        if i == len(order) or keys[order[i]] != keys[order[start]]:
            blocks.append(order[start:i])
            start = i
    return blocks


def paired_bootstrap(a, b, *, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED,
                     block_keys=None, confidence=0.95):
    """IC bootstrap da media de `a - b`, pareado por observacao.

    `a` e `b` sao perdas por observacao (ex.: baseline e candidato) na MESMA
    ordem de partidas. Positivo = `b` perdeu mais = `a` e melhor.

    Com `block_keys` (ex.: mes do kickoff), o bootstrap reamostra BLOCOS
    inteiros em vez de partidas soltas. E o minimo de honestidade temporal:
    partidas do mesmo mes compartilham condicoes, entao trata-las como
    independentes subestima o erro-padrao.

    Devolve dict com `mean_diff`, `se`, `ci_low`, `ci_high`, `p_value`,
    `distinguishable`, `n` e `n_blocks`.
    """
    import numpy as np
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) != len(b) or len(a) < 2:
        raise ValueError("bootstrap pareado requer duas amostras do mesmo tamanho")
    diff = a - b
    n = len(diff)
    blocks = _block_indices(block_keys) if block_keys is not None else None
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    if blocks is None:
        for r in range(resamples):
            means[r] = diff[rng.integers(0, n, size=n)].mean()
    else:
        if len(blocks) < 2:
            raise ValueError("block bootstrap requer ao menos 2 blocos")
        for r in range(resamples):
            pick = rng.integers(0, len(blocks), size=len(blocks))
            means[r] = np.concatenate([diff[blocks[j]] for j in pick]).mean()
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(means, [alpha, 1.0 - alpha])
    p_value = 2.0 * min(float((means <= 0).mean()), float((means >= 0).mean()))
    return {
        "mean_diff": float(diff.mean()),
        "se": float(diff.std(ddof=1) / np.sqrt(n)),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p_value": float(min(1.0, p_value)),
        "distinguishable": bool(lo > 0 or hi < 0),
        "confidence": confidence,
        "resamples": resamples,
        "n": int(n),
        "n_blocks": len(blocks) if blocks is not None else n,
    }


def block_bootstrap_ci(values, block_keys, *, statistic=None, resamples=BOOTSTRAP_RESAMPLES,
                       seed=BOOTSTRAP_SEED, confidence=0.95):
    """IC bootstrap percentil de uma serie, reamostrando blocos temporais."""
    import numpy as np
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        raise ValueError("bootstrap requer ao menos 2 observacoes")
    blocks = _block_indices(block_keys)
    if len(blocks) < 2:
        raise ValueError("block bootstrap requer ao menos 2 blocos")
    stat = statistic or (lambda x: float(np.mean(x)))
    rng = np.random.default_rng(seed)
    stats = np.empty(resamples, dtype=float)
    for r in range(resamples):
        pick = rng.integers(0, len(blocks), size=len(blocks))
        stats[r] = stat(np.concatenate([values[blocks[j]] for j in pick]))
    alpha = (1.0 - confidence) / 2.0
    return {"low": float(np.quantile(stats, alpha)),
            "high": float(np.quantile(stats, 1.0 - alpha)),
            "n_blocks": len(blocks), "confidence": confidence}


#: Vereditos possiveis de uma comparacao pareada.
VERDICTS = ("melhora_robusta", "melhora_pequena", "piora_robusta", "inconclusivo")


def comparison_verdict(paired, *, min_effect=0.0):
    """Traduz o IC pareado em um veredito explicito.

    - `inconclusivo`: o IC cruza zero. Nao ha evidencia de diferenca.
    - `melhora_pequena`: melhora real, mas menor que `min_effect` (a margem
      de erro medida). Real != relevante.
    - `melhora_robusta`: IC inteiro acima de `min_effect`.
    - `piora_robusta`: IC inteiro abaixo de zero.
    """
    lo, hi = paired["ci_low"], paired["ci_high"]
    if lo <= 0 <= hi:
        return "inconclusivo"
    if hi < 0:
        return "piora_robusta"
    return "melhora_robusta" if lo >= min_effect else "melhora_pequena"


def compare_models(predictions_a, predictions_b, actual_index, *, metric="logloss",
                   resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED, block_keys=None,
                   min_effect=0.0, lower_is_better=True):
    """Compara dois modelos nas MESMAS observacoes, com IC e veredito.

    `predictions_a` e o BASELINE; `predictions_b` e o CANDIDATO. A metrica
    padrao e LogLoss. A diferenca e sempre normalizada para "positivo =
    candidato melhor", independentemente de a metrica ser de erro ou de
    acerto.
    """
    import numpy as np
    loss = LOSS_FUNCTIONS.get(metric)
    if loss is None:
        raise ValueError(f"metrica sem perda por observacao: {metric}")
    a = loss(predictions_a, actual_index)
    b = loss(predictions_b, actual_index)
    if not lower_is_better:
        a, b = -a, -b
    paired = paired_bootstrap(a, b, resamples=resamples, seed=seed, block_keys=block_keys)
    paired["metric"] = metric
    paired["mean_a"] = float(np.mean(a))
    paired["mean_b"] = float(np.mean(b))
    paired["relative_improvement"] = (
        (paired["mean_a"] - paired["mean_b"]) / abs(paired["mean_a"])
        if paired["mean_a"] != 0 else None
    )
    paired["verdict"] = comparison_verdict(paired, min_effect=min_effect)
    paired["meets_min_effect"] = bool(paired["ci_low"] >= min_effect)
    return paired


def min_detectable_effect(n, sigma, z=1.959963984540054):
    """Menor efeito detectavel (aprox. normal) com n observacoes e desvio sigma."""
    if n <= 0 or sigma < 0:
        raise ValueError("n deve ser positivo e sigma nao-negativo")
    return z * sigma / math.sqrt(n)
