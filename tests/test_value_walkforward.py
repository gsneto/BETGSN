"""Walk-forward OOS da estratégia de valor — contratos temporais e estatísticos.

O que está em jogo
------------------
A validação full-sample escolheu a banda de odd olhando o corpus inteiro.
Este teste fixa o processo HONESTO: banda escolhida no TRAIN, medida no
TEST, gap/embargo entre blocos, janelas sem sobreposição, estatística
OOS (ROI/Wilson/bootstrap/Brier/LogLoss/ECE), cache com fingerprint que
inclui TODOS os parâmetros de walk-forward, robustez e ablação OOS.

Tudo determinístico: apostas sintéticas, sem rede, sem corpus.
"""

from __future__ import annotations

import json
import math

import pytest

from betgsn.value_walkforward import (
    DEFAULT_GAP_DAYS,
    OOS_CACHE_SCHEMA_VERSION,
    OosEvidence,
    RuleView,
    WalkForwardConfig,
    ablation_scenarios,
    audit_windows,
    build_windows,
    cached_oos_evidence,
    compute_oos_validation,
    oos_cache_fingerprint,
    prospective_clv_evidence,
    robustness_scenarios,
    run_walk_forward,
    save_oos_validation,
    wilson_ci,
)

# ------------------------------------------------------------------ helpers


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


def _corpus() -> list[dict]:
    """Corpus sintético determinístico com EDGE REAL na banda baixa.

    3 anos de apostas diárias: odds < 1.20 ganham 88% (edge), odds
    1.20-1.30 ganham 82% (leve edge), odds >= 1.30 ganham 70% (prejuízo,
    como o mercado cobra). O processo de seleção deve escolher a banda
    baixa no TRAIN e a medição TEST deve refletir o edge real.
    """
    import random

    rng = random.Random(20260923)
    bets: list[dict] = []
    year_days = 365
    for day_idx in range(3 * year_days):
        year = 2023 + day_idx // year_days
        month = 1 + (day_idx % year_days) // 31
        day = 1 + (day_idx % 28)
        d = f"{year:04d}-{month:02d}-{day:02d}"
        # uma aposta curta (regra) e uma longa (mercado) por dia
        odd_short = 1.10 + rng.random() * 0.18   # 1.10-1.28
        odd_long = 1.40 + rng.random() * 1.20
        bets.append(_bet(d, round(odd_short, 3),
                         "win" if rng.random() < 0.86 else "loss"))
        bets.append(_bet(d, round(odd_long, 3),
                         "win" if rng.random() < 0.52 else "loss"))
    return bets


def _config(**kwargs) -> WalkForwardConfig:
    defaults = dict(
        train_days=365, test_days=90, gap_days=DEFAULT_GAP_DAYS,
        candidate_max_odds=(1.15, 1.20, 1.25, 1.30),
        min_books=3, min_train_bets=30, min_test_bets=5,
        bootstrap_resamples=200, bootstrap_seed=424242,
    )
    defaults.update(kwargs)
    return WalkForwardConfig(**defaults)


# ==========================================================================
# 1. Janelas: gap/embargo, adjacência, auditoria
# ==========================================================================


def test_windows_respect_gap_and_never_overlap():
    windows = build_windows("2023-01-01", "2024-12-31", _config())
    assert len(windows) >= 2
    assert audit_windows(windows) == []
    for w in windows:
        from datetime import date

        gap = (date.fromisoformat(w.test_start)
               - date.fromisoformat(w.train_end)).days
        assert gap == DEFAULT_GAP_DAYS
        assert w.test_end > w.test_start > w.train_end > w.train_start


def test_windows_audit_detects_missing_embargo():
    """gap_days=0 declarado deve aparecer na auditoria quando exigido."""
    from datetime import date

    windows = build_windows("2023-01-01", "2024-12-31", _config(gap_days=2))
    # sabotagem: janela com embargo removido
    sabotaged = list(windows)
    w = sabotaged[0]
    sabotaged[0] = type(w)(
        index=w.index, train_start=w.train_start, train_end=w.train_end,
        test_start=w.train_end,  # sem gap!
        test_end=w.test_end, gap_days=w.gap_days,
    )
    violations = audit_windows(sabotaged)
    assert any("embargo" in v for v in violations)


def test_gap_days_zero_is_allowed_but_registered():
    windows = build_windows("2023-01-01", "2024-06-30", _config(gap_days=0))
    assert all(w.gap_days == 0 for w in windows)
    assert audit_windows(windows) == []


