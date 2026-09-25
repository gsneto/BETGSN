"""Testes do Alpha Lab: PIT, avaliação honesta, market audit, registry.

Regra: nenhum teste pode afirmar edge/ROI. O que se verifica é a
metodologia — PIT estrito, status por evidência, ausência de promoção sem
gate.
"""
from __future__ import annotations

import pytest

from betgsn.alpha_lab import (
    STATUS_INSUFFICIENT,
    STATUS_NO_EVIDENCE,
    ForwardObservation,
    default_alpha_registry,
    evaluate_alpha,
    observe_signal,
)
from betgsn.market_dataset import (
    dataset_fingerprint,
    latest_prices_at,
    state_from_prices,
)


# --------------------------------------------------------------------------
# market_dataset
# --------------------------------------------------------------------------


def test_state_from_prices_agrega_corretamente():
    state = state_from_prices(
        event_key="e", market="m", selection="s", timestamp="T",
        prices_by_book={"A": 2.00, "B": 2.10, "C": 2.20},
    )
    assert state.best_price == 2.20
    assert state.best_book == "C"
    assert state.second_best == 2.10
    assert state.worst_price == 2.00
    assert state.median_price == pytest.approx(2.10)
    assert state.book_count == 3
    assert state.dispersion > 0


def test_state_from_prices_descarta_preco_invalido():
    state = state_from_prices(
        event_key="e", market="m", selection="s", timestamp="T",
        prices_by_book={"A": 2.00, "B": 1.0, "C": None},
    )
    assert state is not None and state.book_count == 1


def test_latest_prices_at_respeita_pit():
    series = [
        ("2026-01-01T10:00:00Z", "A", 2.00),
        ("2026-01-01T11:00:00Z", "A", 2.10),
        ("2026-01-01T12:00:00Z", "B", 2.20),  # FUTURO relativo ao cutoff
    ]
    prices = latest_prices_at(series, "2026-01-01T11:00:00Z")
    assert prices == {"A": 2.10}
    assert "B" not in prices


def test_dataset_fingerprint_estavel_e_sensivel():
    a = dataset_fingerprint(event_keys=["e1", "e2"], markets=["m"],
                            bookmakers=["A"], n_observations=10,
                            period=("t0", "t1"))
    b = dataset_fingerprint(event_keys=["e2", "e1"], markets=["m"],
                            bookmakers=["A"], n_observations=10,
                            period=("t0", "t1"))
    c = dataset_fingerprint(event_keys=["e1", "e2"], markets=["m"],
                            bookmakers=["A"], n_observations=11,
                            period=("t0", "t1"))
    assert a == b, "ordem não pode mudar o fingerprint"
    assert a != c, "amostra diferente muda o fingerprint"


# --------------------------------------------------------------------------
# observe_signal — forward só com quotes posteriores
# --------------------------------------------------------------------------


def test_observe_signal_forward_usa_somente_quotes_posteriores():
    series = [
        ("2026-01-01T10:00:00Z", "A", 2.00),
        ("2026-01-01T10:00:00Z", "B", 2.05),
        ("2026-01-01T10:00:00Z", "C", 2.10),
        # depois do sinal (+10min): B sobe
        ("2026-01-01T10:10:00Z", "B", 2.30),
        ("2026-01-01T10:10:00Z", "A", 2.00),
        ("2026-01-01T10:10:00Z", "C", 2.10),
    ]
    obs = observe_signal(
        signal_type="BOOKMAKER_OUTLIER", alpha_id="bookmaker_outlier",
        event_key="e", market="m", selection="s", bookmaker="B",
        signal_timestamp="2026-01-01T10:00:00Z",
        kickoff="2026-01-02T15:00:00Z", series=series,
        decision_price=2.05, median_price=2.05, best_price=2.10,
        book_count=3, dispersion_ratio=0.05, deviation=0.0, direction="UP",
        closing_price=None,
    )
    # +15min cai dentro da janela (quote em +10min) -> pega o preço novo;
    # +5min não tem quote posterior, então permanece None (não inventa).
    assert obs.market_move[300] is None
    assert obs.market_move[900] is not None
    assert obs.market_move[900] > 0, "mediana subiu"
    assert obs.market_followed is True


