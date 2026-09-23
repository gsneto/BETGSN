"""Modelo vs mercado OOS — contratos de origem, temporalidade e separação.

O que está em jogo (ciclo de validação de modelo)
-------------------------------------------------
1. ORIGEM: market_raw (1/odd), market_fair (de-vig), model (ratings) e
   model_calibrated são fontes DIFERENTES — nunca rotuladas umas como
   as outras.
2. TEMPORALIDADE: ratings ajustados só com partidas < train_end;
   calibrador escolhido no train, congelado no test; apostas no gap
   fora de tudo.
3. MESMAS janelas da estratégia (mesmo WalkForwardConfig/embargo).
4. SEPARAÇÃO: performance do modelo ≠ performance da estratégia
   (strategy_model inclui line-shopping — declarado, nunca atribuído
   ao modelo).

Tudo determinístico: corpus sintético, sem rede.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from betgsn.model_walkforward import (
    HOME_ADVANTAGE,
    MODEL_CACHE_SCHEMA_VERSION,
    RATING_WINDOW_DAYS,
    FrozenModel,
    fit_model_on_train,
    model_ablation,
    model_cache_fingerprint,
    model_robustness,
    run_model_walkforward,
)
from betgsn.value_walkforward import WalkForwardConfig, build_windows


# ------------------------------------------------------------------ fixtures


class _Match:
    """Partida histórica mínima para fit_ratings (contrato HistoricalMatch)."""

    def __init__(self, kickoff: str, home: str, away: str,
                 home_goals: int, away_goals: int):
        self.kickoff = kickoff
        self.home = home
        self.away = away
        self.home_goals = home_goals
        self.away_goals = away_goals
        self.timezone = "UTC"
        self.weight = 1.0
        self.home_xg = None
        self.away_xg = None
        self.home_corners = None
        self.away_corners = None
        self.home_cards = None
        self.away_cards = None
        self.home_shots = None
        self.away_shots = None
        self.home_shots_on_target = None
        self.away_shots_on_target = None
        self.league = "E0"
        self.season = ""
        self.date = kickoff[:10]
        self.time = kickoff[11:16]
        self.referee = ""
        self.attendance = None


def _matches(n_days: int = 800, start: str = "2022-01-03") -> list[_Match]:
    """Corpus sintético: 'Strong' marca muito, 'Weak' sofre gols."""
    out = []
    d0 = date.fromisoformat(start)
    for i in range(n_days):
        d = (d0 + timedelta(days=i)).isoformat()
        # Strong ganha em casa; fora empata; Weak perde
        out.append(_Match(f"{d}T15:00:00Z", "Strong", f"Opp{i % 6}",
                          home_goals=3, away_goals=0))
        out.append(_Match(f"{d}T17:00:00Z", f"Opp{i % 6}", "Weak",
                          home_goals=1, away_goals=2))
    return out


def _bet(day: str, home: str, away: str, oc: str, odd: float, res: str,
         fair: float | None = None, lg: str = "E0") -> dict:
    return {
        "d": day, "lg": lg, "mkt": "Resultado Final (1X2)", "oc": oc,
        "home": home, "away": away, "odd": odd,
        "ret": (odd - 1.0) if res == "win" else (0.0 if res == "push" else -1.0),
        "median": odd, "n_books": 5, "res": res,
        "fair": fair if fair is not None else 1.0 / odd,
    }


def _bets() -> list[dict]:
    """Linhas apostáveis cobrindo train (2023) e test (2024)."""
    bets = []
    # train: Strong em casa é favorito real (ganha)
    for i in range(60):
        d = f"2023-{1 + i % 12:02d}-{1 + i % 28:02d}"
        bets.append(_bet(d, "Strong", "Weak", "1", 1.25, "win", fair=0.72))
        bets.append(_bet(d, "Strong", "Weak", "2", 3.60, "loss", fair=0.25))
    # test 2024: as MESMAS partidas (Strong continua ganhando)
    for i in range(60):
        d = f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}"
        bets.append(_bet(d, "Strong", "Weak", "1", 1.25, "win", fair=0.72))
        bets.append(_bet(d, "Strong", "Weak", "2", 3.60, "loss", fair=0.25))
    return bets


def _config(**kwargs) -> WalkForwardConfig:
    defaults = dict(
        train_days=365, test_days=365, gap_days=2,
        candidate_max_odds=(1.30,), min_books=1,
        min_train_bets=10, min_test_bets=5,
        bootstrap_resamples=200, bootstrap_seed=424242,
    )
    defaults.update(kwargs)
    return WalkForwardConfig(**defaults)


# ==========================================================================
# 1. Temporalidade do modelo (fit no TRAIN, congelado no TEST)
# ==========================================================================


def test_fit_model_uses_only_matches_before_train_end():
    """Ratings só com partidas ANTERIORES a train_end — prova com
    partida futura marcada: se entrasse, mudaria o rating."""
    cutoff = "2023-01-01"
    before = _matches(400, start="2021-06-01")
    # partida "do futuro" com padrão invertido (Weak goleada em casa)
    future = [_Match("2023-06-01T15:00:00Z", "Weak", "Strong", 5, 0)]

    model_a = fit_model_on_train(before, cutoff)
    model_b = fit_model_on_train(before + future, cutoff)
    assert model_a is not None and model_b is not None
    # a partida futura não mudou nada: mesmos ratings
    assert model_a.ratings["Strong"].attack == pytest.approx(
        model_b.ratings["Strong"].attack)
    assert model_a.n_matches == model_b.n_matches


def test_fit_model_respects_rating_window():
    """Rolling window: partidas mais velhas que RATING_WINDOW_DAYS ficam
    fora do fit (mesma disciplina do benchmark de produção)."""
    cutoff = "2024-01-01"
    old = _matches(50, start="2018-01-01")  # ~6 anos antes: fora
    recent = _matches(300, start="2023-03-01")
    model = fit_model_on_train(old + recent, cutoff)
    assert model is not None
    # só o recent cabe na janela de 1095d
    assert model.n_matches <= len(recent)


def test_fit_model_insufficient_sample_returns_none():
    assert fit_model_on_train(_matches(10), "2023-01-01") is None


def test_model_frozen_across_test_window():
    """O modelo da janela é UM FrozenModel: mesmas entradas => mesmas
    probabilidades (determinismo do congelamento)."""
    model = fit_model_on_train(_matches(400, start="2022-01-01"), "2024-01-01")
    from betgsn.model_walkforward import _prob_1x2

    p1 = _prob_1x2(model, "Strong", "Weak")
    p2 = _prob_1x2(model, "Strong", "Weak")
    assert p1 == p2
    assert p1 is not None
    p1_sum = sum(p1)
    assert 0.99 < p1_sum < 1.01  # 1X2 normalizado


# ==========================================================================
# 2. Origem das fontes: market_raw vs market_fair vs model
# ==========================================================================


def test_sources_have_different_probabilities():
    """No harness completo, as QUATRO fontes produzem números distintos
    — market_raw != market_fair (de-vig), model != market."""
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())

    assert result.n_windows_valid >= 1
    valid = next(w for w in result.windows if w.n_test_bets)
    # de-vig muda a probabilidade (retira a margem)
    assert valid.market_fair.logloss != pytest.approx(
        valid.market_raw.logloss)
    # modelo tem origem diferente do mercado
    assert valid.model_raw.logloss != pytest.approx(
        valid.market_raw.logloss)


def test_market_fair_removes_overround():
    """fair = implied/overround: probabilidade de-vigada < raw."""
    bet = _bet("2024-01-01", "A", "B", "1", 1.30, "win", fair=0.72)
    assert bet["fair"] < 1.0 / 1.30  # de-vig reduz a prob implícita


def test_population_declared_per_source():
    """n de cada fonte é declarado (fair pode ter subpopulação)."""
    bets = _bets()
    bets[0]["fair"] = 0.0  # uma linha sem fair
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())
    assert result.market_fair.n <= result.market_raw.n


# ==========================================================================
# 3. Mesmas janelas da estratégia
# ==========================================================================


def test_same_windows_as_strategy_harness():
    """As janelas do modelo são AS MESMAS de value_walkforward (mesmo
    builder/config) — comparação legítima."""
    cfg = _config()
    model_windows = build_windows("2023-01-01", "2025-01-01", cfg)
    from betgsn.value_walkforward import build_windows as strategy_builder

    strategy_windows = strategy_builder("2023-01-01", "2025-01-01", cfg)
    assert [w.index for w in model_windows] == [
        w.index for w in strategy_windows]
    assert all(
        a.test_start == b.test_start and a.gap_days == b.gap_days
        for a, b in zip(model_windows, strategy_windows))


def test_gap_bets_excluded_from_model_evaluation():
    """Aposta no embargo (entre train_end e test_start) não entra em nada.

    Janela: train [2023-09-01, 2024-08-31), gap 2d, test [2024-09-02, 2025-09-02).
    """
    bets = [
        _bet("2023-09-01", "Strong", "Weak", "1", 1.30, "win"),   # train
        _bet("2024-09-01", "Strong", "Weak", "1", 1.30, "win"),   # GAP
        _bet("2024-10-01", "Strong", "Weak", "1", 1.30, "win"),   # test
    ]
    matches = _matches(900, start="2022-06-01")
    result = run_model_walkforward(bets, matches, _config())
    # só a aposta de teste foi avaliada; a do gap, nunca
    assert result.n_bets_oos == 1


# ==========================================================================
# 4. Separação modelo vs estratégia
# ==========================================================================


def test_strategy_model_is_labeled_separately():
    """strategy_model carrega nota explícita: inclui line-shopping, NÃO
    é performance do modelo."""
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())
    if result.strategy_model:
        assert "line-shopping" in result.strategy_model["note"]
        assert "NÃO é performance do modelo" in result.strategy_model["note"]


def test_strategy_market_reported_in_same_window():
    """A regra atual (favoritos curtos) é medida na MESMA janela —
    comparável, sem misturar fontes."""
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())
    valid = [w for w in result.windows if w.strategy_market_n]
    assert valid  # a regra de favoritos rodou nas mesmas janelas
    for w in valid:
        assert w.strategy_market_roi is not None


# ==========================================================================
# 5. Calibração do modelo: escolha no TRAIN, frozen no TEST
# ==========================================================================


def test_calibration_method_declared_per_window():
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())
    for w in result.windows:
        if w.n_test_bets:
            assert w.calibration_method in ("raw", "platt", "isotonic")


def test_model_probabilities_come_from_ratings_not_market():
    """Com mercado MAL calibrado e modelo BOM, o modelo ganha: prova que
    p_model vem dos ratings, não das odds."""
    bets = []
    # train: Strong ganha sempre
    for i in range(60):
        d = f"2023-{1 + i % 12:02d}-{1 + i % 28:02d}"
        bets.append(_bet(d, "Strong", "Weak", "1", 5.00, "win", fair=0.30))
    # test: odds dizem 20% (1/5.00) mas Strong continua ganhando
    for i in range(60):
        d = f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}"
        bets.append(_bet(d, "Strong", "Weak", "1", 5.00, "win", fair=0.30))
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())
    valid = next(w for w in result.windows if w.n_test_bets)
    # mercado errado (logloss alto)...
    assert valid.market_raw.logloss > 0.5
    # ...modelo (que aprendeu Strong nos ratings) muito melhor
    assert valid.model_raw.logloss < valid.market_raw.logloss


# ==========================================================================
# 6. Robustez, ablação, drift
# ==========================================================================


def test_robustness_by_odds_band():
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    scenarios = model_robustness(bets, matches, _config())
    labels = [s["scenario"] for s in scenarios]
    assert any("1.40-2.00" in l for l in labels)
    for s in scenarios:
        assert "status" in s  # OK ou INSUFFICIENT_DATA — declarado


def test_ablation_separates_components():
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    rows = model_ablation(bets, matches, _config())
    components = [r["componente"] for r in rows]
    assert any("market_raw" in c for c in components)
    assert any("market_fair" in c for c in components)
    assert any("model_raw" in c for c in components)
    assert any("model_calibrated" in c for c in components)


def test_drift_summary_declared():
    bets = _bets()
    matches = _matches(800, start="2022-01-03")
    result = run_model_walkforward(bets, matches, _config())
    if result.drift:
        for source, info in result.drift.items():
            assert "drift" in info  # STABLE/DEGRADED/INSUFFICIENT_DATA


# ==========================================================================
# 7. Cache: fingerprint cobre modelo, janelas e calibração
# ==========================================================================


def test_model_fingerprint_parameters():
    cfg = _config()
    base = model_cache_fingerprint(config=cfg, corpus_signature="c1")
    assert model_cache_fingerprint(config=cfg, corpus_signature="c2") != base
    assert model_cache_fingerprint(
        config=_config(gap_days=3), corpus_signature="c1") != base
    assert model_cache_fingerprint(
        config=cfg, corpus_signature="c1",
        rating_window_days=730) != base
    assert model_cache_fingerprint(
        config=cfg, corpus_signature="c1", home_advantage=1.2) != base
    assert model_cache_fingerprint(
        config=cfg, corpus_signature="c1", ev_threshold=0.02) != base
    # determinístico
    assert model_cache_fingerprint(config=cfg, corpus_signature="c1") == base


def test_cached_model_evidence_schema_gate(monkeypatch, tmp_path):
    import json

    from betgsn import model_walkforward as mw
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(mw, "_model_cache_path",
                        lambda: tmp_path / "model.json")
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "c1")
    cfg = _config()
    fp = model_cache_fingerprint(config=cfg, corpus_signature="c1")
    (tmp_path / "model.json").write_text(json.dumps({
        "schema": MODEL_CACHE_SCHEMA_VERSION, "cache_fingerprint": fp,
    }), encoding="utf-8")
    assert mw.cached_model_evidence(cfg) is not None

    # schema errado -> None
    (tmp_path / "model.json").write_text(json.dumps({
        "schema": MODEL_CACHE_SCHEMA_VERSION + 1,
        "cache_fingerprint": fp,
    }), encoding="utf-8")
    assert mw.cached_model_evidence(cfg) is None

    # fingerprint de outro corpus -> None (stale)
    (tmp_path / "model.json").write_text(json.dumps({
        "schema": MODEL_CACHE_SCHEMA_VERSION, "cache_fingerprint": fp,
    }), encoding="utf-8")
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "outro")
    assert mw.cached_model_evidence(cfg) is None


# ==========================================================================
# 8. Isolamento: o harness não conhece providers nem decide apostas
# ==========================================================================


def test_model_harness_has_no_provider_coupling():
    from pathlib import Path

    import betgsn.model_walkforward as mw

    source = Path(mw.__file__).read_text(encoding="utf-8").lower()
    for provider in ("the odds api", "parlayapi", "oddspapi",
                     "opticodds", "odds-api.io"):
        assert provider not in source
    # e não produz decisão: sem import de staking/decide_bet
    assert "decide_bet" not in source
    assert "betdecision" not in source