# ==========================================================================
# 2. PIT/leakage do harness
# ==========================================================================


def test_bets_in_gap_are_excluded_from_train_and_test():
    """Aposta no embargo (entre train_end e test_start) não entra em nada."""
    cfg = _config(train_days=365, test_days=90, gap_days=5,
                  candidate_max_odds=(1.30,), min_train_bets=1,
                  min_test_bets=1)
    bets = [
        _bet("2023-01-01", 1.10, "win"),   # train [2023-01-01, 2024-01-01)
        _bet("2024-01-03", 1.10, "win"),   # GAP (2024-01-01..2024-01-06)
        _bet("2024-03-01", 1.10, "win"),   # test [2024-01-06, 2024-04-06)
    ]
    result = run_walk_forward(bets, cfg)
    # só a aposta de teste entrou no OOS; a do gap, nunca
    assert result.n_bets_oos == 1


def test_test_bets_strictly_after_train_window():
    cfg = _config(min_train_bets=1, min_test_bets=1)
    bets = [
        _bet("2023-06-01", 1.10, "win"),
        _bet("2024-01-05", 1.10, "win"),
        _bet("2024-03-01", 1.10, "win"),
    ]
    result = run_walk_forward(bets, cfg)
    for w in result.windows:
        if w.n_test_bets:
            assert w.test_start >= w.train_end


def test_selection_uses_only_train_data():
    """A banda escolhida no TRAIN é a que tem melhor limite inferior LÁ.

    Corpus onde a banda baixa tem edge no primeiro ano (train da janela
    1) e a banda alta passa a ter edge depois: a seleção da janela 1 não
    pode "ver" o futuro — a banda escolhida reflete o train, e o OOS
    mede o que a escolha rendeu no test (possivelmente pior).
    """
    import random

    rng = random.Random(7)
    bets: list[dict] = []
    # ano 2023 (TRAIN da janela 1): banda 1.10-1.15 com edge grande
    for i in range(120):
        bets.append(_bet(f"2023-{1 + i % 12:02d}-{1 + i % 28:02d}",
                         1.12, "win" if rng.random() < 0.95 else "loss"))
        bets.append(_bet(f"2023-{1 + i % 12:02d}-{1 + i % 28:02d}",
                         1.28, "win" if rng.random() < 0.70 else "loss"))
    # 2024 (TEST da janela 1): a banda 1.10-1.15 PERDEU o edge
    for i in range(60):
        bets.append(_bet(f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}",
                         1.12, "win" if rng.random() < 0.70 else "loss"))
        bets.append(_bet(f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}",
                         1.28, "win" if rng.random() < 0.90 else "loss"))

    cfg = _config(train_days=365, test_days=365,
                  candidate_max_odds=(1.15, 1.30), min_train_bets=50,
                  min_test_bets=5)
    result = run_walk_forward(bets, cfg)
    w0 = result.windows[0]
    # a seleção no train escolheu a banda baixa (edge de 95% ali)
    assert w0.selected_max_odd == 1.15
    # mas o OOS da janela 1 registrou o que essa escolha rendeu no test:
    # com ~70% de win a 1.12, o ROI é negativo — registrado honestamente
    assert w0.roi is not None
    assert w0.roi < 0.0


# ==========================================================================
# 3. Métricas OOS
# ==========================================================================


