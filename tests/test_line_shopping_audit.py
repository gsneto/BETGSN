"""Auditoria do line-shopping — decomposição do efeito +6,4pp.

Determinístico: nenhum corpus real, nenhuma rede. Cenários sintéticos
com valores exatos calculáveis à mão.

O que esta suíte prova:

  - efeito PREÇO isolado: mesma população, ROI(best) vs ROI(median)
    difere exatamente pelo uplift de preço (pergunta A);
  - a semântica da ablação `use_median` TROCA a população (n's
    diferentes) — o delta reportado mistura seleção e preço;
  - segunda melhor / pior / média / dispersão chegam ao audit
    (perguntas A/I);
  - janelas, odd bands, ligas e buckets de bookmakers segmentam o
    efeito (perguntas F/G/H/I);
  - as respostas A-I estão presentes, com limitações temporais
    declaradas (B/C/E: corpus sem timestamps);
  - collect_bets enriquece as linhas com second/worst/mean/disp.
"""

from __future__ import annotations

import types

import pytest

from betgsn.line_shopping_audit import (
    price_of,
    run_line_shopping_audit,
    strategy_model_decomposition,
)
from betgsn.value_walkforward import WalkForwardConfig

MARKET = "Resultado Final (1X2)"


def _bet(
    d: str,
    *,
    best: float,
    second: float | None,
    median: float,
    worst: float | None = None,
    mean: float | None = None,
    disp: float | None = None,
    n_books: int = 3,
    res: str = "win",
    lg: str = "E0",
    oc: str = "1",
) -> dict:
    return {
        "d": d, "lg": lg, "mkt": MARKET, "oc": oc,
        "home": "A", "away": "B",
        "odd": best, "second": second, "median": median,
        "worst": worst if worst is not None else (median - 0.05),
        "mean": mean if mean is not None else median,
        "disp": disp if disp is not None else 0.02,
        "n_books": n_books, "res": res, "fair": 1.0 / median,
    }


def _config() -> WalkForwardConfig:
    return WalkForwardConfig(
        train_days=30, test_days=10, gap_days=2,
        bootstrap_resamples=200, bootstrap_seed=42,
    )


# ==========================================================================
# price_of
# ==========================================================================


def test_price_of_all_keys():
    bet = _bet("2030-01-01", best=1.25, second=1.24, median=1.20)
    assert price_of(bet, "best") == 1.25
    assert price_of(bet, "second") == 1.24
    assert price_of(bet, "median") == 1.20
    assert price_of(bet, "mean") == 1.20
    assert price_of(bet, "worst") == 1.15


def test_price_of_second_none_with_single_book():
    bet = _bet("2030-01-01", best=1.25, second=None, median=1.20, n_books=1)
    assert price_of(bet, "second") is None
    assert price_of(bet, "best") == 1.25


def test_price_of_unknown_key_raises():
    with pytest.raises(ValueError):
        price_of(_bet("2030-01-01", best=1.25, second=1.2, median=1.2), "nope")


# ==========================================================================
# pergunta A: efeito PREÇO isolado (população constante)
# ==========================================================================


