"""BETGSN :: models.validation — validacao out-of-sample walk-forward.

Por que este modulo existe
--------------------------
Um numero unico ("o modelo bateu o baseline") nao e evidencia. O que e
evidencia e a mesma conclusao reaparecer em varias janelas temporais,
comparando sempre os dois modelos nas MESMAS partidas e respeitando a
dependencia temporal dos jogos.

Este modulo junta tres coisas que o projeto ja tinha separadas:

1. `walk_forward.windows` / `indices` — janelas expansivas ou rolantes, com
   selecao por disponibilidade real dos dados (nao por data de calendario);
2. `evaluation.compare_models` — comparacao pareada com block bootstrap;
3. um auditor de leakage que reprova a configuracao antes de qualquer
   metrica ser calculada.

Regras de honestidade embutidas
-------------------------------
- O conjunto de teste de uma janela NUNCA entra no treino, na validacao nem
  no teste de outra janela. `leakage_audit` verifica isso e devolve as
  violacoes em vez de confiar que a configuracao esta certa.
- `tuned_on_test=True` nao altera as metricas: apenas marca o resultado
  como contaminado. Esconder isso seria pior do que reportar.
- Nenhum ajuste de hiperparametro e feito aqui. Este modulo mede, nao
  otimiza. Otimizar no teste e o defeito que ele existe para expor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from ..evaluation import LOSS_FUNCTIONS, compare_models, score_predictions
from ..timeutil import utc_key
from .base import probabilities
from .walk_forward import indices, windows

#: Amostras minimas por bloco para a janela contar como evidencia.
MIN_TRAIN = 200
MIN_VALIDATION = 50
MIN_TEST = 50


@dataclass(frozen=True)
class Fold:
    """Uma janela walk-forward com os indices de cada bloco."""

    index: int
    window: object
    train: tuple[int, ...]
    validation: tuple[int, ...]
    test: tuple[int, ...]

    @property
    def sizes(self) -> tuple[int, int, int]:
        return len(self.train), len(self.validation), len(self.test)


def build_folds(
    kickoff_times: Sequence[str],
    available_times: Sequence[str],
    start: str,
    end: str,
    *,
    train_days: int = 1095,
    validation_days: int = 365,
    test_days: int = 365,
    expanding: bool = True,
    min_train: int = MIN_TRAIN,
    min_validation: int = MIN_VALIDATION,
    min_test: int = MIN_TEST,
) -> list[Fold]:
    """Monta as janelas walk-forward e descarta as pequenas demais.

    `available_times` e o instante em que o RESULTADO de cada partida ficou
    conhecido. Usar so o kickoff permitiria treinar com partidas cujo
    resultado ainda nao existia — a forma mais comum de leakage temporal.
    """
    if len(kickoff_times) != len(available_times):
        raise ValueError("kickoff e disponibilidade precisam estar alinhados")
    folds: list[Fold] = []
    for i, window in enumerate(windows(
        start, end, train_days=train_days, validation_days=validation_days,
        test_days=test_days, expanding=expanding,
    )):
        train, validation, test = indices(window, kickoff_times, available_times)
        if (len(train) < min_train or len(validation) < min_validation
                or len(test) < min_test):
            continue
        folds.append(Fold(i, window, tuple(train), tuple(validation), tuple(test)))
    return folds


def leakage_audit(
    folds: Sequence[Fold],
    kickoff_times: Sequence[str],
    available_times: Sequence[str],
) -> list[str]:
    """Devolve a lista de violacoes de disciplina temporal. Vazia = ok.

    Checagens:
    - teste de uma janela nao pode aparecer em treino/validacao dela;
    - teste de uma janela nao pode aparecer no teste de outra;
    - treino/validacao/teste nao podem conter partidas cujo resultado so
      ficou disponivel depois do fim do bloco;
    - o treino nao pode conter partidas com kickoff posterior ao corte.
    """
    violations: list[str] = []
    test_owner: dict[int, int] = {}
    for fold in folds:
        train, validation, test = set(fold.train), set(fold.validation), set(fold.test)
        overlap = (train & test) | (validation & test) | (train & validation)
        if overlap:
            violations.append(f"janela {fold.index}: blocos sobrepostos {sorted(overlap)[:5]}")
        for idx in test:
            if idx in test_owner:
                violations.append(
                    f"indice {idx} no teste das janelas {test_owner[idx]} e {fold.index}"
                )
            test_owner[idx] = fold.index
        bounds = [fold.window.train_end, fold.window.validation_end, fold.window.test_end]
        for name, block in (("treino", train), ("validacao", validation), ("teste", test)):
            for idx in block:
                if utc_key(available_times[idx]) >= utc_key(bounds[0 if name == "treino" else (1 if name == "validacao" else 2)]):
                    violations.append(
                        f"janela {fold.index}: {name} usa partida {idx} "
                        "cujo resultado so existia depois do fim do bloco"
                    )
                    break
        for idx in train:
            if utc_key(kickoff_times[idx]) >= utc_key(fold.window.train_end):
                violations.append(
                    f"janela {fold.index}: treino contem kickoff posterior ao corte ({idx})"
                )
                break
    return violations


@dataclass
class WalkForwardResult:
    """Resultado agregado da validacao walk-forward."""

    folds: list[dict] = field(default_factory=list)
    per_fold_metrics: list[dict] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)
    stability: float = 0.0
    paired: dict | None = None
    leakage: list[str] = field(default_factory=list)
    tuned_on_test: bool = False
    verdict: str = "inconclusivo"

    @property
    def clean(self) -> bool:
        return not self.leakage and not self.tuned_on_test

    def to_dict(self) -> dict:
        return {
            "folds": self.folds,
            "per_fold_metrics": self.per_fold_metrics,
            "aggregate": self.aggregate,
            "stability": self.stability,
            "paired": self.paired,
            "leakage": self.leakage,
            "tuned_on_test": self.tuned_on_test,
            "verdict": self.verdict,
            "clean": self.clean,
        }


def walk_forward_validate(
    fit_predict: Callable[[tuple[int, ...], tuple[int, ...], tuple[int, ...]], np.ndarray],
    baseline_predict: Callable[[tuple[int, ...]], np.ndarray],
    y: Sequence[int],
    kickoff_times: Sequence[str],
    available_times: Sequence[str],
    start: str,
    end: str,
    *,
    metric: str = "logloss",
    min_effect: float = 0.0,
    tuned_on_test: bool = False,
    seed: int = 6767,
    resamples: int = 5000,
    **fold_kwargs,
) -> WalkForwardResult:
    """Roda a validacao walk-forward e compara candidato vs baseline.

    `fit_predict(train, validation, test)` deve treinar APENAS em
    train/validation e devolver as probabilidades no teste. O modulo nao
    consegue impedir um `fit_predict` malicioso; `tuned_on_test=True`
    existe para declarar explicitamente quando isso aconteceu.
    """
    if metric not in LOSS_FUNCTIONS:
        raise ValueError(f"metrica sem perda por observacao: {metric}")
    y = np.asarray(y, dtype=int)
    folds = build_folds(kickoff_times, available_times, start, end, **fold_kwargs)
    violations = leakage_audit(folds, kickoff_times, available_times)

    result = WalkForwardResult(leakage=violations, tuned_on_test=tuned_on_test)
    if not folds:
        result.aggregate = {"n_folds": 0}
        return result

    pooled_candidate: list[np.ndarray] = []
    pooled_baseline: list[np.ndarray] = []
    pooled_y: list[int] = []
    pooled_times: list[str] = []
    for fold in folds:
        candidate = fit_predict(fold.train, fold.validation, fold.test)
        baseline = baseline_predict(fold.test)
        cand_probs = probabilities(_as_probs(candidate, len(fold.test)))
        base_probs = probabilities(_as_probs(baseline, len(fold.test)))
        y_test = y[list(fold.test)]
        cand_metrics = score_predictions(cand_probs, y_test)
        base_metrics = score_predictions(base_probs, y_test)
        result.folds.append({
            "index": fold.index,
            "train": len(fold.train),
            "validation": len(fold.validation),
            "test": len(fold.test),
            "window": {
                "train_start": fold.window.train_start,
                "train_end": fold.window.train_end,
                "validation_end": fold.window.validation_end,
                "test_end": fold.window.test_end,
            },
        })
        result.per_fold_metrics.append({
            "index": fold.index,
            "n_test": len(fold.test),
            "candidate": {k: float(cand_metrics[k]) for k in (metric,)},
            "baseline": {k: float(base_metrics[k]) for k in (metric,)},
            "candidate_better": bool(cand_metrics[metric] < base_metrics[metric]),
        })
        pooled_candidate.append(cand_probs)
        pooled_baseline.append(base_probs)
        pooled_y.extend(int(v) for v in y_test)
        pooled_times.extend(str(kickoff_times[i]) for i in fold.test)

    cand_all = np.vstack(pooled_candidate)
    base_all = np.vstack(pooled_baseline)
    y_all = np.asarray(pooled_y, dtype=int)
    blocks = [t[:7] for t in pooled_times]

    result.aggregate = {
        "n_folds": len(folds),
        "n_test": int(len(y_all)),
        "candidate": score_predictions(cand_all, y_all),
        "baseline": score_predictions(base_all, y_all),
    }
    wins = sum(1 for row in result.per_fold_metrics if row["candidate_better"])
    result.stability = wins / len(folds)
    if len(set(blocks)) >= 2:
        result.paired = compare_models(
            base_all, cand_all, y_all, metric=metric, resamples=resamples,
            seed=seed, block_keys=blocks, min_effect=min_effect,
        )
        result.verdict = result.paired["verdict"]
    else:
        result.paired = None
        result.verdict = "inconclusivo"
    if violations:
        result.verdict = "leakage"
    return result


def _as_probs(predictions, n: int) -> np.ndarray:
    """Aceita (n, k) ou (n,) binario e devolve (n, k) normalizado."""
    array = np.asarray(predictions, dtype=float)
    if array.ndim == 1:
        array = np.column_stack([1.0 - array, array])
    if array.shape[0] != n:
        raise ValueError("previsoes fora do tamanho do bloco de teste")
    return array


# --------------------------------------------------------------------------
# Ablacao de ensemble
# --------------------------------------------------------------------------


def _weighted(stack: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return probabilities(np.tensordot(weights, stack, axes=1))


def ensemble_ablation(
    predictions: dict[str, np.ndarray],
    y: Sequence[int],
    *,
    weights: dict[str, float] | None = None,
    metric: str = "logloss",
) -> dict:
    """Contribuicao de cada membro do ensemble, por leave-one-out.

    `contribution` = perda SEM o membro menos perda COM o membro. Positivo
    significa que remover o membro piora o ensemble, ou seja, ele ajuda.
    Proximo de zero significa que o membro nao agrega — e um membro que nao
    agrega so serve para aumentar a variancia da estimativa dos pesos.
    """
    loss = LOSS_FUNCTIONS.get(metric)
    if loss is None:
        raise ValueError(f"metrica sem perda por observacao: {metric}")
    names = tuple(sorted(predictions))
    if len(names) < 2:
        raise ValueError("ablacao de ensemble requer ao menos 2 membros")
    y = np.asarray(y, dtype=int)
    stack = np.array([probabilities(predictions[n]) for n in names])
    if weights is None:
        w = np.full(len(names), 1.0 / len(names))
    else:
        w = np.array([float(weights.get(n, 0.0)) for n in names], dtype=float)
        if w.sum() <= 0:
            raise ValueError("pesos do ensemble precisam somar positivo")
        w = w / w.sum()

    full_loss = float(loss(_weighted(stack, w), y).mean())
    members: list[dict] = []
    for i, name in enumerate(names):
        keep = [j for j in range(len(names)) if j != i]
        w_out = w[keep] / w[keep].sum()
        out_loss = float(loss(_weighted(stack[keep], w_out), y).mean())
        members.append({
            "name": name,
            "weight": float(w[i]),
            "loss_with": full_loss,
            "loss_without": out_loss,
            "contribution": out_loss - full_loss,
            "helps": bool(out_loss > full_loss),
        })
    return {
        "metric": metric,
        "weights": {n: float(v) for n, v in zip(names, w)},
        "loss": full_loss,
        "members": members,
        "effective_members": _effective_members(w),
    }


def _effective_members(w: np.ndarray) -> float:
    """Numero efetivo de membros (inverso de Herfindahl dos pesos)."""
    s = float(np.sum(w))
    if s <= 0:
        return 0.0
    p = w / s
    return float(1.0 / np.sum(p * p))


#: Coeficiente de variacao acima disso marca o peso como instavel.
MAX_WEIGHT_CV = 0.5


def weight_stability(
    weight_samples: Sequence[Sequence[float]],
    *,
    max_cv: float = MAX_WEIGHT_CV,
) -> dict:
    """Dispersao dos pesos entre janelas: peso que muda de sinal nao e peso.

    Recebe uma lista de vetores de peso (uma por janela/reamostragem) e
    devolve, por posicao, media, desvio, coeficiente de variacao e a fracao
    de amostras com sinal diferente da media. `stable` exige CV <= `max_cv`:
    desvio absoluto sozinho esconde instabilidade em pesos pequenos.
    """
    import statistics

    if not weight_samples:
        raise ValueError("weight_stability requer ao menos uma amostra")
    width = len(weight_samples[0])
    if any(len(row) != width for row in weight_samples):
        raise ValueError("amostras de peso com tamanhos diferentes")
    out = []
    for i in range(width):
        column = [float(row[i]) for row in weight_samples]
        mean = statistics.fmean(column)
        stdev = statistics.pstdev(column) if len(column) > 1 else 0.0
        sign_flips = sum(1 for v in column if (v > 0) != (mean > 0))
        if abs(mean) < 1e-12:
            cv = None
            stable = stdev <= 1e-12
        else:
            cv = stdev / abs(mean)
            stable = cv <= max_cv
        out.append({
            "mean": mean,
            "stdev": stdev,
            "cv": cv,
            "sign_flip_rate": sign_flips / len(column),
            "stable": bool(stable),
        })
    return {"members": out}
