"""Ensemble (stacking OOS por janela) nas MESMAS 24 janelas da Etapa 19.

O que esta suíte prova:

  - o adapter de ensemble faz stacking REAL: o meta-modelo é treinado
    SOMENTE em previsões out-of-sample das bases (rolling-origin folds
    DENTRO do TRAIN da própria janela — cada fold previsto por bases
    ajustadas apenas em partidas anteriores à fronteira do fold);
  - nenhuma base e nenhum fold enxerga partidas >= train_end (o
    gap/embargo e o TEST ficam fora do treino por construção, e o
    audit trail do adapter permite verificar);
  - as bases finais (as que preveem o TEST) são ajustadas apenas no
    TRAIN completo da janela e ficam congeladas;
  - o ensemble entra no MESMO harness (`run_model_walkforward`) — sem
    caminho paralelo de avaliação;
  - determinismo: mesmo corpus, mesmo corte -> mesmas previsões;
  - separação de fontes: mudar as ODDS do mercado muda as métricas de
    mercado e NÃO muda as métricas do ensemble (e vice-versa);
    strategy_market não consome a probabilidade do ensemble.
"""

from __future__ import annotations

import pytest

from betgsn.ml_walkforward import (
    ENSEMBLE_BASE_KINDS,
    ENSEMBLE_KIND,
    EnsembleWindowAdapter,
    build_feature_corpus,
)
from betgsn.model_walkforward import run_model_walkforward
from test_ml_walkforward import _bets, _config, _matches

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _corpus(n_days: int = 400):
    matches = _matches(n_days)
    return matches, build_feature_corpus(matches)


# ---------------------------------------------------------------------------
# constantes declaradas
# ---------------------------------------------------------------------------


def test_ensemble_constants_declared():
    assert ENSEMBLE_KIND == "ensemble"
    assert ENSEMBLE_BASE_KINDS == ("elo", "xgboost", "lightgbm")
    # as bases do ensemble são exatamente os kinds avaliados
    # individualmente no mesmo protocolo
    from betgsn.ml_walkforward import ML_MODEL_KINDS

    assert ENSEMBLE_BASE_KINDS == ML_MODEL_KINDS


def test_ensemble_unknown_base_kind_rejected():
    _m, corpus = _corpus(60)
    with pytest.raises(ValueError, match="kind"):
        EnsembleWindowAdapter(corpus, kinds=("nope",))


def test_ensemble_zero_folds_rejected():
    _m, corpus = _corpus(60)
    with pytest.raises(ValueError, match="folds"):
        EnsembleWindowAdapter(corpus, kinds=("elo",), n_folds=0)


# ---------------------------------------------------------------------------
# contrato do frozen view
# ---------------------------------------------------------------------------


def test_ensemble_fit_returns_frozen_view_with_contract():
    matches, corpus = _corpus(400)
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo",), n_folds=2, min_stack_rows=50)
    frozen = adapter.fit(matches, "2022-09-01")
    assert frozen is not None
    assert frozen.n_matches > 0
    probs = frozen.prob_1x2("Strong", "Opp0", day="2022-09-15")
    assert probs is None  # Opp0 só existe no início do corpus
    # previsão de um dia DENTRO do horizonte, para um confronto existente
    last_strong = next(m for m in reversed(matches) if m.home == "Strong")
    day = last_strong.kickoff[:10]
    probs = frozen.prob_1x2("Strong", last_strong.away, day=day)
    assert probs is not None
    p1, px, p2 = probs
    assert 0.0 <= p1 <= 1.0 and 0.0 <= px <= 1.0 and 0.0 <= p2 <= 1.0
    assert p1 + px + p2 == pytest.approx(1.0, abs=1e-6)
    # par desconhecido: None — nunca probabilidade inventada
    assert frozen.prob_1x2("Ninguem", "Nada", day="2022-09-15") is None


def test_ensemble_insufficient_train_returns_none():
    _m, corpus = _corpus(10)
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo",), n_folds=2, min_stack_rows=10)
    assert adapter.fit(_m, "2022-02-01") is None


def test_ensemble_fit_only_uses_matches_before_train_end():
    matches, corpus = _corpus(400)
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo",), n_folds=2, min_stack_rows=50)
    early = adapter.fit(matches, "2022-09-01")
    late = adapter.fit(matches, "2023-05-01")
    assert early is not None and late is not None
    assert late.n_matches > early.n_matches


# ---------------------------------------------------------------------------
# stacking sem leakage: audit trail dos folds
# ---------------------------------------------------------------------------


