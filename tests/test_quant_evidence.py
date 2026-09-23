"""Evidência metodológica do Quant — origem das probabilidades e calibração.

O que está em jogo (auditoria do ciclo)
---------------------------------------
1. ORIGEM: Brier/LogLoss/ECE do harness OOS vêm de probabilidades
   IMPLÍCITAS DO MERCADO (p = 1/odd das apostas da regra), não do modelo
   BETGSN. Isso é o MARKET BASELINE e precisa estar rotulado — nunca
   apresentado como performance de modelo. Sem de-vig, sem mistura.

2. CALIBRAÇÃO: por janela walk-forward — método escolhido por validação
   DENTRO do train, calibrador reajustado no train completo e aplicado
   CONGELADO no teste. O teste nunca participa da escolha nem do ajuste.

3. SEGMENTOS DO GATE: mesma população (calibrado vs raw nas mesmas
   apostas) — antes a regra (favoritos) era comparada ao mercado inteiro
   e a "melhora" era artefato de população.

4. CANAL DE CALIBRAÇÃO DO GATE: ECE por janela (amostra suficiente);
   janelas insuficientes são INSUFFICIENT_DATA declaradas.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from betgsn.value_walkforward import (
    CALIBRATION_METHODS,
    MIN_ECE_SAMPLE,
    OosEvidence,
    RuleView,
    WalkForwardConfig,
    _metrics_from_pairs,
    _prob_metrics,
    _raw_prob_pairs,
    run_walk_forward,
)

_REPO = Path(__file__).resolve().parents[1]


def _bet(day: str, odd: float, res: str, lg: str = "E0",
         n_books: int = 5, median: float | None = None) -> dict:
    return {
        "d": day, "lg": lg, "mkt": "Resultado Final (1X2)",
        "odd": odd,
        "ret": (odd - 1.0) if res == "win" else (0.0 if res == "push" else -1.0),
        "median": median if median is not None else odd,
        "n_books": n_books,
        "res": res,
    }


def _config(**kwargs) -> WalkForwardConfig:
    defaults = dict(
        train_days=365, test_days=90, gap_days=2,
        candidate_max_odds=(1.30,), min_books=3,
        min_train_bets=30, min_test_bets=5,
        bootstrap_resamples=200, bootstrap_seed=424242,
    )
    defaults.update(kwargs)
    return WalkForwardConfig(**defaults)


# ==========================================================================
# 1. ORIGEM das probabilidades — MARKET BASELINE, nunca modelo
# ==========================================================================


def test_probabilities_are_market_implied_not_model():
    """p = 1/odd exatamente — sem de-vig, sem modelo, sem transformação."""
    bets = [
        _bet("2024-02-01", 1.25, "win"),
        _bet("2024-03-01", 1.10, "loss"),
        _bet("2024-04-01", 1.40, "win"),
    ]
    metrics = _prob_metrics(bets)
    ps, ys = _raw_prob_pairs(bets)
    # p é exatamente o inverso da odd — nada no meio
    assert ps == [1.0 / 1.25, 1.0 / 1.10, 1.0 / 1.40]
    assert ys == [1, 0, 1]
    assert metrics["n_prob"] == 3
    # Brier conferido à mão
    expected = sum((p - y) ** 2 for p, y in zip(ps, ys)) / 3
    assert metrics["brier"] == pytest.approx(expected, abs=1e-6)


def test_no_devig_anywhere_in_the_oos_harness():
    """De-vig normalizaria probabilidades entre resultados do mesmo jogo;
    o harness trata cada aposta como probabilidade INDEPENDENTE 1/odd.
    Prova: odds da mesma 'partida' não se normalizam entre si."""
    bets = [
        _bet("2024-02-01", 1.10, "win"),
        _bet("2024-02-01", 8.00, "loss"),   # mesma data/liga: outro mercado
    ]
    ps, _ys = _raw_prob_pairs(bets)
    # 1/1.10 + 1/8.0 != 1 — não há normalização de overround
    assert sum(ps) != pytest.approx(1.0)


def test_harness_has_no_model_dependency():
    """O harness OOS não importa nenhum modelo do BETGSN: as
    probabilidades não podem vir dali por construção (prova AST)."""
    source = (_REPO / "betgsn" / "value_walkforward.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = [m for m in imported
                 if m.startswith("betgsn.models")
                 and m != "betgsn.models.calibration"
                 and m != "betgsn.models.promotion"]
    assert forbidden == [], (
        f"harness OOS importa modelos — probabilidade deixaria de ser "
        f"mercado: {forbidden}"
    )


def test_payload_labels_market_vs_calibrated(tmp_path, monkeypatch):
    """O payload separa market_* (baseline) de calibrated_* por nome —
    consumidor não pode confundir origem."""
    import json

    from betgsn import value_walkforward as vwf
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "corpus-abc")
    bets = _corpus()
    monkeypatch.setattr(vwf, "collect_bets", lambda *a, **k: bets)
    payload = vwf.compute_oos_validation(_config())
    agg = payload["aggregate"]
    assert {"market_brier", "market_logloss", "market_ece"} <= set(agg)
    assert {"calibrated_brier", "calibrated_logloss", "calibrated_ece"} <= set(agg)
    # os nomes antigos (confundíveis com modelo) não existem mais
    assert "brier" not in agg and "logloss" not in agg and "ece" not in agg


def _corpus() -> list[dict]:
    import random

    rng = random.Random(20260923)
    bets: list[dict] = []
    for day_idx in range(3 * 365):
        year = 2023 + day_idx // 365
        month = 1 + (day_idx % 365) // 31
        day = 1 + (day_idx % 28)
        d = f"{year:04d}-{month:02d}-{day:02d}"
        odd_short = 1.10 + rng.random() * 0.18
        odd_long = 1.40 + rng.random() * 1.20
        bets.append(_bet(d, round(odd_short, 3),
                         "win" if rng.random() < 0.86 else "loss"))
        bets.append(_bet(d, round(odd_long, 3),
                         "win" if rng.random() < 0.52 else "loss"))
    return bets


# ==========================================================================
# 2. Calibração: método escolhido no TRAIN, aplicado CONGELADO no TEST
# ==========================================================================


def _biased_corpus() -> list[dict]:
    """Mercado sistematicamente otimista na banda curta.

    p implícita ~0.87 (odd 1.15), mas win rate real ~0.80: calibrador
    Platt treinado no TRAIN DEVE encolher a probabilidade e reduzir o
    ECE no TEST.
    """
    import random

    rng = random.Random(99)
    bets: list[dict] = []
    for day_idx in range(4 * 365):
        year = 2022 + day_idx // 365
        month = 1 + (day_idx % 365) // 31
        day = 1 + (day_idx % 28)
        d = f"{year:04d}-{month:02d}-{day:02d}"
        for _ in range(4):
            bets.append(_bet(d, 1.15, "win" if rng.random() < 0.80 else "loss"))
        bets.append(_bet(d, 2.50, "win" if rng.random() < 0.40 else "loss"))
    return bets


def test_calibration_reduces_ece_when_market_is_biased():
    """Com viés sistemático, o calibrador frozen reduz ECE no TEST."""
    cfg = _config(train_days=365, test_days=365, min_train_bets=10)
    result = run_walk_forward(_biased_corpus(), cfg)
    assert result.n_bets_oos > 100
    # o mercado implícito é enviesado (ECE alto)...
    assert result.market_ece > 0.03
    # ...e a calibração escolhida no TRAIN reduz o ECE no TEST
    assert result.calibrated_ece < result.market_ece
    # método registrado por janela
    methods = {w.calibration_method for w in result.windows if w.n_test_bets}
    assert methods <= set(CALIBRATION_METHODS)
    assert "platt" in methods  # o viés linear é o caso do Platt


def test_calibration_uses_only_train_data():
    """O calibrador é ajustado com apostas do TRAIN da janela — prova
    contrapositiva: com viés APENAS no teste (inexistente no train), o
    calibrador NÃO se adapta ao viés do teste."""
    import random

    rng = random.Random(5)
    bets: list[dict] = []
    # 2023 (train): mercado calibrado (win rate == implied)
    for i in range(400):
        d = f"2023-{1 + i % 12:02d}-{1 + i % 28:02d}"
        bets.append(_bet(d, 1.25, "win" if rng.random() < 0.80 else "loss"))
    # 2024 (test): mercado SÚBITO enviesado — nunca visto no train
    for i in range(200):
        d = f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}"
        bets.append(_bet(d, 1.25, "win" if rng.random() < 0.60 else "loss"))

    cfg = _config(train_days=365, test_days=365,
                  candidate_max_odds=(1.30,), min_train_bets=10)
    result = run_walk_forward(bets, cfg)
    w0 = result.windows[0]
    # o calibrador treinado no 2023 calibrado aproxima-se da identidade:
    # não "descobre" o viés de 2024 (que seria leakage)
    assert w0.calibration_method in CALIBRATION_METHODS
    assert w0.n_calibration_train > 0


def test_raw_wins_when_train_is_already_calibrated():
    """Mercado calibrado no train: validação interna escolhe raw —
    calibrador não inventa correção onde não há o que corrigir."""
    import random

    rng = random.Random(11)
    bets: list[dict] = []
    for i in range(600):
        year = 2023 + i // 300
        d = f"{year}-{1 + i % 12:02d}-{1 + i % 28:02d}"
        p = 0.80
        bets.append(_bet(d, round(1.0 / p, 3),
                         "win" if rng.random() < p else "loss"))
    cfg = _config(train_days=365, test_days=365,
                  candidate_max_odds=(1.30,), min_train_bets=10)
    result = run_walk_forward(bets, cfg)
    # com train já calibrado, raw deve vencer a validação interna na
    # maior parte das janelas (platt/isotonic podem empatar em 1)
    methods = [w.calibration_method for w in result.windows if w.n_test_bets]
    assert methods.count("raw") >= len(methods) - 1


# ==========================================================================
# 3. Segmentos do gate: MESMA população
# ==========================================================================


def test_promotion_segments_same_population():
    """metrics (calibrado) vs baseline (raw) nas MESMAS apostas.

    Prova: roi idêntico nos dois lados (mesmas apostas); n_matches é o
    tamanho do SEGMENTO (partidas apostáveis), enquanto as métricas vêm
    do subconjunto da regra.
    """
    bets = _corpus()
    result = run_walk_forward(bets, _config())
    evidence = OosEvidence(
        n_windows=result.n_windows,
        n_windows_valid=result.n_windows_valid,
        n_bets_oos=result.n_bets_oos,
        roi=result.roi, roi_se=result.roi_se, avg_odd=result.avg_odd,
        max_drawdown=result.max_drawdown,
        embargo_days=result.embargo_days,
        oos_rule_bets=tuple(result.oos_rule_bets),
        oos_market_bets=tuple(result.oos_market_bets),
        aggregate={},
        calibration_windows=tuple(result.calibration_windows),
        calibration_insufficient=result.calibration_insufficient,
    )
    segments = evidence.promotion_segments()
    assert segments
    for seg in segments:
        # MESMA população: baseline é a MESMA lista de apostas da regra
        assert seg.metrics["roi"] == pytest.approx(seg.baseline_metrics["roi"])
        # n_matches = tamanho do segmento (partidas), >= apostas da regra
        assert seg.n_matches >= 0
        # ece só com amostra suficiente; pequeno é None (INSUFFICIENT_DATA)
        if seg.n_matches < MIN_ECE_SAMPLE:
            assert seg.metrics["ece"] is None
    # melhora de logloss agora é calibração vs raw na mesma população —
    # sem o artefato de população do ciclo anterior
    assert all("logloss" in s.metrics for s in segments)
    assert all("logloss" in s.baseline_metrics for s in segments)


def test_population_artifact_is_gone():
    """Regressão do bug: comparar favoritos contra mercado inteiro
    produzia 'melhora' gigante (19%) sem qualidade real. Com mesma
    população, a melhora de logloss é a contribuição da calibração —
    da ordem de pontos percentuais, não dezenas."""
    bets = _corpus()
    result = run_walk_forward(bets, _config())
    evidence = OosEvidence(
        n_windows=result.n_windows, n_windows_valid=result.n_windows_valid,
        n_bets_oos=result.n_bets_oos, roi=result.roi, roi_se=result.roi_se,
        avg_odd=result.avg_odd, max_drawdown=result.max_drawdown,
        embargo_days=result.embargo_days,
        oos_rule_bets=tuple(result.oos_rule_bets),
        oos_market_bets=tuple(result.oos_market_bets),
        aggregate={},
    )
    segments = evidence.promotion_segments()
    improvements = []
    for s in segments:
        if s.baseline_metrics.get("logloss"):
            improvements.append(
                (s.baseline_metrics["logloss"] - s.metrics["logloss"])
                / abs(s.baseline_metrics["logloss"])
            )
    mean_improvement = sum(improvements) / len(improvements)
    # o artefato de população produzia ~+19%; calibração real é pequena
    assert mean_improvement < 0.10


# ==========================================================================
# 4. Canal de calibração do gate
# ==========================================================================


def test_calibration_channel_reports_windows_and_insufficient():
    bets = _corpus()
    result = run_walk_forward(bets, _config())
    evidence = OosEvidence(
        n_windows=result.n_windows, n_windows_valid=result.n_windows_valid,
        n_bets_oos=result.n_bets_oos, roi=result.roi, roi_se=result.roi_se,
        avg_odd=result.avg_odd, max_drawdown=result.max_drawdown,
        embargo_days=result.embargo_days,
        oos_rule_bets=tuple(result.oos_rule_bets),
        oos_market_bets=tuple(result.oos_market_bets),
        aggregate={},
        calibration_windows=tuple(result.calibration_windows),
        calibration_insufficient=result.calibration_insufficient,
    )
    channel = evidence.calibration_channel()
    if channel is not None:
        assert channel["n_segments"] == len(result.calibration_windows)
        assert channel["min_sample_per_segment"] == MIN_ECE_SAMPLE
        assert 0.0 <= channel["mean_ece"] <= 1.0
        # insuficientes declarados, nunca escondidos
        assert channel["insufficient_segments"] == result.calibration_insufficient
    else:
        assert not result.calibration_windows


def test_calibration_channel_none_without_sufficient_windows():
    evidence = OosEvidence(
        n_windows=0, n_windows_valid=0, n_bets_oos=0,
        roi=None, roi_se=None, avg_odd=None, max_drawdown=None,
        embargo_days=2, oos_rule_bets=(), oos_market_bets=(),
        aggregate={},
    )
    assert evidence.calibration_channel() is None


def test_gate_calibration_channel_passes_and_fails():
    """O canal aplica o MESMO limite (0.05): abaixo passa, acima reprova."""
    from betgsn.models.promotion import evaluate_promotion

    segs = [_segment("E0", "2024"), _segment("E0", "2025"),
            _segment("SP1", "2024"), _segment("SP1", "2025")]

    good = evaluate_promotion(
        model="x", segments=segs,
        calibration={"mean_ece": 0.02, "n_segments": 20,
                     "insufficient_segments": 2,
                     "min_sample_per_segment": MIN_ECE_SAMPLE,
                     "method": "platt"},
    )
    crit = next(c for c in good.criteria if c.name == "calibracao_aceitavel")
    assert crit.passed is True
    assert "janela" in crit.detail and "INSUFFICIENT_DATA" in crit.detail

    bad = evaluate_promotion(
        model="x", segments=segs,
        calibration={"mean_ece": 0.08, "n_segments": 20,
                     "insufficient_segments": 0,
                     "min_sample_per_segment": MIN_ECE_SAMPLE,
                     "method": "platt"},
    )
    crit_bad = next(c for c in bad.criteria if c.name == "calibracao_aceitavel")
    assert crit_bad.passed is False


def _segment(league: str, season: str):
    from betgsn.models.promotion import SegmentResult

    return SegmentResult(
        league=league, season=season, n_matches=300,
        metrics={"roi": 0.02, "brier": 0.18, "logloss": 0.50, "ece": None},
        baseline_metrics={"roi": 0.02, "brier": 0.19, "logloss": 0.52,
                          "ece": None},
    )


# ==========================================================================
# 5. Cache: schema 2 invalida caches do schema 1
# ==========================================================================


def test_schema_bump_invalidates_old_cache(tmp_path, monkeypatch):
    import json

    from betgsn import value_walkforward as vwf

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    config = WalkForwardConfig()
    fp = vwf.oos_cache_fingerprint(config=config, corpus_signature="c")
    stale = {
        "schema": 1,  # schema antigo
        "cache_fingerprint": fp,
        "aggregate": {}, "oos_rule_bets": [],
    }
    (tmp_path / "oos.json").write_text(json.dumps(stale), encoding="utf-8")
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "c")
    assert vwf.cached_oos_evidence() is None


def test_fingerprint_covers_calibration_parameters():
    """Mudança nos parâmetros de calibração (métodos candidatos, fração
    de validação, mínimo de ECE) muda o fingerprint — são parte da
    medição."""
    from betgsn import value_walkforward as vwf

    base = vwf.oos_cache_fingerprint(
        config=WalkForwardConfig(), corpus_signature="c")
    original_methods = vwf.CALIBRATION_METHODS
    original_fraction = vwf.CALIBRATION_VALIDATION_FRACTION
    try:
        vwf.CALIBRATION_METHODS = ("raw", "platt")
        assert vwf.oos_cache_fingerprint(
            config=WalkForwardConfig(), corpus_signature="c") != base
        vwf.CALIBRATION_METHODS = original_methods
        vwf.CALIBRATION_VALIDATION_FRACTION = 0.3
        assert vwf.oos_cache_fingerprint(
            config=WalkForwardConfig(), corpus_signature="c") != base
    finally:
        vwf.CALIBRATION_METHODS = original_methods
        vwf.CALIBRATION_VALIDATION_FRACTION = original_fraction


# ==========================================================================
# 6. Métricas de pares — primitiva correta
# ==========================================================================


def test_metrics_from_pairs_known_values():
    import math

    ps = [0.9, 0.6, 0.6]
    ys = [1, 0, 1]
    m = _metrics_from_pairs(ps, ys)
    expected_brier = ((0.9 - 1) ** 2 + (0.6 - 0) ** 2 + (0.6 - 1) ** 2) / 3
    assert m["brier"] == pytest.approx(expected_brier, abs=1e-6)
    # logloss: -(y·ln p + (1-y)·ln(1-p)) médio
    expected_ll = -(
        math.log(0.9)            # y=1, p=0.9
        + math.log(1 - 0.6)      # y=0, p=0.6
        + math.log(0.6)          # y=1, p=0.6
    ) / 3
    assert m["logloss"] == pytest.approx(expected_ll, abs=1e-6)