def test_oos_aggregate_metrics_on_known_bets():
    """ROI/Brier/LogLoss/ECE batem com o calculado à mão nas apostas OOS."""
    cfg = _config(min_train_bets=1, min_test_bets=1,
                  candidate_max_odds=(1.30,))
    bets = [
        # train: 3 wins a 1.10 (edge claro para a banda)
        _bet("2023-01-01", 1.10, "win"),
        _bet("2023-02-01", 1.10, "win"),
        _bet("2023-03-01", 1.10, "win"),
        # test: 2 wins, 1 loss, 1 push a 1.10
        _bet("2024-02-01", 1.10, "win"),
        _bet("2024-03-01", 1.10, "win"),
        _bet("2024-04-01", 1.10, "loss"),
        _bet("2024-05-01", 1.10, "push"),
    ]
    result = run_walk_forward(bets, cfg)
    assert result.n_bets_oos == 4
    # ROI: (0.1 + 0.1 - 1.0 + 0.0) / 4
    assert result.roi == pytest.approx((0.1 + 0.1 - 1.0) / 4, abs=1e-6)
    # MARKET Brier: p=1/1.1=0.9090..; y=[1,1,0] (push fora)
    p = 1.0 / 1.10
    expected_brier = ((p - 1) ** 2 + (p - 1) ** 2 + p ** 2) / 3
    assert result.market_brier == pytest.approx(expected_brier, abs=1e-5)
    # LogLoss idem
    import math as _math

    expected_ll = -(
        _math.log(p) + _math.log(p) + _math.log(1 - p)
    ) / 3
    assert result.market_logloss == pytest.approx(expected_ll, abs=1e-5)
    # Wilson: wins=2, losses=1
    lo, hi = wilson_ci(2, 1)
    assert result.wilson_low == pytest.approx(lo, abs=1e-6)
    assert result.wilson_high == pytest.approx(hi, abs=1e-6)
    # drawdown registrado
    assert result.max_drawdown is not None and result.max_drawdown >= 0
    # calibracao presente (raw quando o train nao sustenta ajuste)
    assert result.calibrated_ece is not None


def test_window_without_train_sample_selects_nothing():
    cfg = _config(min_train_bets=100, candidate_max_odds=(1.30,))
    bets = [_bet("2023-01-01", 1.10, "win"), _bet("2024-02-01", 1.10, "win")]
    result = run_walk_forward(bets, cfg)
    assert all(w.selected_max_odd is None for w in result.windows)
    assert result.n_bets_oos == 0
    assert result.roi is None


def test_wilson_ci_reference_values():
    lo, hi = wilson_ci(80, 20)   # p=0.8, n=100
    # valores de referência da fórmula de Wilson (z=1.95996...)
    assert lo == pytest.approx(0.71116, abs=1e-4)
    assert hi == pytest.approx(0.86663, abs=1e-4)
    # simetria básica em torno da estimativa ajustada
    assert lo < 0.8 < hi


def test_full_synthetic_corpus_walk_forward():
    """Corpus 3 anos com edge na banda baixa: OOS positivo e multi-janela."""
    bets = _corpus()
    result = run_walk_forward(bets, _config())
    assert result.n_windows >= 4
    assert result.n_windows_valid >= 3
    assert result.n_bets_oos >= 100
    assert result.roi is not None and result.roi > 0
    assert result.roi_t is not None and result.roi_t > 2
    # por janela: n/limites registrados
    for w in result.windows:
        if w.n_test_bets:
            assert w.roi is not None
            assert w.wilson_low is not None
    # mercado (baseline do promotion) tem MAIS apostas que a regra
    assert len(result.oos_market_bets) > len(result.oos_rule_bets)


# ==========================================================================
# 4. Robustez e ablação OOS
# ==========================================================================


def test_robustness_scenarios_record_degradation():
    bets = _corpus()
    scenarios = robustness_scenarios(bets, _config())
    names = {s["scenario"] for s in scenarios}
    assert names == {"max_odd=1.25", "max_odd=1.35",
                     "min_books=2", "min_books=4"}
    for s in scenarios:
        assert s["n_bets_oos"] > 0
        assert s["roi"] is not None
        assert "degradation_vs_baseline" in s


def test_ablation_scenarios_isolate_components():
    bets = _corpus()
    out = ablation_scenarios(bets, _config())
    names = [s["scenario"] for s in out]
    assert names[0].startswith("baseline")
    assert any("sem_line_shopping" in n for n in names)
    assert any("sem_consenso" in n for n in names)
    for s in out:
        assert s["roi"] is not None
        assert "delta_vs_baseline" in s


def test_median_ablation_changes_the_odds_used():
    """use_median realmente usa a mediana: odd alta com mediana baixa
    não passa na banda — e o ROI muda em relação ao best-odd."""
    cfg = _config(min_train_bets=1, min_test_bets=1,
                  candidate_max_odds=(1.30,))
    bets = [
        _bet("2023-01-01", 1.10, "win"),
        _bet("2024-02-01", 1.28, "win", median=1.05),
        _bet("2024-03-01", 1.28, "win", median=1.05),
    ]
    best = run_walk_forward(bets, cfg, RuleView(max_odd=1.30))
    median = run_walk_forward(bets, cfg, RuleView(max_odd=1.30, use_median=True))
    # best-odd: as apostas de 1.28 entram (odd < 1.30)
    assert best.n_bets_oos == 2
    # mediana 1.05 também entra, mas com OUTRO odd -> outro ROI
    assert median.n_bets_oos == 2
    assert median.roi != pytest.approx(best.roi)


