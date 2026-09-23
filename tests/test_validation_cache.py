"""I-14 — cache de validacao nao pode misturar corpora/configuracoes.

O cache de `value_strategy` (output/value_validation.json) alimenta a
DECISAO de apostar (`_quant_decision` -> `decide_bet`). Reaproveitar
uma validacao feita sobre outro corpus, outros mercados, outro tipo de
odd ou outros parametros seria servir evidencia de outra medicao como
se fosse atual.

Aqui se prova que o fingerprint:

- e deterministico (mesma entrada -> mesma chave);
- muda com CADA elemento relevante (regra, mercados, fechamento,
  corpus, parametros de bootstrap e faixas de odd — Fase D);
- e o gate REAL do cache (hit so com fingerprint identico; miss
  recalcula);
- protege a decisao: cache de outro corpus nao entra em decide_bet.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from betgsn.value_strategy import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
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


# ------------------------------------------------- Fase D: bootstrap/bandas


def test_fingerprint_changes_with_bootstrap_resamples():
    """Outro numero de resamples = outros ICs persistidos = outra medicao."""
    assert _fp(bootstrap_resamples=BOOTSTRAP_RESAMPLES + 1000) != _fp()


def test_fingerprint_changes_with_bootstrap_seed():
    """Outra seed = outros ICs persistidos = outra medicao."""
    assert _fp(bootstrap_seed=BOOTSTRAP_SEED + 1) != _fp()


def test_fingerprint_changes_with_odd_bands():
    """Outras faixas = outro `by_band` persistido = outra medicao."""
    assert _fp(odd_bands=((1.01, 1.10), (1.10, 1.20))) != _fp()


def test_cache_from_old_schema_is_rejected(monkeypatch):
    """Cache escrito no schema v2 (fingerprint antigo) nao e servido.

    O fingerprint v3 inclui schema + bootstrap + faixas: o JSON legado
    em disco (pre-Fase D) nao bate e e recalculado, nunca lido como se
    fosse validacao atual. O fallback seguro para as constantes
    validadas cobre o intervalo sem cache (teste abaixo ja o prova).
    """
    from betgsn.value_strategy import ODD_BANDS

    _patch_corpus(monkeypatch, "corpus-1")
    # Fingerprint EXATAMENTE como era no schema v2 (payload antigo,
    # sem bootstrap/faixas) — o que um cache real pre-Fase D carrega.
    legacy_payload = {
        "schema": 2,
        "max_odd": float(MAX_ODD),
        "min_books": int(MIN_BOOKS),
        "markets": list(MARKETS),
        "closing": False,
        "corpus": "corpus-1",
    }
    assert ODD_BANDS  # sanity: faixas existem no payload atual
    legacy_fp = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()[:16]
    assert legacy_fp != _fp()  # schema novo produz chave diferente
    _write_cache(_validation_payload(legacy_fp))

    assert cached_validation() is None
    bets = [{"d": "2025-01-01", "lg": "E0", "mkt": MARKETS[0],
             "odd": 1.10, "ret": 0.10}]
    monkeypatch.setattr("betgsn.value_strategy.collect_bets",
                        lambda *a, **k: list(bets))
    result = validate()
    assert result.n_bets == 1
    assert result.cache_fingerprint == _fp()


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
    saida segura — e o ROI da decisao e o da constante, nao o do cache
    de outro corpus.

    O ACTION agora e NO_BET: o promotion gate e avaliado no caminho
    operacional e, sem cache OOS valido, reprova (evidencia ausente nao
    e aprovada). O ROI conservador continua o da constante — a fonte
    da vantagem e o que este teste fixa.
    """
    from betgsn.api.service import BetgsnService
    from betgsn.value_strategy import EDGE_ROI

    _patch_corpus(monkeypatch, "corpus-1")
    _write_cache(_validation_payload(_fp()))
    _patch_corpus(monkeypatch, "corpus-2")

    svc = BetgsnService()
    decision = svc._quant_decision("timestamped")
    # cache invalido -> promotion reprova -> NO BET (gate encadeado)
    assert decision.action == "NO_BET"
    assert any(
        c.name == "promocao_da_estrategia" and not c.passed
        for c in decision.checks
    )
    # mas o ROI reportado e o da CONSTANTE, nao o do cache stale
    assert decision.conservative_roi is not None
    assert decision.conservative_roi == pytest.approx(
        EDGE_ROI - 1.6448536269514722 * 0.0054)
