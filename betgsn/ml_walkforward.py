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
market_raw/market_fair), sem ranking, sem vencedor. O Ensemble entra
neste MESMO protocolo via stacking OOS dentro de cada janela
(`EnsembleWindowAdapter`): meta-modelo treinado apenas em previsões
out-of-sample das bases DENTRO do TRAIN da própria janela.

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

#: Kind do ensemble: stacking OOS dentro do TRAIN de cada janela.
ENSEMBLE_KIND = "ensemble"

#: Bases do stacking: exatamente os kinds avaliados individualmente no
#: MESMO protocolo (mesmo corpus de features, mesmos defaults).
ENSEMBLE_BASE_KINDS = ML_MODEL_KINDS

#: Folds rolling-origin DENTRO do TRAIN para gerar as previsões OOS que
#: treinam o meta-modelo. n_folds=3 => 4 chunks temporais: o primeiro
#: alimenta as bases do primeiro fold, os demais são previstos.
ENSEMBLE_FOLDS = 3

#: Amostra mínima por fold de stacking (rows de meta-treino por fold).
MIN_STACK_FOLD_ROWS = 1000

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
        if key in keys:
            # duas partidas do mesmo confronto no MESMO dia: dado
            # duplicado no corpus — falha alto, nunca absorve em
            # silencio (a segunda sobrescreveria a primeira).
            raise ValueError(
                f"corpus com partida duplicada: {key!r} — a chave "
                "(home, away, dia) precisa ser unica"
            )
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


def _fold_boundaries(
    times: Sequence[str], n_chunks: int, min_rows: int,
) -> list[int] | None:
    """Fronteiras dos `n_chunks` chunks temporais (cortes em fronteira
    de timestamp).

    Partidas simultâneas exigem disjunção estrita entre o treino de uma
    base e o fold previsto por ela: cada corte avança até a próxima
    fronteira de timestamp. Sem fronteira utilizável (chunks
    sobrepostos, amostra abaixo de `min_rows` nos chunks de previsão)
    devolve None — declarado, nunca contornado com dado sobreposto.
    """
    n = len(times)
    if n_chunks < 2:
        raise ValueError("n_chunks precisa ser >= 2")
    bounds = [0]
    for k in range(1, n_chunks):
        idx = int(k * n / n_chunks)
        while idx < n and (idx == 0 or times[idx] == times[idx - 1]):
            idx += 1
        if idx <= bounds[-1] or idx >= n:
            return None
        bounds.append(idx)
    bounds.append(n)
    # chunks de PREVISÃO (1..n_chunks-1) precisam de amostra mínima
    for k in range(1, n_chunks):
        if bounds[k + 1] - bounds[k] < min_rows:
            return None
    return bounds