# ==========================================================================
# 5. Cache: fingerprint cobre TODOS os parâmetros de walk-forward
# ==========================================================================


def _fp(**config_kwargs) -> str:
    corpus = "corpus-abc"
    return oos_cache_fingerprint(
        config=_config(**config_kwargs), corpus_signature=corpus,
    )


def test_fingerprint_changes_with_every_wf_parameter():
    base = _fp()
    assert _fp(train_days=366) != base
    assert _fp(test_days=91) != base
    assert _fp(gap_days=3) != base
    assert _fp(candidate_max_odds=(1.15, 1.20, 1.25, 1.30, 1.35)) != base
    assert _fp(min_books=4) != base
    assert _fp(min_train_bets=31) != base
    assert _fp(min_test_bets=6) != base
    assert _fp(bootstrap_resamples=201) != base
    assert _fp(bootstrap_seed=1) != base
    # determinístico
    assert _fp() == base


def test_fingerprint_changes_with_corpus_and_rule():
    base = oos_cache_fingerprint(config=_config(), corpus_signature="c1")
    assert oos_cache_fingerprint(config=_config(), corpus_signature="c2") != base
    assert oos_cache_fingerprint(
        config=_config(), corpus_signature="c1", max_odd=1.25) != base


def _patch_corpus_collection(monkeypatch, bets=None, corpus="corpus-abc"):
    """Isola o harness do corpus real: collect_bets sintético + corpus fixo."""
    from betgsn import value_walkforward as vwf
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: corpus)
    monkeypatch.setattr(vwf, "collect_bets",
                        lambda *a, **k: list(bets if bets is not None else _corpus()))


def test_cached_oos_evidence_roundtrip_and_stale(monkeypatch, tmp_path):
    """Cache salvo com fingerprint atual lê; qualquer mudança invalida."""
    from betgsn import value_walkforward as vwf
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    _patch_corpus_collection(monkeypatch)
    payload = compute_oos_validation(_config())
    save_oos_validation(payload)

    evidence = cached_oos_evidence(_config())
    assert evidence is not None
    assert evidence.n_bets_oos == payload["aggregate"]["n_bets_oos"]
    assert evidence.embargo_days == _config().gap_days
    assert evidence.oos_rule_bets

    # corpus mudou -> stale -> None
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "corpus-novo")
    assert cached_oos_evidence(_config()) is None

    # parâmetro de walk-forward mudou -> stale -> None
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "corpus-abc")
    assert cached_oos_evidence(_config(gap_days=7)) is None
    assert cached_oos_evidence(_config(train_days=800)) is None


def test_cached_oos_evidence_rejects_wrong_schema(monkeypatch, tmp_path):
    from betgsn import value_walkforward as vwf

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    _patch_corpus_collection(monkeypatch)
    payload = compute_oos_validation(_config())
    payload["schema"] = OOS_CACHE_SCHEMA_VERSION + 1
    save_oos_validation(payload)
    assert cached_oos_evidence(_config()) is None


def test_compute_oos_validation_payload_shape(monkeypatch, tmp_path):
    from betgsn import value_walkforward as vwf

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    _patch_corpus_collection(monkeypatch)
    payload = compute_oos_validation(_config())
    assert payload["schema"] == OOS_CACHE_SCHEMA_VERSION
    assert payload["cache_fingerprint"]
    for key in ("aggregate", "windows", "robustness", "ablation",
                "oos_rule_bets", "oos_market_bets"):
        assert key in payload
    agg = payload["aggregate"]
    for key in ("n_windows", "n_windows_valid", "n_bets_oos", "roi",
                "roi_se", "wilson_low", "bootstrap_low",
                "market_brier", "market_logloss", "market_ece",
                "calibrated_brier", "calibrated_logloss", "calibrated_ece",
                "max_drawdown", "embargo_days"):
        assert key in agg
    for key in ("calibration_windows", "calibration_insufficient",
                "oos_rule_bets", "oos_market_bets"):
        assert key in payload


# ==========================================================================
# 6. Segmentos do promotion gate: SOMENTE apostas OOS
# ==========================================================================