def test_observe_signal_sem_futuro_nao_inventa():
    series = [
        ("2026-01-01T10:00:00Z", "A", 2.00),
        ("2026-01-01T10:00:00Z", "B", 2.10),
        ("2026-01-01T10:00:00Z", "C", 2.20),
    ]
    obs = observe_signal(
        signal_type="BOOKMAKER_OUTLIER", alpha_id="bookmaker_outlier",
        event_key="e", market="m", selection="s", bookmaker="B",
        signal_timestamp="2026-01-01T10:00:00Z",
        kickoff="2026-01-02T15:00:00Z", series=series,
        decision_price=2.10, median_price=2.10, best_price=2.20,
        book_count=3, dispersion_ratio=0.05, deviation=0.0, direction="UP",
        closing_price=None,
    )
    assert all(v is None for v in obs.market_move.values())
    assert obs.market_followed is None
    assert obs.closing_price is None


# --------------------------------------------------------------------------
# evaluate_alpha — veredito por evidência
# --------------------------------------------------------------------------


def _obs(**kw) -> ForwardObservation:
    base = dict(
        signal_type="BOOKMAKER_OUTLIER", alpha_id="bookmaker_outlier",
        event_key="e", market="m", selection="s", bookmaker="A",
        signal_timestamp="2026-01-01T10:00:00Z", observed_at="2026-01-01T10:00:00Z",
        decision_price=2.0, median_price=2.0, best_price=2.0, book_count=3,
        dispersion_ratio=0.05, deviation=0.0, direction="UP",
        seconds_to_kickoff=3600.0, market_move={300: 0.0}, reference_move={300: 0.0},
        closing_price=None, closing_move=None, market_followed=None,
        converged=None,
    )
    base.update(kw)
    return ForwardObservation(**base)


def test_evaluate_alpha_insufficient_data():
    rows = [_obs() for _ in range(10)]
    ev = evaluate_alpha(rows, min_sample=100)
    assert ev.status == STATUS_INSUFFICIENT
    assert ev.primary_metric is None


def test_evaluate_alpha_no_evidence_quando_ic_cruza_zero():
    # movimentos simétricos: média ~0, IC cruza 0
    rows = []
    for i in range(200):
        move = 0.01 if i % 2 == 0 else -0.01
        rows.append(_obs(market_move={300: move},
                         market_followed=move > 0))
    ev = evaluate_alpha(rows, min_sample=100)
    assert ev.status == STATUS_NO_EVIDENCE
    assert ev.ci_low <= 0 <= ev.ci_high


def test_evaluate_alpha_convergencia_nao_usa_follow():
    rows = [_obs(direction="FLAT", converged=(i % 10 != 0),
                 market_followed=None) for i in range(200)]
    ev = evaluate_alpha(rows, min_sample=100)
    assert ev.metric_kind == "convergence"
    assert ev.primary_label == "converged_rate"
    assert ev.primary_metric is not None and ev.primary_metric > 0.5


def test_default_registry_tem_alpha_blocked_sem_dado():
    reg = default_alpha_registry()
    assert reg["market_residual"].status == "BLOCKED"
    assert reg["favorite_longshot"].status == "BLOCKED"
    assert "bookmaker_outlier" in reg


# --------------------------------------------------------------------------
# market_audit
# --------------------------------------------------------------------------


def _quote(book, sel, price, market="Resultado Final (1X2)",
           event="e1", ts="2026-01-01T10:00:00Z"):
    from betgsn.odds_normalize import NormalizedQuote

    return NormalizedQuote(
        event_id=event, provider="P", sport_key="s", league="L",
        home_team="H", away_team="A", kickoff="2026-01-02T15:00:00Z",
        bookmaker=book, market=market, selection=sel, price=price,
        timestamp=ts, line=None,
    )