def _fit_base(
    kind: str, corpus: "FeatureCorpus", rows: Sequence[int],
    min_early_stop_rows: int,
) -> Callable[[Any], Any] | None:
    """Ajusta UMA base (`kind`) nas linhas `rows` do corpus.

    Mesmo protocolo do MLWindowAdapter: para os boosters, os últimos
    EARLY_STOP_FRACTION do bloco são reservados ao early stopping (split
    temporal DENTRO do bloco — o que quer que venha depois nunca
    participa). Sem fronteira utilizável devolve None: janela/fold sem
    essa base, declarado.

    Devolve um callable `predict(x)` com colunas canônicas (0=1, 1=X,
    2=2).
    """
    from .models.base import TemporalBatch, separated

    x_train = corpus.matrix[rows]
    y_train = corpus.labels[rows]
    times_train = [corpus.times[i] for i in rows]

    if kind == "elo":
        cols = [corpus.columns.index(c) for c in ELO_COLUMNS]
        from .models.elo import EloModel

        model = EloModel().fit(TemporalBatch(
            x_train[:, cols] / 400.0, y_train, tuple(times_train)))

        def predict(x):
            return _map_classes(
                model.predict_proba(x[:, cols] / 400.0),
                model.model.classes_,
            )
        return predict

    # early stopping com split temporal DENTRO do bloco, cortado numa
    # fronteira de timestamp (partidas simultaneas exigem disjuncao
    # estrita — ver _temporal_split_index)
    split = _temporal_split_index(
        times_train, EARLY_STOP_FRACTION, min_rows=min_early_stop_rows)
    if split is None:
        return None
    batch_fit = TemporalBatch(
        x_train[:split], y_train[:split], tuple(times_train[:split]))
    batch_val = TemporalBatch(
        x_train[split:], y_train[split:], tuple(times_train[split:]))
    separated(batch_fit, batch_val)
    if kind == "xgboost":
        from .models.xgboost_model import XGBoostModel

        model = XGBoostModel().fit(batch_fit, batch_val)
    elif kind == "lightgbm":
        from .models.lightgbm_model import LightGBMModel

        model = LightGBMModel().fit(batch_fit, batch_val)
    else:
        raise ValueError(f"kind desconhecido: {kind!r}")

    # o modelo early-stopped fica CONGELADO: sem reajuste, sem ver
    # nada alem do proprio bloco de treino.
    def predict(x):
        return _map_classes(
            model.predict_proba(x), model.model.classes_)
    return predict


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
        corpus = self.corpus
        cutoff = str(train_end)
        train_idx = [i for i, t in enumerate(corpus.times) if t < cutoff]
        if len(train_idx) < MIN_TRAIN_MATCHES:
            return None

        predict = _fit_base(
            self.kind, corpus, train_idx, self.min_early_stop_rows)
        if predict is None:
            return None

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
    """Modelo congelado com `prob_1x2(home, away, day)` e `n_matches`.

    O harness consulta por (home, away, dia): um par pode existir em
    temporadas/dias diferentes e CADA partida recebe a previsão do SEU
    dia — consultas sem `day` são ambíguas e ficam sem resposta (None)
    quando o par tem mais de um dia previsto; nunca são resolvidas
    "aproximadamente" com o dia errado.
    """

    def __init__(self, n_matches, train_end, probabilities, pair_days):
        self.n_matches = n_matches
        self.train_end = train_end
        self._probabilities = probabilities
        self._pair_days = pair_days

    def prob_1x2(self, home: str, away: str, day: str | None = None):
        if day is not None:
            return self._probabilities.get((home, away, day))
        days = self._pair_days.get((home, away))
        if not days:
            return None
        if len(days) == 1:
            return self._probabilities.get((home, away, days[0]))
        # sem o dia e com multiplos confrontos previstos: a resposta
        # correta nao existe — None, nunca o dia errado
        return None


#: Fábrica de adapters por kind (o tool consome).
ADAPTER_FACTORIES: dict[str, Callable[[FeatureCorpus], MLWindowAdapter]] = {
    kind: (lambda corpus, k=kind: MLWindowAdapter(k, corpus))
    for kind in ML_MODEL_KINDS
}


# --------------------------------------------------------------------------
# Ensemble: stacking OOS por janela
# --------------------------------------------------------------------------


