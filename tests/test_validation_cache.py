"""I-14 — cache de validacao nao pode misturar corpora/configuracoes.

O cache de `value_strategy` (output/value_validation.json) alimenta a
DECISAO de apostar (`_quant_decision` -> `decide_bet`). Reaproveitar
uma validacao feita sobre outro corpus, outros mercados, outro tipo de
odd ou outros parametros seria servir evidencia de outra medicao como
se fosse atual.

Aqui se prova que o fingerprint:

- e deterministico (mesma entrada -> mesma chave);
- muda com CADA elemento relevante (regra, mercados, fechamento,
  corpus);
- e o gate REAL do cache (hit so com fingerprint identico; miss
  recalcula);
- protege a decisao: cache de outro corpus nao entra em decide_bet.
"""
from __future__ import annotations

import json

import pytest

from betgsn.value_strategy import (
    MARKETS,
    MAX_ODD,
    MIN_BOOKS,
    StrategyValidation,
    _validation_cache_path,
    cached_validation,
    validate,
    validation_cache_fingerprint,
)


def _fp(**overrides):
    params = dict(
        max_odd=MAX_ODD, min_books=MIN_BOOKS, markets=MARKETS,
        closing=False, corpus_signature="corpus-1",
    )
    params.update(overrides)
    return validation_cache_fingerprint(**params)


def _write_cache(payload: dict):
    path = _validation_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _validation_payload(fingerprint: str) -> dict:
    return StrategyValidation(
        max_odd=MAX_ODD, min_books=MIN_BOOKS, n_bets=1000,
        roi=0.02, se=0.005, t=4.0, ci_low=0.01, ci_high=0.03,
        avg_odd=1.2, positive_years=10, total_years=10,
        positive_leagues=20, total_leagues=20,
        cache_fingerprint=fingerprint,
    ).to_json()


def _patch_corpus(monkeypatch, signature: str):
    from betgsn.football_data_uk import FootballDataClient
    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: signature)


# --------------------------------------------------------- fingerprint


def test_fingerprint_is_deterministic():
    assert _fp() == _fp()
    assert _fp(corpus_signature="corpus-2") == _fp(corpus_signature="corpus-2")


def test_fingerprint_changes_with_rule_parameters():
    base = _fp()
    assert _fp(max_odd=1.25) != base
    assert _fp(min_books=2) != base


def test_fingerprint_changes_with_markets():
    assert _fp(markets=("Total de Gols",)) != _fp()


def test_fingerprint_changes_with_closing_flag():
    """Odds de abertura e fechamento sao medicoes DIFERENTES."""
    assert _fp(closing=True) != _fp(closing=False)


def test_fingerprint_changes_with_corpus():
    """Um CSV novo (assinatura nova) invalida o cache antigo."""
    assert _fp(corpus_signature="corpus-2") != _fp(corpus_signature="corpus-1")


# --------------------------------------------------------- cache hit/miss


def test_cache_hit_with_identical_fingerprint(monkeypatch):
    _patch_corpus(monkeypatch, "corpus-1")
    fp = _fp()
    _write_cache(_validation_payload(fp))

    def _fail(*a, **k):  # collect_bets NUNCA pode rodar num hit
        raise AssertionError("cache hit nao deve recalcular")

    monkeypatch.setattr("betgsn.value_strategy.collect_bets", _fail)
    result = validate()
    assert result.roi == 0.02
    assert result.n_bets == 1000


def test_cache_miss_when_parameters_change(monkeypatch):
    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp()))

    bets = [
        {"d": "2024-01-01", "lg": "E0", "mkt": MARKETS[0],
         "odd": 1.20, "ret": 0.20},
        {"d": "2024-02-01", "lg": "E0", "mkt": MARKETS[0],
         "odd": 1.20, "ret": -1.0},
    ]
    monkeypatch.setattr("betgsn.value_strategy.collect_bets",
                        lambda *a, **k: list(bets))
    # outra regra: fingerprint nao bate, validacao roda de novo
    result = validate(max_odd=1.25)
    assert result.max_odd == 1.25
    assert result.n_bets == 2