def test_market_audit_mercado_completo_tem_fair():
    from betgsn.market_audit import audit_market

    quotes = [
        _quote("A", "1", 2.0), _quote("A", "X", 3.5), _quote("A", "2", 4.0),
        _quote("B", "1", 2.05), _quote("B", "X", 3.4), _quote("B", "2", 3.9),
    ]
    audit = audit_market(quotes, "Resultado Final (1X2)")
    assert audit.n_complete == 1
    assert audit.mean_overround is not None and audit.mean_overround > 0
    # fair soma ~1 (1X2 completo)
    assert abs(sum(audit.examples_fair.values()) - 1.0) < 0.05


def test_market_audit_mercado_incompleto_nao_gera_fair():
    from betgsn.market_audit import audit_market

    quotes = [_quote("A", "1", 2.0)]  # falta X e 2
    audit = audit_market(quotes, "Resultado Final (1X2)")
    assert audit.n_complete == 0
    assert audit.examples_fair == {}


# --------------------------------------------------------------------------
# signal_registry — nunca promove sem gate
# --------------------------------------------------------------------------


def test_registry_nao_promove_sem_clv():
    from betgsn.signal_registry import build_registry

    registry = build_registry(
        alpha_evaluations={
            "BOOKMAKER_OUTLIER": {"n": 5000, "status": "VALIDATED",
                                  "limitations": []},
        },
        clv_evidence={"n": 0, "mean": None, "median": None,
                      "positive_rate": None},
        execution_gap={"status": "UNKNOWN"},
    )
    entry = registry["BOOKMAKER_OUTLIER"]
    assert entry.status == "VALIDATED", "sem CLV não vira candidate"
    assert entry.clv_status == "RED"


def test_registry_promove_a_candidate_so_com_evidencia_completa():
    from betgsn.signal_registry import build_registry

    registry = build_registry(
        alpha_evaluations={
            "BOOKMAKER_OUTLIER": {"n": 5000, "status": "VALIDATED",
                                  "limitations": []},
        },
        clv_evidence={"n": 250, "mean": 0.01, "median": 0.008,
                      "positive_rate": 0.6},
        execution_gap={"status": "MEASURED"},
    )
    assert registry["BOOKMAKER_OUTLIER"].status == "PRODUCTION_CANDIDATE"


def test_registry_bloqueado_quando_dado_ausente():
    from betgsn.signal_registry import build_registry

    registry = build_registry(clv_evidence={"n": 0})
    # market_residual é BLOCKED por falta de modelo/resultado
    assert registry["market_residual"].status == "BLOCKED"


# --------------------------------------------------------------------------
# provider_diagnostics — classificação honesta (não confundir quota com chave)
# --------------------------------------------------------------------------


def _load_diag():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "provider_diagnostics.py"
    spec = importlib.util.spec_from_file_location("provider_diagnostics", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["provider_diagnostics"] = module
    spec.loader.exec_module(module)
    return module


def test_provider_classify_distingue_quota_de_chave_invalida():
    diag = _load_diag()
    quota = diag._classify({
        "state": "UNAVAILABLE", "last_kind": "AUTH", "last_status": 401,
        "last_error": "HTTP 401 ... OUT_OF_USAGE_CREDITS ... quota has been reached",
    })
    assert quota == ("yes", "yes", "exhausted"), quota
    invalid = diag._classify({
        "state": "UNAVAILABLE", "last_kind": "AUTH", "last_status": 401,
        "last_error": "Invalid or inactive API key",
    })
    assert invalid == ("no", "no", "unknown"), invalid


def test_provider_classify_healthy_e_rate_limit():
    diag = _load_diag()
    assert diag._classify({"state": "HEALTHY"}) == ("yes", "yes", "ok")
    rl = diag._classify({"state": "UNAVAILABLE", "last_status": 429,
                         "last_error": "too many"})
    assert rl == ("yes", "yes", "rate_limited")