def test_ensemble_stack_folds_are_oos_and_inside_train():
    """Cada fold do stacking é previsto por bases treinadas APENAS em
    partidas anteriores à fronteira do fold; e o meta-modelo só consome
    essas previsões OOS — nunca partidas do TEST/gap."""
    matches, corpus = _corpus(400)
    train_end = "2022-11-01"
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo", "xgboost", "lightgbm"), n_folds=2,
        min_early_stop_rows=20, min_stack_rows=50)
    frozen = adapter.fit(matches, train_end)
    assert frozen is not None

    audit = adapter.last_stack_audit
    assert audit is not None
    assert audit["train_end"] == train_end
    assert len(audit["folds"]) == 2

    for fold in audit["folds"]:
        # base treinada estritamente antes da primeira previsão do fold
        assert fold["base_train_end"] < fold["first_prediction"]
        # fold inteiro dentro do TRAIN (nada do gap/TEST vira meta-dado)
        assert fold["last_prediction"] < train_end
        assert fold["n_base_rows"] > 0 and fold["n_rows"] > 0

    # meta-modelo treinado apenas nos rows OOS dos folds
    meta = audit["meta"]
    assert meta["n_rows"] == sum(f["n_rows"] for f in audit["folds"])
    assert meta["last_time"] < train_end
    # bases finais (as que preveem o TEST): apenas TRAIN da janela
    assert audit["final_bases"]["last_time"] < train_end
    assert audit["final_bases"]["n_rows"] == frozen.n_matches


def test_ensemble_skipped_folds_are_declared():
    """Fold sem early-stop utilizável é DECLARADO no audit — nunca
    contornado com dado sobreposto."""
    matches, corpus = _corpus(400)
    # min_early_stop_rows gigante: nenhum fold de booster consegue split
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo", "xgboost"), n_folds=2,
        min_early_stop_rows=10_000_000, min_stack_rows=50)
    frozen = adapter.fit(matches, "2022-11-01")
    # fold com booster é pulado; o fold de elo também é pulado (fold
    # inteiro exige TODAS as bases) -> sem meta rows -> janela sem
    # ensemble, declarado
    assert frozen is None


# ---------------------------------------------------------------------------
# determinismo
# ---------------------------------------------------------------------------


def test_ensemble_deterministic_repeatable():
    matches, corpus = _corpus(300)
    kwargs = dict(kinds=("elo", "xgboost", "lightgbm"), n_folds=2,
                  min_early_stop_rows=20, min_stack_rows=50)
    frozen_a = EnsembleWindowAdapter(corpus, **kwargs).fit(
        matches, "2022-09-01")
    frozen_b = EnsembleWindowAdapter(corpus, **kwargs).fit(
        matches, "2022-09-01")
    assert frozen_a is not None and frozen_b is not None
    # mesmas previsões para TODAS as chaves do corpus no horizonte
    checked = 0
    for (home, away, day) in corpus.rows:
        pa = frozen_a.prob_1x2(home, away, day=day)
        pb = frozen_b.prob_1x2(home, away, day=day)
        assert pa == pb
        if pa is not None:
            checked += 1
    assert checked > 0


# ---------------------------------------------------------------------------
# harness: MESMO caminho das 24 janelas
# ---------------------------------------------------------------------------


def test_run_model_walkforward_with_ensemble_provider():
    matches = _matches(500)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo", "xgboost", "lightgbm"), n_folds=2,
        min_early_stop_rows=20, min_stack_rows=50)

    result = run_model_walkforward(
        bets, matches, _config(), model_fn=adapter.fit)

    assert result.n_bets_oos > 0
    assert result.model_raw.brier is not None
    assert result.market_raw.brier is not None
    assert result.market_fair.brier is not None
    # separação: performance do modelo e a strategy_model reportadas
    # separadas (com line-shopping declarado)
    assert "strategy_model" in result.to_dict()


def test_ensemble_predictions_do_not_follow_market_odds():
    """Separação de fontes (Parte 10): mudar as ODDS muda as métricas de
    MERCADO e não muda as do ENSEMBLE — model probability nunca é
    derivada de market probability."""
    matches = _matches(500)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)
    kwargs = dict(kinds=("elo", "xgboost", "lightgbm"), n_folds=2,
                 min_early_stop_rows=20, min_stack_rows=50)

    result_a = run_model_walkforward(
        bets, matches, _config(),
        model_fn=EnsembleWindowAdapter(corpus, **kwargs).fit)

    # mesmas partidas, mesmas chaves — só as ODDS mudam
    bets_alt = [
        dict(b, odd=b["odd"] * 1.5, fair=b["fair"] * 0.9)
        for b in bets
    ]
    result_b = run_model_walkforward(
        bets_alt, matches, _config(),
        model_fn=EnsembleWindowAdapter(corpus, **kwargs).fit)

    # mercado reagiu às odds novas; o ensemble é o mesmo modelo
    assert result_b.market_raw.brier != pytest.approx(
        result_a.market_raw.brier, abs=1e-9)
    assert result_b.model_raw.brier == pytest.approx(
        result_a.model_raw.brier, abs=1e-12)
    assert result_b.n_bets_oos == result_a.n_bets_oos