def test_promotion_segments_use_only_oos_bets():
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
    )
    segments = evidence.promotion_segments()
    assert segments
    # todo segmento tem métricas da regra E do mercado (mesmo teste OOS)
    for seg in segments:
        assert seg.n_matches > 0
        assert "roi" in seg.metrics and "roi" in seg.baseline_metrics
        assert "brier" in seg.metrics and "brier" in seg.baseline_metrics
    # apostas full-sample (fora dos testes) não viram segmento:
    # segmentos só podem ter temporadas cobertas pelos testes
    test_years = {
        str(b["d"])[:4] for w in result.windows if w.test_start
        for b in result.oos_rule_bets
    }
    seg_years = {s.season for s in segments}
    assert seg_years <= test_years


# ==========================================================================
# 7. CLV prospectivo: evidência do store
# ==========================================================================


def test_prospective_clv_evidence_empty_store(tmp_path):
    from betgsn.odds_snapshots import OddsSnapshotStore

    store = OddsSnapshotStore(tmp_path / "odds.db")
    ev = prospective_clv_evidence(store)
    assert ev == {"mean": 0.0, "n": 0, "prospective": True}


def test_prospective_clv_evidence_counts_only_ok_results(tmp_path):
    from betgsn.odds_normalize import event_key
    from betgsn.odds_snapshots import (
        OddsObservation, OddsSnapshotStore, CLV_ENTRY_SOURCE,
    )
    from betgsn.timeutil import utc_key

    store = OddsSnapshotStore(tmp_path / "odds.db")
    key = event_key("Alfa", "Bravo", utc_key("2030-06-01T18:00:00Z"))
    # fechamento real APÓS a entrada (CLV válido)
    store.add([
        OddsObservation(
            match_key=key, market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=1.90, timestamp="2030-06-01T10:00:00Z",
            kickoff="2030-06-01T18:00:00Z", provider="The Odds API",
        ),
        OddsObservation(
            match_key=key, market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=1.70, timestamp="2030-06-01T17:30:00Z",
            kickoff="2030-06-01T18:00:00Z", provider="The Odds API",
        ),
    ])
    assert store.register_entry(
        match_key=key, market="Resultado Final (1X2)", outcome="1",
        entry_odd=1.85, entry_timestamp="2030-06-01T11:00:00Z",
        entry_n_books=1, kickoff="2030-06-01T18:00:00Z",
        prediction_timestamp="2030-06-01T11:00:00Z",
        source=CLV_ENTRY_SOURCE,
    )
    ev = prospective_clv_evidence(store)
    assert ev["n"] == 1
    assert ev["prospective"] is True
    assert ev["mean"] == pytest.approx(1.85 / 1.70 - 1.0, abs=1e-6)


def test_prospective_clv_ignores_closing_before_entry(tmp_path):
    """Fechamento anterior à entrada: NÃO conta como CLV prospectivo."""
    from betgsn.odds_normalize import event_key
    from betgsn.odds_snapshots import (
        OddsObservation, OddsSnapshotStore, CLV_ENTRY_SOURCE,
    )
    from betgsn.timeutil import utc_key

    store = OddsSnapshotStore(tmp_path / "odds.db")
    key = event_key("Alfa", "Bravo", utc_key("2030-06-01T18:00:00Z"))
    store.add([
        OddsObservation(
            match_key=key, market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=1.70, timestamp="2030-06-01T09:00:00Z",
            kickoff="2030-06-01T18:00:00Z", provider="The Odds API",
        ),
    ])
    store.register_entry(
        match_key=key, market="Resultado Final (1X2)", outcome="1",
        entry_odd=1.85, entry_timestamp="2030-06-01T11:00:00Z",
        entry_n_books=1, kickoff="2030-06-01T18:00:00Z",
        prediction_timestamp="2030-06-01T11:00:00Z",
        source=CLV_ENTRY_SOURCE,
    )
    ev = prospective_clv_evidence(store)
    assert ev["n"] == 0  # CLOSING_BEFORE_ENTRY: não é CLV prospectivo


# ==========================================================================
# 8. Config inválida levanta (não mede com janela quebrada)
# ==========================================================================


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        WalkForwardConfig(train_days=0).validate()
    with pytest.raises(ValueError):
        WalkForwardConfig(gap_days=-1).validate()
    with pytest.raises(ValueError):
        WalkForwardConfig(candidate_max_odds=()).validate()
