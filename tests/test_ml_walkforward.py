"""Modelos experimentais (Elo/XGBoost/LightGBM) nas MESMAS 24 janelas.

O que esta suíte prova:

  - o corpus de features é point-in-time (FeatureBuilder) e indexado
    pela MESMA chave (home, away, dia) das linhas canônicas;
  - o adapter ajusta apenas com partidas ANTERIORES a train_end (a
    mesma regra do BASELINE_V1) e devolve um modelo CONGELADO com o
    contrato `prob_1x2`/`n_matches`;
  - o harness `run_model_walkforward(model_fn=...)` consome o provider
    no MESMO caminho — sem avaliação paralela;
  - confrontos de temporadas diferentes nunca compartilham previsão;
  - kind desconhecido é rejeitado na construção.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from betgsn.ml_walkforward import (
    ELO_COLUMNS,
    ML_MODEL_KINDS,
    MLWindowAdapter,
    build_feature_corpus,
)
from betgsn.model_walkforward import run_model_walkforward
from betgsn.value_walkforward import WalkForwardConfig


@dataclasses.dataclass
class _Match:
    """Partida histórica mínima aceita pelo FeatureBuilder.

    Dataclass de propósito: `visible_xg` usa `dataclasses.replace` — o
    contrato real de HistoricalMatch.
    """

    kickoff: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    timezone: str = "UTC"
    result_available_at: str | None = None
    xg_status: str = "UNAVAILABLE"
    xg_source: str | None = None
    xg_available_at: str | None = None
    home_shots: int = 10
    away_shots: int = 8
    home_shots_on_target: int = 4
    away_shots_on_target: int = 3
    home_corners: int = 5
    away_corners: int = 4
    home_cards: int = 2
    away_cards: int = 2
    home_xg: float | None = None
    away_xg: float | None = None
    home_xg_against: float | None = None
    away_xg_against: float | None = None
    league: str = "E0"
    season: str = ""


def _matches(n_days: int = 500, start: str = "2022-01-03") -> list[_Match]:
    """'Strong' ganha em casa; 'Weak' perde; 1 em cada 3 dias há empate.

    As TRÊS classes de 1X2 precisam existir no corpus: sklearn devolve
    uma coluna por classe presente, e o XGBClassifier exige o mesmo
    conjunto de classes em fit e eval_set.
    """
    out = []
    d0 = date.fromisoformat(start)
    for i in range(n_days):
        d = (d0 + timedelta(days=i)).isoformat()
        out.append(_Match(f"{d}T15:00:00Z", "Strong", f"Opp{i % 6}",
                          home_goals=3, away_goals=0))
        if i % 3 == 0:
            out.append(_Match(f"{d}T17:00:00Z", f"Opp{i % 6}", "Weak",
                              home_goals=1, away_goals=1))
        else:
            out.append(_Match(f"{d}T17:00:00Z", f"Opp{i % 6}", "Weak",
                              home_goals=1, away_goals=2))
    return out


def _bets(matches: list[_Match]) -> list[dict]:
    """Linhas apostáveis cobrindo train e test, mesmas chaves do corpus."""
    bets = []
    for m in matches:
        day = m.kickoff[:10]
        if m.home == "Strong":
            bets.append({
                "d": day, "lg": "E0", "mkt": "Resultado Final (1X2)",
                "oc": "1", "home": m.home, "away": m.away,
                "odd": 1.25, "median": 1.20, "n_books": 4, "res": "win",
                "fair": 0.72, "ret": 0.25,
            })
    return bets


def _config(**kwargs) -> WalkForwardConfig:
    defaults = dict(
        train_days=365, test_days=120, gap_days=2,
        candidate_max_odds=(1.30,), min_books=1,
        min_train_bets=10, min_test_bets=5,
        bootstrap_resamples=100, bootstrap_seed=424242,
    )
    defaults.update(kwargs)
    return WalkForwardConfig(**defaults)


# ==========================================================================
# corpus de features
# ==========================================================================


def test_feature_corpus_indexed_by_home_away_day():
    matches = _matches(30)
    corpus = build_feature_corpus(matches)
    assert corpus.matrix.shape[0] == len(matches)
    assert corpus.labels.shape[0] == len(matches)
    # Strong 3x0 -> label 0 (vitória casa)
    strong_key = ("Strong", "Opp0", matches[0].kickoff[:10])
    assert corpus.keys[strong_key] == 0
    assert corpus.labels[0] == 0
    # linha reversa do índice
    assert corpus.rows[0] == strong_key
    # colunas Elo sempre presentes e preenchidas (rating default existe)
    for col in ELO_COLUMNS:
        assert col in corpus.columns
    col_idx = [corpus.columns.index(c) for c in ELO_COLUMNS]
    import numpy as np

    assert not np.isnan(corpus.matrix[:, col_idx]).any()


def test_feature_corpus_labels_three_classes():
    matches = _matches(30)
    corpus = build_feature_corpus(matches)
    # Strong ganha (0), Opp em casa vs Weak perde (2)
    assert set(corpus.labels.tolist()) <= {0, 1, 2}
    assert 0 in corpus.labels.tolist()
    assert 2 in corpus.labels.tolist()


# ==========================================================================
# adapter: contrato, congelamento, temporalidade
# ==========================================================================


def test_unknown_kind_rejected():
    with pytest.raises(ValueError, match="kind"):
        MLWindowAdapter("nope", build_feature_corpus(_matches(10)))


def test_adapter_fit_returns_frozen_view_with_contract():
    matches = _matches(200)
    corpus = build_feature_corpus(matches)
    adapter = MLWindowAdapter("elo", corpus)
    frozen = adapter.fit(matches, "2022-09-01")
    assert frozen is not None
    assert frozen.n_matches > 0
    # contrato do harness (com o dia da partida — desambigua
    # confrontos repetidos)
    day = corpus.keys[("Strong", "Opp0", matches[0].kickoff[:10])]
    assert day == 0
    probs = frozen.prob_1x2("Strong", "Opp0", day=matches[0].kickoff[:10])
    assert probs is not None
    p1, px, p2 = probs
    assert 0.0 < p1 < 1.0 and 0.0 < px < 1.0 and 0.0 < p2 < 1.0
    assert p1 + px + p2 == pytest.approx(1.0, abs=1e-6)
    # par desconhecido: None — nunca probabilidade inventada
    assert frozen.prob_1x2("Ninguem", "Nada", day="2022-01-01") is None


def test_adapter_fit_only_uses_matches_before_train_end():
    matches = _matches(400)
    corpus = build_feature_corpus(matches)
    adapter = MLWindowAdapter("elo", corpus)
    early = adapter.fit(matches, "2022-10-01")
    late = adapter.fit(matches, "2023-06-01")
    assert early is not None and late is not None
    assert late.n_matches > early.n_matches
    # o corte realmente limita o treino: o modelo tardio viu mais
    # partidas do que o modelo antigo
    assert late.n_matches > early.n_matches > 300


def test_adapter_insufficient_train_returns_none():
    matches = _matches(10)
    corpus = build_feature_corpus(matches)
    adapter = MLWindowAdapter("elo", corpus)
    assert adapter.fit(matches, "2022-02-01") is None


def test_frozen_view_resolves_by_day_multi_season_pair():
    """Confronto do mesmo par em dois dias DEPOIS do corte: cada partida
    recebe a previsão do SEU dia — o lookup sem dia é ambíguo e fica
    SEM resposta (None), nunca resolvido com o dia errado."""
    matches = _matches(200)
    extra = [
        _Match("2023-01-01T15:00:00Z", "Strong", "Opp0", 2, 1),
        _Match("2023-02-01T15:00:00Z", "Strong", "Opp0", 3, 0),
    ]
    corpus = build_feature_corpus(matches + extra)
    adapter = MLWindowAdapter("elo", corpus)
    frozen = adapter.fit(matches + extra, "2022-09-01")
    days = sorted(
        d for (h, a, d) in corpus.keys if {h, a} == {"Strong", "Opp0"}
    )
    assert len(days) >= 2
    after = [d for d in days if d >= "2022-09-01"]
    assert len(after) >= 2
    p_first = frozen.prob_1x2("Strong", "Opp0", day=after[0])
    p_second = frozen.prob_1x2("Strong", "Opp0", day=after[1])
    # as duas partidas existem e têm previsões DIFERENTES (dias
    # diferentes => features diferentes) — nunca a mesma previsão
    assert p_first is not None and p_second is not None
    assert p_first != p_second
    # consulta ambígua (sem o dia): None, nunca "aproximadamente"
    assert frozen.prob_1x2("Strong", "Opp0") is None


def test_frozen_view_single_meeting_resolves_without_day():
    """Par com UM único dia previsto: a consulta sem dia é inequívoca."""
    matches = _matches(200)
    extra = [_Match("2023-01-01T15:00:00Z", "OneHome", "OneAway", 1, 1)]
    corpus = build_feature_corpus(matches + extra)
    adapter = MLWindowAdapter("elo", corpus)
    frozen = adapter.fit(matches + extra, "2022-09-01")
    probs = frozen.prob_1x2("OneHome", "OneAway")
    assert probs is not None
    assert sum(probs) == pytest.approx(1.0, abs=1e-6)


def test_duplicate_corpus_key_raises():
    """Partida duplicada no corpus (mesmo confronto, mesmo dia) falha
    alto — a segunda nunca sobrescreve a primeira em silêncio."""
    matches = _matches(30)
    dup = _Match(matches[0].kickoff, matches[0].home, matches[0].away,
                 1, 1)
    with pytest.raises(ValueError, match="duplicada"):
        build_feature_corpus(matches + [dup])


# ==========================================================================
# harness: o provider entra no MESMO caminho das 24 janelas
# ==========================================================================


def test_run_model_walkforward_with_elo_provider():
    matches = _matches(500)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)
    adapter = MLWindowAdapter("elo", corpus)

    result = run_model_walkforward(
        bets, matches, _config(), model_fn=adapter.fit)

    # mesmas fontes separadas, agora com o provider no papel de model
    assert result.n_bets_oos > 0
    assert result.model_raw.brier is not None
    assert result.market_raw.brier is not None
    assert result.market_fair.brier is not None
    # separação: performance do modelo é medida, e a strategy_model é
    # reportada separada (com line-shopping declarado)
    assert "strategy_model" in result.to_dict()


def test_run_model_walkforward_xgboost_provider_small():
    """XGBoost (early stopping interno ao TRAIN) no mesmo harness."""
    matches = _matches(400)
    bets = _bets(matches)
    corpus = build_feature_corpus(matches)
    adapter = MLWindowAdapter("xgboost", corpus, min_early_stop_rows=20)

    result = run_model_walkforward(
        bets, matches, _config(test_days=90), model_fn=adapter.fit)
    assert result.n_bets_oos > 0
    assert result.model_raw.brier is not None


def test_temporal_split_respects_simultaneous_matches():
    """Partidas simultâneas: o corte anda até a fronteira de timestamp —
    `separated` nunca recebe blocos sobrepostos."""
    from betgsn.ml_walkforward import _temporal_split_index

    # 10 partidas no mesmo instante no meio do corte
    times = ["2022-01-01"] * 10 + ["2022-06-01"] * 10
    split = _temporal_split_index(times, 0.2, min_rows=2)
    assert split is not None
    assert times[split - 1] != times[split]
    # sem fronteira utilizável (tudo no mesmo instante): None
    assert _temporal_split_index(["2022-01-01"] * 20, 0.2, min_rows=2) is None
    # bloco de validação menor que o mínimo: None
    assert _temporal_split_index(
        ["2022-01-01"] + [f"2022-06-{i:02d}" for i in range(1, 11)],
        0.95, min_rows=5) is None


def test_ml_model_kinds_declared():
    assert ML_MODEL_KINDS == ("elo", "xgboost", "lightgbm")