def test_strategy_market_does_not_consume_ensemble(monkeypatch):
    """strategy_market (favoritos curtos) é regra de mercado: probabilidades
    do ensemble NÃO participam — um ensemble 'quebrado' não muda a
    strategy_market."""
    matches = _matches(500)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)

    result_a = run_model_walkforward(
        bets, matches, _config(),
        model_fn=EnsembleWindowAdapter(
            corpus, kinds=("elo",), n_folds=2, min_stack_rows=50).fit)

    class _GarbageView:
        n_matches = 99999

        def prob_1x2(self, home, away, day=None):
            # probabilidade absurda, sempre a mesma: se algo do caminho
            # market consumisse o modelo, o resultado mudaria
            return (0.98, 0.01, 0.01)

    def _garbage_fit(_matches, train_end):
        return _GarbageView()

    result_b = run_model_walkforward(
        bets, matches, _config(), model_fn=_garbage_fit)

    for wa, wb in zip(result_a.windows, result_b.windows):
        assert wb.strategy_market_n == wa.strategy_market_n
        assert wb.strategy_market_roi == wa.strategy_market_roi
    # o modelo de verdade mudou (ensemble vs garbage) — sanity de que o
    # teste não é vazio
    assert result_b.model_raw.brier != pytest.approx(
        result_a.model_raw.brier, abs=1e-9)


# ---------------------------------------------------------------------------
# linhas de predição por aposta (insumo da análise por odd band)
# ---------------------------------------------------------------------------


def test_collect_rows_exposes_per_bet_predictions():
    """collect_rows expõe (window, odd, p_raw, p_fair, p_model, p_cal, y)
    por aposta do TEST — população EXATA da avaliação, insumo do
    breakdown por odd band sem re-rodar nada."""
    matches = _matches(500)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo",), n_folds=2, min_stack_rows=50)

    result = run_model_walkforward(
        bets, matches, _config(), model_fn=adapter.fit, collect_rows=True)

    rows = result.prediction_rows
    assert len(rows) == result.n_bets_oos
    assert len(rows) > 0
    for row in rows:
        window, odd, p_raw, p_fair, p_model, p_cal, y = row
        assert isinstance(window, int)
        assert odd == 1.25  # odd das bets sintéticas
        assert p_raw == pytest.approx(1.0 / odd)
        assert 0.0 <= p_model <= 1.0
        assert y in (0, 1)
    # windows declarados correspondem às janelas válidas
    valid_windows = {w.index for w in result.windows if w.n_test_bets > 0}
    assert {row[0] for row in rows} == valid_windows
    # rows NÃO serializam (o cache continua leve)
    assert "prediction_rows" not in result.to_dict()


def test_odds_band_breakdown_from_prediction_rows():
    """Breakdown por odd band sobre as prediction_rows: mesmas métricas
    do harness, população declarada por banda, bandas pequenas
    INSUFFICIENT_DATA."""
    from tools.ml_oos_validation import odds_band_breakdown

    rows = []
    for i in range(400):
        # odd alterna entre bandas < 1.40 e 1.40-2.00
        odd = 1.20 if i % 2 == 0 else 1.60
        p_model = 0.75 if i % 2 == 0 else 0.5
        y = 1 if i % 3 == 0 else 0
        rows.append((0, odd, 1.0 / odd, 1.0 / odd, p_model, p_model, y))

    bands = odds_band_breakdown(rows)
    by_band = {b["band"]: b for b in bands}
    assert set(by_band) == {"< 1.40", "1.40-2.00", "2.00-3.00", ">= 3.00"}

    lo = by_band["< 1.40"]
    assert lo["n"] == 200
    assert lo["model_raw"]["n"] == 200
    assert lo["market_raw"]["n"] == 200
    assert lo["delta_logloss_model_vs_market_raw"] is not None

    # banda vazia: INSUFFICIENT_DATA com n=0, sem métricas fabricadas
    empty = by_band[">= 3.00"]
    assert empty["n"] == 0
    assert empty["status"] == "INSUFFICIENT_DATA"
    assert "model_raw" not in empty


def test_ensemble_tool_payload_is_json_serializable():
    """O caminho completo do tool — _evaluate_adapter(collect_rows) ->
    odds_band_breakdown -> _ensemble_payload -> json.dumps(allow_nan=False)
    — não explode: é o contrato do artefato final."""
    import json

    from betgsn.value_walkforward import WalkForwardConfig
    from tools.ml_oos_validation import (
        _ensemble_payload,
        _evaluate_adapter,
        odds_band_breakdown,
    )

    matches = _matches(500)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)
    adapter = EnsembleWindowAdapter(
        corpus, kinds=("elo", "xgboost"), n_folds=2,
        min_early_stop_rows=20, min_stack_rows=50)

    result = _evaluate_adapter(
        adapter, bets, matches, _config(), collect_rows=True)
    rows = result.pop("prediction_rows")
    assert len(rows) == result["n_bets_oos"]

    result["odds_bands"] = odds_band_breakdown(rows)
    # protocolo do que RODOU (kinds/n_folds do adapter), como no main()
    payload = _ensemble_payload(result, _config(), {
        "base_models": ["elo", "xgboost"], "n_folds": 2,
    })
    text = json.dumps(payload, allow_nan=False)
    assert json.loads(text)["status"] == "OK"
    assert "prediction_rows" not in payload
    assert payload["stacking_protocol"]["n_folds"] == 2
    assert payload["stacking_protocol"]["base_models"] == ["elo", "xgboost"]