def test_cache_miss_when_corpus_changes(monkeypatch):
    """Corpo novo: o cache da medicao antiga nao vale."""
    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp()))

    _patch_corpus(monkeypatch, "corpus-2")  # CSV novo chegou
    bets = [{"d": "2025-01-01", "lg": "E0", "mkt": MARKETS[0],
             "odd": 1.10, "ret": 0.10}]
    monkeypatch.setattr("betgsn.value_strategy.collect_bets",
                        lambda *a, **k: list(bets))
    result = validate()
    assert result.n_bets == 1
    assert result.cache_fingerprint == _fp(corpus_signature="corpus-2")


def test_cache_miss_when_closing_changes(monkeypatch):
    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp(closing=False)))
    bets = [{"d": "2025-01-01", "lg": "E0", "mkt": MARKETS[0],
             "odd": 1.10, "ret": 0.10}]
    monkeypatch.setattr("betgsn.value_strategy.collect_bets",
                        lambda *a, **k: list(bets))
    result = validate(closing=True)  # validacao sobre odds de fechamento
    assert result.n_bets == 1
    assert result.cache_fingerprint == _fp(closing=True)


def test_legacy_cache_without_fingerprint_is_invalid(monkeypatch):
    """Cache antigo (sem fingerprint) e rejeitado, nao lido como atual."""
    _patch_corpus(monkeypatch, "corpus-1")
    payload = _validation_payload("irrelevante")
    payload.pop("cache_fingerprint")
    _write_cache(payload)

    assert cached_validation() is None
    bets = [{"d": "2025-01-01", "lg": "E0", "mkt": MARKETS[0],
             "odd": 1.10, "ret": 0.10}]
    monkeypatch.setattr("betgsn.value_strategy.collect_bets",
                        lambda *a, **k: list(bets))
    result = validate()
    assert result.n_bets == 1


def test_validate_writes_fingerprint(monkeypatch, tmp_path):
    _patch_corpus(monkeypatch, "corpus-1")
    monkeypatch.setattr(
        "betgsn.value_strategy._validation_cache_path",
        lambda: tmp_path / "value_validation.json",
    )
    bets = [{"d": "2025-01-01", "lg": "E0", "mkt": MARKETS[0],
             "odd": 1.10, "ret": 0.10}]
    monkeypatch.setattr("betgsn.value_strategy.collect_bets",
                        lambda *a, **k: list(bets))
    validate()
    payload = json.loads(
        (tmp_path / "value_validation.json").read_text(encoding="utf-8"))
    assert payload["cache_fingerprint"] == _fp()


# --------------------------------------------------------- decisao


def test_cached_validation_returns_none_on_corpus_mismatch(monkeypatch):
    """Cache de outro corpus nao e evidencia desta regra."""
    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp()))
    _patch_corpus(monkeypatch, "corpus-2")
    assert cached_validation() is None


def test_cached_validation_returns_value_on_match(monkeypatch):
    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp()))
    val = cached_validation()
    assert val is not None
    assert val.roi == 0.02


def test_cached_validation_none_without_cache(monkeypatch):
    _patch_corpus(monkeypatch, "corpus-1")
    path = _validation_cache_path()
    if path.exists():
        path.unlink()
    assert cached_validation() is None


def test_decision_ignores_stale_cache(monkeypatch):
    """_quant_decision cai para as constantes quando o cache nao bate.

    O caminho de decisao NAO pode consumir ROI de um cache medido sobre
    outro corpus: voltar para os parametros validados constantes e a
    saida segura.
    """
    from betgsn.api.service import BetgsnService
    from betgsn.staking import EDGE_ROI

    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp()))
    _patch_corpus(monkeypatch, "corpus-2")

    svc = BetgsnService()
    decision = svc._quant_decision("timestamped")
    # cache invalido -> constantes -> decisao ainda e BET (a regra
    # validada constante sustenta), mas o ROI e o da constante, nao o
    # do cache de outro corpus
    assert decision.action == "BET"
    assert decision.conservative_roi is not None
    assert decision.conservative_roi == pytest.approx(
        EDGE_ROI - 1.6448536269514722 * 0.0054)