def test_price_effect_isolated_constant_population():
    """Todas as linhas elegíveis no best; todas ganham.

    ROI(best) = best - 1; ROI(median) = median - 1. O delta é EXATAMENTE
    o uplift de preço médio — nada de efeito de seleção."""
    bets = [
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20, res="win"),
        _bet("2030-01-01", best=1.28, second=1.26, median=1.22, res="win"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    by_price = audit.rule_population_by_price
    # MESMA população: n idêntico em todos os preços com dado
    assert by_price["best"]["n"] == 2
    assert by_price["median"]["n"] == 2
    assert by_price["second"]["n"] == 2
    # ROI exato: média dos retornos
    assert by_price["best"]["roi"] == round(((1.25 - 1) + (1.28 - 1)) / 2, 6)
    assert by_price["median"]["roi"] == round(((1.20 - 1) + (1.22 - 1)) / 2, 6)
    delta = by_price["best"]["roi"] - by_price["median"]["roi"]
    assert delta == pytest.approx(((1.25 - 1.20) + (1.28 - 1.22)) / 2, abs=1e-6)


def test_price_effect_with_losses_deterministic():
    """Mistura win/loss: liquidação correta por preço (stake 1)."""
    bets = [
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20, res="win"),
        _bet("2030-01-01", best=1.28, second=1.26, median=1.22, res="loss"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    by_price = audit.rule_population_by_price
    assert by_price["best"]["roi"] == round((0.25 - 1.0) / 2, 6)
    assert by_price["median"]["roi"] == round((0.20 - 1.0) / 2, 6)


def test_rule_population_respects_eligibility_on_best():
    """Linha elegível só na mediana NÃO entra na população da regra."""
    bets = [
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20, res="win"),
        # best 1.40: fora da regra (< 1.30), embora median 1.10 entre
        _bet("2030-01-01", best=1.40, second=1.35, median=1.10, res="win"),
        # 2 casas: sem consenso
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20,
             n_books=2, res="win"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    assert audit.n_lines_rule == 1
    assert audit.rule_population_by_price["best"]["n"] == 1


# ==========================================================================
# semântica da ablação: população muda
# ==========================================================================


def test_ablation_semantics_changes_population():
    bets = [
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20, res="win"),
        _bet("2030-01-01", best=1.40, second=1.35, median=1.10, res="loss"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    sem = audit.ablation_semantics
    # população best: só a primeira linha
    assert sem["population_best"]["n"] == 1
    # população median: as duas
    assert sem["population_median"]["n"] == 2
    # o delta da semântica reportada mistura seleção e preço
    assert sem["delta_reported_semantics"] is not None


def test_delta_reported_semantics_value():
    """pop_best: 1 linha win @1.25 -> roi 0.25.
    pop_median: bet1 (median 1.20, win -> +0.20) + bet2 (median 1.10,
    loss -> -1.0) -> roi (0.20 - 1.0)/2 = -0.40.
    delta = -0.40 - 0.25 = -0.65 (populações diferentes!)."""
    bets = [
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20, res="win"),
        _bet("2030-01-01", best=1.40, second=1.35, median=1.10, res="loss"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    sem = audit.ablation_semantics
    assert sem["population_best"]["roi_best"] == 0.25
    assert sem["population_median"]["roi_median"] == -0.40
    assert sem["delta_reported_semantics"] == pytest.approx(-0.65, abs=1e-6)


# ==========================================================================
# janelas (pergunta G) e embargo
# ==========================================================================


def _anchor() -> dict:
    """Linha fora da regra (best >= 1.30) que ancora o início do corpus.

    Sem ela, days[0] seria a primeira aposta elegível e as janelas de
    teste começariam depois dela — o teste das janelas precisaria de
    datas relativas ao primeiro dia ELEGÍVEL, não ao corpus."""
    return _bet("2030-01-01", best=2.00, second=1.90, median=1.95,
                res="loss")


def test_per_window_effect_counted():
    """Janelas de teste separadas carregam seus próprios deltas.

    Com days[0]=2030-01-01, train 30d, gap 2d: TEST w0 = [02-02, 02-12),
    TEST w1 = [02-12, 02-22)."""
    bets = [
        _anchor(),
        _bet("2030-02-05", best=1.25, second=1.24, median=1.20, res="win"),
        _bet("2030-02-15", best=1.28, second=1.27, median=1.22, res="loss"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    assert len(audit.by_window) == 2
    w0, w1 = audit.by_window
    assert w0["roi_best"] == 0.25
    assert w1["roi_best"] == -1.0
    answers_g = audit.answers["G_depende_de_poucas_janelas"]
    assert answers_g["n_janelas_com_efeito"] == 2


def test_bets_in_gap_are_excluded_from_windows():
    """Linha no gap (embargo) não entra em nenhum TEST de janela.

    days[0]=2030-01-01: train_end=2030-01-31, test_start=2030-02-02.
    A linha de 2030-01-31 está no embargo — fora de treino E teste."""
    bets = [
        _anchor(),
        _bet("2030-01-31", best=1.25, second=1.24, median=1.20, res="win"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    # nenhuma janela reporta a linha do embargo
    assert audit.by_window == []
    # mas a população agregada (sem recorte de janela) a inclui
    assert audit.n_lines_rule == 1


# ==========================================================================
# segmentações (F/H/I)
# ==========================================================================


def test_segmentation_by_n_books_bucket():
    bets = [
        _anchor(),
        _bet("2030-02-02", best=1.25, second=1.24, median=1.20,
             n_books=3, res="win"),
        _bet("2030-02-03", best=1.26, second=1.25, median=1.21,
             n_books=10, res="win"),
    ]
    audit = run_line_shopping_audit(bets, _config(), min_segment_lines=1)
    buckets = {row["segment"]: row for row in audit.by_n_books}
    assert "3" in buckets and "10+" in buckets
    assert buckets["3"]["n"] == 1
    assert buckets["10+"]["n"] == 1


def test_segmentation_by_band():
    bets = [
        _anchor(),
        _bet("2030-02-02", best=1.25, second=1.24, median=1.20, res="win"),
        _bet("2030-02-03", best=1.26, second=1.25, median=1.21, res="win"),
    ]
    audit = run_line_shopping_audit(bets, _config(), min_segment_lines=1)
    bands = {row["segment"]: row for row in audit.by_band}
    # 1.25 e 1.26 caem na banda [1.25, 1.30) — limite superior exclusivo
    assert "1.25-1.30" in bands
    assert bands["1.25-1.30"]["n"] == 2


def test_small_segments_below_minimum_are_skipped():
    """MIN_SEGMENT_LINES evita segmentos com amostra insustentável."""
    bets = [
        _bet("2030-02-02", best=1.25, second=1.24, median=1.20,
             lg="XX", res="win"),
    ]
    audit = run_line_shopping_audit(bets, _config())
    assert audit.by_league == []


# ==========================================================================
# uplift de preço (A/I)
# ==========================================================================


def test_price_uplift_stats():
    bets = [
        _bet("2030-01-01", best=1.25, second=1.24, median=1.20, disp=0.02),
        _bet("2030-01-01", best=1.29, second=1.28, median=1.25, disp=0.03),
    ]
    audit = run_line_shopping_audit(bets, _config())
    uplift = audit.price_uplift
    assert uplift["best_vs_median"]["mean"] == round(
        ((1.25 / 1.20 - 1) + (1.29 / 1.25 - 1)) / 2, 6)
    assert uplift["best_vs_second"]["mean"] == round(
        ((1.25 / 1.24 - 1) + (1.29 / 1.28 - 1)) / 2, 6)
    assert uplift["dispersion_over_median"]["mean"] == round(
        (0.02 / 1.20 + 0.03 / 1.25) / 2, 6)


# ==========================================================================
# respostas A-I e limitações (B/C/E)
# ==========================================================================


def test_answers_cover_A_to_I_with_limitations():
    bets = [_bet("2030-02-02", best=1.25, second=1.24, median=1.20)]
    audit = run_line_shopping_audit(bets, _config())
    expected = {
        "A_ganho_e_do_melhor_preco",
        "B_melhor_preco_disponivel_na_decisao",
        "C_existe_look_ahead",
        "D_cotacao_rastreavel_ate_snapshot",
        "E_restricao_a_quotes_ate_o_instante",
        "F_depende_de_poucos_bookmakers",
        "G_depende_de_poucas_janelas",
        "H_permece_nas_odd_bands",
        "I_depende_de_jogos_com_muitas_casas",
    }
    assert expected <= set(audit.answers)
    # B/C/E declaram a limitação temporal do corpus
    assert "INDEMONSTRÁVEL" in audit.answers["B_melhor_preco_disponivel_na_decisao"]
    assert "timestamp" in audit.limitations[0]
    # A reporta o ROI por preço
    assert "roi_por_preco" in audit.answers["A_ganho_e_do_melhor_preco"]


def test_to_dict_serializable():
    import json

    bets = [_bet("2030-02-02", best=1.25, second=1.24, median=1.20)]
    audit = run_line_shopping_audit(bets, _config())
    payload = json.loads(json.dumps(audit.to_dict()))
    assert payload["n_lines_rule"] == 1
    assert payload["config"]["gap_days"] == _config().gap_days


# ==========================================================================
# janelas inválidas levantam (não mede)
# ==========================================================================


def test_empty_bets_returns_empty_audit():
    audit = run_line_shopping_audit([], _config())
    assert audit.n_lines_total == 0
    assert audit.n_lines_rule == 0
    assert audit.rule_population_by_price["best"]["n"] == 0


# ==========================================================================
# strategy_model: decomposição preço/seleção do efeito reportado
# ==========================================================================


def _srow(window: int, best: float, median: float, res: str) -> dict:
    return {
        "window": window, "d": "2030-02-05", "lg": "E0",
        "home": "A", "away": "B", "oc": "1",
        "odd": best, "median": median, "n_books": 3, "res": res,
        "p_calibrated": 0.55, "ev_best": 0.1,
    }


def test_strategy_model_decomposition_isolates_price():
    """Mesmas linhas EV>0: delta = puro efeito preço, sem trocar população."""
    from betgsn.line_shopping_audit import strategy_model_decomposition

    rows = [
        _srow(0, best=1.25, median=1.20, res="win"),
        _srow(0, best=1.28, median=1.22, res="loss"),
        _srow(1, best=1.26, median=1.24, res="win"),
    ]
    out = strategy_model_decomposition(rows, _config())
    assert out["n_rows"] == 3
    # mesma população nos dois preços (3 linhas: win/loss/win)
    assert out["roi_best"] == round((0.25 - 1.0 + 0.26) / 3, 6)
    assert out["roi_median"] == round((0.20 - 1.0 + 0.24) / 3, 6)
    assert out["delta_price_effect"] == pytest.approx(0.07 / 3, abs=1e-6)
    # por janela: w0 tem 2 linhas, w1 tem 1
    assert {w["window"]: w["n"] for w in out["by_window"]} == {0: 2, 1: 1}


def test_strategy_model_decomposition_empty():
    from betgsn.line_shopping_audit import strategy_model_decomposition

    out = strategy_model_decomposition([], _config())
    assert out["n_rows"] == 0
    assert out["roi_best"] is None
    assert out["delta_price_effect"] is None


# ==========================================================================
# collect_bets enriquecido: second/worst/mean/disp/n_books
# ==========================================================================


class _FakeStore:
    def __init__(self, odds: dict) -> None:
        self._odds = odds

    def odds_for(self, m) -> dict:
        return self._odds


def _fake_match(kickoff: str, home: str, away: str, hg: int, ag: int):
    return types.SimpleNamespace(
        kickoff=kickoff, league="Premier League (England)",
        home=home, away=away, home_goals=hg, away_goals=ag,
        home_corners=None, away_corners=None,
        home_cards=None, away_cards=None,
    )


def test_collect_bets_enriches_second_worst_mean_disp(monkeypatch):
    from betgsn import backtest_engine, value_strategy
    from betgsn import football_data_uk as fduk

    odds = {
        MARKET: {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
            "William Hill": {"1": 2.05, "X": 3.30, "2": 4.00},
        },
    }
    matches = [_fake_match("2030-01-01T15:00:00Z", "Arsenal", "Chelsea", 2, 0)]

    # collect_bets importa csv_odds_store DENTRO da funcao: o alvo do
    # patch e o modulo backtest_engine, nao value_strategy.
    monkeypatch.setattr(
        backtest_engine, "csv_odds_store",
        lambda closing=False: _FakeStore(odds))
    monkeypatch.setattr(
        fduk.FootballDataClient, "load_matches", lambda self: matches)

    bets = value_strategy.collect_bets(99.0, 1)
    line_1 = next(b for b in bets if b["oc"] == "1")
    # best / second / worst / mean / disp a partir das 3 casas
    assert line_1["odd"] == 2.05
    assert line_1["second"] == 1.95
    assert line_1["worst"] == 1.90
    assert line_1["mean"] == pytest.approx((1.90 + 1.95 + 2.05) / 3, abs=1e-6)
    assert line_1["median"] == 1.95
    assert line_1["n_books"] == 3
    assert line_1["disp"] > 0
    # resultado liquidade certo (2x0 -> "1" ganha)
    assert line_1["res"] == "win"


def test_collect_bets_single_book_no_second(monkeypatch):
    from betgsn import backtest_engine, value_strategy
    from betgsn import football_data_uk as fduk

    odds = {
        MARKET: {"Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20}},
    }
    matches = [_fake_match("2030-01-01T15:00:00Z", "Arsenal", "Chelsea", 0, 2)]

    monkeypatch.setattr(
        backtest_engine, "csv_odds_store",
        lambda closing=False: _FakeStore(odds))
    monkeypatch.setattr(
        fduk.FootballDataClient, "load_matches", lambda self: matches)

    bets = value_strategy.collect_bets(99.0, 1)
    line_2 = next(b for b in bets if b["oc"] == "2")
    assert line_2["second"] is None
    assert line_2["n_books"] == 1
    assert line_2["disp"] == 0.0
    assert line_2["res"] == "win"