class EnsembleWindowAdapter:
    """Ensemble com stacking OOS dentro do TRAIN de cada janela.

    Protocolo por janela (`fit(matches, train_end)` — o MESMO gancho do
    harness das 24 janelas):

      1. TRAIN = partidas com kickoff < train_end (a MESMA regra do
         `fit_model_on_train`/MLWindowAdapter; o gap/embargo e o TEST
         ficam fora por construção);
      2. o TRAIN é dividido em `n_folds + 1` chunks temporais (cortes
         em fronteira de timestamp). Para cada fold k: bases ajustadas
         APENAS nos chunks anteriores prevêem o chunk k — previsões
         out-of-sample por construção (rolling origin);
      3. o meta-modelo (regressão logística multinomial sobre as
         probabilidades das bases) é ajustado SOMENTE nessas previsões
         OOS;
      4. bases FINAIS ajustadas no TRAIN completo ficam congeladas e
         preveem o horizonte da janela; o ensemble = meta aplicado às
         previsões das bases finais.

    O que isto NÃO é: treinar as bases no corpus inteiro para "depois"
    avaliar nas 24 janelas — isso seria leakage. Aqui cada janela refaz
    folds, meta e bases do zero, somente com o próprio TRAIN.

    Hiperparâmetros: os mesmos DEFAULTS das bases (nenhum tuning OOS) e
    a regressão logística padrão do meta (sem grid, sem escolha por
    resultado). `last_stack_audit` expõe o rastro do stacking (cortes,
    folds, tamanhos) para verificação de integridade temporal.
    """

    version = "ENSEMBLE_STACK_V1"

    def __init__(
        self,
        corpus: "FeatureCorpus",
        *,
        kinds: Sequence[str] = ENSEMBLE_BASE_KINDS,
        n_folds: int = ENSEMBLE_FOLDS,
        min_early_stop_rows: int = MIN_EARLY_STOP_ROWS,
        min_stack_rows: int = MIN_STACK_FOLD_ROWS,
        progress: Any = None,
    ) -> None:
        kinds = tuple(kinds)
        if not kinds:
            raise ValueError("ensemble exige ao menos uma base")
        for kind in kinds:
            if kind not in ML_MODEL_KINDS:
                raise ValueError(
                    f"kind desconhecido: {kind!r} (use {ML_MODEL_KINDS})")
        if n_folds < 1:
            raise ValueError("n_folds precisa ser >= 1")
        self.corpus = corpus
        self.kinds = kinds
        self.n_folds = int(n_folds)
        self.min_early_stop_rows = int(min_early_stop_rows)
        self.min_stack_rows = int(min_stack_rows)
        self._progress = progress
        #: (home, away) -> [dias]: o dia desambigua temporadas de um
        #: mesmo confronto (mesma semantica do MLWindowAdapter).
        self._pair_days: dict[tuple[str, str], list[str]] = {}
        for (home, away, day) in corpus.keys:
            self._pair_days.setdefault((home, away), []).append(day)
        #: rastro do último fit: folds, meta e bases finais — prova de
        #: que o stacking só consumiu o TRAIN da própria janela.
        self.last_stack_audit: dict | None = None

    # ------------------------------------------------------------ fit

    def fit(self, matches: Sequence, train_end: str):
        import numpy as np

        from datetime import timedelta
        from .temporal import result_time
        from .timeutil import parse_kickoff, utc_key

        corpus = self.corpus
        cutoff = str(train_end)
        # Labels must be available, not merely kicked off. Feature cache
        # contains no publication timestamps: recover them from source rows.
        available = {
            (m.home, m.away, str(m.kickoff)[:10]): result_time(m)
            for m in matches
        }
        train_idx = [i for i, t in enumerate(corpus.times)
                     if t < cutoff and available.get(corpus.rows[i], "9999")
                     < utc_key(cutoff)]
        if len(train_idx) < MIN_TRAIN_MATCHES:
            return None
        times_train = [corpus.times[i] for i in train_idx]

        n_chunks = self.n_folds + 1
        bounds = _fold_boundaries(
            times_train, n_chunks, self.min_stack_rows)
        if bounds is None:
            # sem fronteira utilizavel: janela sem ensemble — declarado,
            # nunca contornado com dado sobreposto
            self.last_stack_audit = {
                "train_end": cutoff, "folds": [], "meta": None,
                "final_bases": None,
                "reason": "sem fronteiras temporais utilizaveis "
                          f"para {n_chunks} chunks",
            }
            return None

        # ---- folds rolling-origin: previsões OOS para o meta ----
        # chunks: [bounds[j], bounds[j+1]); fold k (1..n_folds) é
        # previsto por bases ajustadas apenas em [0, bounds[k])
        meta_x: list = []
        meta_y: list = []
        meta_times: list[str] = []
        folds_audit: list[dict] = []
        for k in range(1, n_chunks):
            if self._progress is not None:
                self._progress(
                    k, self.n_folds,
                    f"ensemble fold {k}/{self.n_folds} (train_end "
                    f"{cutoff[:10]})")
            pred_rows = train_idx[bounds[k]:bounds[k + 1]]
            first_prediction = utc_key(corpus.times[pred_rows[0]])
            base_cutoff = (parse_kickoff(first_prediction)
                           - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            fit_rows = [i for i in train_idx[:bounds[k]]
                        if utc_key(corpus.times[i]) < base_cutoff
                        and available[corpus.rows[i]] < first_prediction]
            if not fit_rows:
                self.last_stack_audit = {"train_end": cutoff,
                                        "reason": "insufficient embargoed fold"}
                return None
            predictors: dict[str, Callable] = {}
            for kind in self.kinds:
                predict = _fit_base(
                    kind, corpus, fit_rows, self.min_early_stop_rows)
                if predict is None:
                    predictors = {}
                    break
                predictors[kind] = predict
            if not predictors:
                # fold sem TODAS as bases disponiveis: pulado e
                # declarado — nunca previsto com base faltando
                folds_audit.append({
                    "fold": k,
                    "skipped": True,
                    "base_train_end": corpus.times[fit_rows[-1]],
                    "first_prediction": times_train[bounds[k]],
                    "last_prediction": times_train[bounds[k + 1] - 1],
                    "n_base_rows": len(fit_rows),
                    "n_rows": len(pred_rows),
                })
                continue
            base_preds = [
                np.asarray(predictors[kind](corpus.matrix[pred_rows]),
                           dtype=float)
                for kind in self.kinds
            ]
            stack = np.concatenate(base_preds, axis=1)
            meta_x.append(stack)
            meta_y.append(corpus.labels[pred_rows])
            meta_times.extend(
                times_train[bounds[k]:bounds[k + 1]])
            folds_audit.append({
                "fold": k,
                "skipped": False,
                "base_train_end": corpus.times[fit_rows[-1]],
                "base_cutoff": base_cutoff,
                "labels_available_until": max(available[corpus.rows[i]] for i in fit_rows),
                "embargo_days": 2,
                "first_prediction": times_train[bounds[k]],
                "last_prediction": times_train[bounds[k + 1] - 1],
                "n_base_rows": len(fit_rows),
                "n_rows": len(pred_rows),
            })

        if not meta_x:
            self.last_stack_audit = {
                "train_end": cutoff, "folds": folds_audit, "meta": None,
                "final_bases": None,
                "reason": "nenhum fold utilizavel: janela sem ensemble",
            }
            return None

        # ---- meta-modelo: SOMENTE nas previsões OOS dos folds ----
        from sklearn.linear_model import LogisticRegression

        x_meta = np.concatenate(meta_x, axis=0)
        y_meta = np.concatenate(meta_y, axis=0)
        meta = LogisticRegression(max_iter=1000)
        meta.fit(x_meta, y_meta)
        meta_classes = list(meta.classes_)

        # ---- bases finais no TRAIN completo, congeladas ----
        final_predictors: dict[str, Callable] = {}
        for kind in self.kinds:
            predict = _fit_base(
                kind, corpus, train_idx, self.min_early_stop_rows)
            if predict is None:
                self.last_stack_audit = {
                    "train_end": cutoff, "folds": folds_audit,
                    "meta": {
                        "n_rows": int(len(meta_times)),
                        "first_time": min(meta_times),
                        "last_time": max(meta_times),
                    },
                    "final_bases": None,
                    "reason": f"base final {kind!r} sem split utilizavel",
                }
                return None
            final_predictors[kind] = predict

        # ---- previsões do horizonte: bases finais + meta congelado ----
        probabilities = self._predict_window(final_predictors, meta,
                                             meta_classes, cutoff)

        self.last_stack_audit = {
            "train_end": cutoff,
            "folds": folds_audit,
            "meta": {
                "n_rows": int(len(meta_times)),
                "first_time": min(meta_times),
                "last_time": max(meta_times),
            },
            "final_bases": {
                "n_rows": len(train_idx),
                "last_time": times_train[-1],
            },
        }
        return _FrozenView(
            n_matches=len(train_idx), train_end=cutoff,
            probabilities=probabilities, pair_days=self._pair_days)

    # ------------------------------------------------------------ predict

    def _predict_window(
        self,
        final_predictors: dict[str, Callable],
        meta: Any,
        meta_classes: list[int],
        cutoff: str,
    ) -> dict[tuple[str, str, str], tuple[float, float, float]]:
        """Previsões congeladas do ensemble para o horizonte da janela.

        Mesmo horizonte do MLWindowAdapter ([cutoff - 800d, cutoff +
        380d]): cobre as apostas do TRAIN (para a escolha do calibrador)
        e do TEST. As bases finais preveem; o meta combina.
        """
        from datetime import date, timedelta

        import numpy as np

        corpus = self.corpus
        base = date.fromisoformat(cutoff[:10])
        lower = (base - timedelta(days=_PREDICT_LOOKBACK_DAYS)).isoformat()
        upper = (base + timedelta(days=_PREDICT_HORIZON_DAYS)).isoformat()
        targets = [
            i for i, t in enumerate(corpus.times)
            if (base + timedelta(days=2)).isoformat() <= t[:10] <= upper
        ]
        if not targets:
            return {}
        x = corpus.matrix[targets]
        base_preds = [
            np.asarray(predict(x), dtype=float)
            for predict in (
                final_predictors[kind] for kind in self.kinds)
        ]
        stack = np.concatenate(base_preds, axis=1)
        preds = _map_classes(meta.predict_proba(stack), meta_classes)
        out: dict[tuple[str, str, str], tuple[float, float, float]] = {}
        for pos, i in enumerate(targets):
            key = corpus.rows[i]
            p = preds[pos]
            out[key] = (float(p[0]), float(p[1]), float(p[2]))
        return out
