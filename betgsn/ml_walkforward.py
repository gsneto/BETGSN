"""BETGSN :: ml_walkforward — modelos experimentais nas MESMAS 24 janelas.

POR QUE ESTE MODULO EXISTE
--------------------------
Elo, XGBoost e LightGBM tinham avaliação própria (benchmark de 5 blocos
por liga, `benchmark.run_benchmark`) — protocolo legítimo, mas JANELAS
DIFERENTES das 24 janelas walk-forward da Etapa 19. A pergunta "o modelo
experimental adiciona informação além do mercado?" só é comparável ao
BASELINE_V1 quando medida no MESMO protocolo:

    TRAIN (730d) -> GAP/EMBARGO (2d) -> TEST (365d), 24 janelas,
    treino apenas em partidas anteriores a train_end (mesma regra do
    `fit_model_on_train`), avaliação apenas no TEST, mesmo benchmark
    de mercado (market_raw / market_fair do `run_model_walkforward`).

DISCIPLINA TEMPORAL
-------------------
- Features: `FeatureBuilder` é point-in-time (rolling windows com cutoff
  no kickoff da própria partida; Elo só atualiza após `result_time`).
- Fit por janela: partidas com kickoff < train_end; para os boosters,
  os últimos 20% do TRAIN são reservados ao early stopping (split
  temporal DENTRO do train — o TEST nunca participa). O modelo
  early-stopped fica CONGELADO para o TEST: sem reajuste, sem tuning.
- Hiperparâmetros: os DEFAULTS de `models/*_model.py`, os mesmos do
  benchmark histórico — NENHUM ajuste olhando OOS.

NADA aqui promove modelo: o output é evidência comparativa (model vs
market_raw/market_fair), sem ranking, sem vencedor. O Ensemble fica
PENDENTE neste protocolo (exige stacking OOS dentro de cada janela; a
evidência dele vive em cross_season_gate.json, protocolo próprio).

Contrato do provider: objeto com `prob_1x2(home, away)` e `n_matches`,
produzido por `fit(matches, train_end)` — consumido pelo MESMO harness
(`run_model_walkforward(model_fn=...)`), sem caminho paralelo de
avaliação.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: Fracao final do TRAIN reservada ao early stopping (split temporal).
EARLY_STOP_FRACTION = 0.2

#: Amostra minima do bloco de early stopping (apos ajuste de fronteira).
MIN_EARLY_STOP_ROWS = 1000


def _temporal_split_index(
    times: Sequence[str], fraction: float,
    *, min_rows: int = MIN_EARLY_STOP_ROWS,
) -> int | None:
    """Índice do split train/validação numa fronteira de TIMESTAMP.

    Partidas simultâneas (mesmo kickoff) são comuns no corpus: o corte
    bruto pode cair ENTRE elas, e `separated` exige disjunção estrita.
    O corte avança até a próxima fronteira de timestamp; sem espaço
    suficiente devolve None (a janela fica sem modelo — declarado,
    nunca contornado com dado sobreposto).
    """
    n = len(times)
    raw = int(n * (1.0 - fraction))
    if raw < 1 or raw >= n:
        return None
    idx = raw
    # avanca ate a proxima fronteira de timestamp
    while idx < n and times[idx] == times[idx - 1]:
        idx += 1
    if idx >= n:
        # sem espaco a frente: recua ate a fronteira anterior
        idx = raw
        while idx > 0 and times[idx] == times[idx - 1]:
            idx -= 1
    if idx <= 0 or idx >= n or n - idx < min_rows:
        return None
    if times[idx] == times[idx - 1]:
        return None
    return idx

#: Amostra minima de partidas do TRAIN para o fit (mesma ordem do
#: MIN_RATINGS_SAMPLE do BASELINE_V1).
MIN_TRAIN_MATCHES = 300

#: Colunas Elo usadas pelo EloModel (mesma escolha do benchmark).
ELO_COLUMNS = ("elo_difference", "home_elo_home", "away_elo_away")

#: Kinds suportados neste protocolo.
ML_MODEL_KINDS = ("elo", "xgboost", "lightgbm")

#: Horizonte de previsao por janela: TRAIN bets (730d) + TEST (365d) +
#: margem. Prever alem disso seria custo sem consumo no harness.
_PREDICT_LOOKBACK_DAYS = 800
_PREDICT_HORIZON_DAYS = 380


# --------------------------------------------------------------------------
# Matriz de features point-in-time (uma passada pelo corpus)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureCorpus:
    """Features PIT por partida, indexadas por (home, away, dia).

    Construída UMA vez com `FeatureBuilder` (features no kickoff da
    própria partida — sem informação futura por construção). O índice é
    o mesmo (home, away, d) das linhas canônicas de `collect_bets`.
    """

    keys: dict[tuple[str, str, str], int]
    rows: tuple[tuple[str, str, str], ...]
    matrix: Any  # numpy.ndarray (n, features)
    labels: Any  # numpy.ndarray (n,) — 0=1, 1=X, 2=2
    times: tuple[str, ...]
    columns: tuple[str, ...]


def build_feature_corpus(
    matches: Sequence, progress: Any = None,
) -> FeatureCorpus:
    """Uma passada de FeatureBuilder sobre o corpus histórico inteiro.

    `matches`: partidas históricas (`to_historical()`); a ordem de
    entrada é imposta (o builder exige builds cronológicos).

    MEMÓRIA: a matriz é PRÉ-ALOCADA (259k partidas × ~166 features em
    float64 ≈ 344 MB) e os snapshots são descartados a cada iteração —
    reter a lista de FeatureSnapshot custa vários GB e derruba o
    processo. As chaves de features são CONSTANTES do builder (mesmo
    conjunto para qualquer partida), então a primeira define as colunas.
    """
    import numpy as np

    from .features import FeatureBuilder
    from .model import Fixture

    ordered = sorted(matches, key=lambda m: str(m.kickoff))
    builder = FeatureBuilder(ordered)

    keys: dict[tuple[str, str, str], int] = {}
    rows: list[tuple[str, str, str]] = []
    labels = np.empty(len(ordered), dtype=np.int64)
    times: list[str] = []
    matrix: "np.ndarray | None" = None
    columns: tuple[str, ...] = ()

    for i, m in enumerate(ordered):
        if progress is not None and i % 20000 == 0:
            progress(i, len(ordered), f"features {i}/{len(ordered)}")
        fx = Fixture(m.home, m.away, m.league, str(m.kickoff))
        snap = builder.build(fx)
        if matrix is None:
            columns = tuple(sorted(snap.values))
            matrix = np.full(
                (len(ordered), len(columns)), np.nan, dtype=np.float64)
        row = matrix[i]
        for j, col in enumerate(columns):
            value = snap.values.get(col)
            if value is not None:
                row[j] = float(value)
        goal_diff = m.home_goals - m.away_goals
        labels[i] = 0 if goal_diff > 0 else 1 if goal_diff == 0 else 2
        times.append(str(m.kickoff))
        key = (m.home, m.away, str(m.kickoff)[:10])
        keys[key] = i
        rows.append(key)
        # o snapshot nao sobrevive a iteracao: so a linha da matriz

    assert matrix is not None, "corpus vazio"
    return FeatureCorpus(
        keys=keys,
        rows=tuple(rows),
        matrix=matrix,
        labels=labels,
        times=tuple(times),
        columns=columns,
    )


# --------------------------------------------------------------------------
# Provider: modelo congelado por janela com o contrato prob_1x2
# --------------------------------------------------------------------------


class MLWindowAdapter:
    """Adapta um modelo experimental ao contrato das 24 janelas.

    `kind` in {"elo", "xgboost", "lightgbm"}. Uso:

        adapter = MLWindowAdapter("xgboost", corpus)
        result = run_model_walkforward(bets, matches, config,
                                       model_fn=adapter.fit)

    `fit(matches, train_end)` (o `matches` do harness é ignorado — o
    corpus de features já o cobre por inteiro) seleciona as partidas
    com kickoff < train_end — a MESMA regra do `fit_model_on_train` do
    BASELINE_V1 — ajusta o modelo nos hiperparâmetros DEFAULT (sem
    tuning) e devolve um modelo congelado que responde `prob_1x2` para
    as linhas do TEST (e do TRAIN, usadas apenas pela escolha do
    calibrador dentro do train — o TEST nunca participa).
    """

    def __init__(self, kind: str, corpus: FeatureCorpus,
                 *, min_early_stop_rows: int = MIN_EARLY_STOP_ROWS) -> None:
        if kind not in ML_MODEL_KINDS:
            raise ValueError(f"kind desconhecido: {kind!r}")
        self.kind = kind
        self.corpus = corpus
        self.min_early_stop_rows = min_early_stop_rows
        #: (home, away) -> [dias]: o dia desambigua temporadas de um
        #: mesmo confronto.
        self._pair_days: dict[tuple[str, str], list[str]] = {}
        for (home, away, day) in corpus.keys:
            self._pair_days.setdefault((home, away), []).append(day)

    # ------------------------------------------------------------ fit

    def fit(self, matches: Sequence, train_end: str):
        import numpy as np

        from .models.base import TemporalBatch, separated

        corpus = self.corpus
        cutoff = str(train_end)
        train_idx = [i for i, t in enumerate(corpus.times) if t < cutoff]
        if len(train_idx) < MIN_TRAIN_MATCHES:
            return None

        x_train = corpus.matrix[train_idx]
        y_train = corpus.labels[train_idx]
        times_train = [corpus.times[i] for i in train_idx]

        if self.kind == "elo":
            cols = [corpus.columns.index(c) for c in ELO_COLUMNS]
            from .models.elo import EloModel

            model = EloModel().fit(TemporalBatch(
                x_train[:, cols] / 400.0, y_train, tuple(times_train)))

            def predict(x):
                return _map_classes(
                    model.predict_proba(x[:, cols] / 400.0),
                    model.model.classes_,
                )
        else:
            # early stopping com split temporal DENTRO do train, cortado
            # numa fronteira de timestamp (partidas simultaneas exigem
            # disjuncao estrita — ver _temporal_split_index)
            split = _temporal_split_index(
                times_train, EARLY_STOP_FRACTION,
                min_rows=self.min_early_stop_rows)
            if split is None:
                # sem fronteira utilizavel: janela sem modelo ML —
                # declarado, nunca contornado com dado sobreposto
                return None
            batch_fit = TemporalBatch(
                x_train[:split], y_train[:split],
                tuple(times_train[:split]))
            batch_val = TemporalBatch(
                x_train[split:], y_train[split:],
                tuple(times_train[split:]))
            separated(batch_fit, batch_val)
            if self.kind == "xgboost":
                from .models.xgboost_model import XGBoostModel

                model = XGBoostModel().fit(batch_fit, batch_val)
            else:
                from .models.lightgbm_model import LightGBMModel

                model = LightGBMModel().fit(batch_fit, batch_val)

            # o modelo early-stopped fica CONGELADO: sem reajuste no
            # train completo, sem ver o TEST.
            def predict(x):
                return _map_classes(
                    model.predict_proba(x), model.model.classes_)

        probabilities = self._predict_window(predict, cutoff)
        return _FrozenView(
            n_matches=len(train_idx), train_end=cutoff,
            probabilities=probabilities, pair_days=self._pair_days)

    # ------------------------------------------------------------ predict

    def _predict_window(
        self, predict: Callable, cutoff: str,
    ) -> dict[tuple[str, str, str], tuple[float, float, float]]:
        """Previsões congeladas para as linhas que o harness consome.

        Horizonte: [cutoff - 800d, cutoff + 380d] — cobre as apostas do
        TRAIN (730d, para a escolha do calibrador) e do TEST (~365d).
        Nada além disso é previsto: seria custo sem consumo.
        """
        from datetime import date, timedelta

        import numpy as np

        corpus = self.corpus
        base = date.fromisoformat(cutoff[:10])
        lower = (base - timedelta(days=_PREDICT_LOOKBACK_DAYS)).isoformat()
        upper = (base + timedelta(days=_PREDICT_HORIZON_DAYS)).isoformat()
        targets = [
            i for i, t in enumerate(corpus.times)
            if lower <= t[:10] <= upper
        ]
        if not targets:
            return {}
        x = corpus.matrix[targets]
        preds = np.asarray(predict(x))
        out: dict[tuple[str, str, str], tuple[float, float, float]] = {}
        for pos, i in enumerate(targets):
            key = corpus.rows[i]
            p = preds[pos]
            out[key] = (float(p[0]), float(p[1]), float(p[2]))
        return out


def _map_classes(raw: "np.ndarray", classes) -> "np.ndarray":
    """Colunas de predict_proba -> posições canônicas (0=1, 1=X, 2=2).

    sklearn devolve uma coluna por classe PRESENTE no treino: corpus
    sem empates produz 2 colunas. O contrato do harness é a tripla
    (p1, pX, p2) — a classe ausente recebe 0 (nunca observada no
    treino), sem inventar probabilidade.
    """
    import numpy as np

    raw = np.asarray(raw, dtype=float)
    out = np.zeros((raw.shape[0], 3), dtype=float)
    for j, cls in enumerate(classes):
        out[:, int(cls)] = raw[:, j]
    return out


class _FrozenView:
    """Modelo congelado com `prob_1x2(home, away)` e `n_matches`.

    O harness consulta por (home, away). Um par pode existir em
    temporadas diferentes (dias diferentes no corpus): a consulta
    resolve o dia da partida mais próxima DEPOIS do corte do treino —
    que é a linha que o TEST daquela janela contém. Partidas de
    temporadas diferentes nunca compartilham previsão.
    """

    def __init__(self, n_matches, train_end, probabilities, pair_days):
        self.n_matches = n_matches
        self.train_end = train_end
        self._probabilities = probabilities
        self._pair_days = pair_days

    def prob_1x2(self, home: str, away: str):
        days = self._pair_days.get((home, away))
        if not days:
            return None
        if len(days) == 1:
            return self._probabilities.get((home, away, days[0]))
        cutoff = self.train_end[:10]
        after = sorted(d for d in days if d >= cutoff)
        day = after[0] if after else sorted(days)[-1]
        return self._probabilities.get((home, away, day))


#: Fábrica de adapters por kind (o tool consome).
ADAPTER_FACTORIES: dict[str, Callable[[FeatureCorpus], MLWindowAdapter]] = {
    kind: (lambda corpus, k=kind: MLWindowAdapter(k, corpus))
    for kind in ML_MODEL_KINDS
}
