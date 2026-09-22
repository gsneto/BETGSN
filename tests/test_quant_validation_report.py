"""Fase D — o relatorio quant NAO serve cache stale como validacao atual.

`tools/quant_validation_report.py` le `value_validation.json`
diretamente do disco. Sem o gate de fingerprint (o mesmo mecanismo do
I-14, agora estendido com bootstrap/faixas — Fase D), uma validacao
medida sobre outro corpus/regra/parametros seria reportada como
evidencia corrente.

Aqui se prova que:

- cache com fingerprint que NAO bate -> `cache_stale: True` (valores
  preservados apenas como dado historico, nunca como validacao);
- cache com fingerprint atual -> `cache_stale: False`;
- cache legado sem fingerprint -> stale;
- sem arquivo -> `value_validation` None (comportamento preservado).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.quant_validation_report import betting_evidence  # noqa: E402


def _bands(tmp_path: Path) -> Path:
    p = tmp_path / "odds_bands.json"
    p.write_text(json.dumps({
        "n_bets_considered": 10,
        "financial_status": "EXPLORATORY_CSV_UNTIMESTAMPED",
        "bands": [],
    }), encoding="utf-8")
    return p


def _value(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "value_validation.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _payload(fingerprint):
    return {
        "roi": 0.02, "se": 0.005, "ci_low": 0.01, "ci_high": 0.03,
        "n_bets": 1000, "cache_fingerprint": fingerprint,
    }


def _patch_current_corpus(monkeypatch, signature="corpus-1"):
    from betgsn.football_data_uk import FootballDataClient
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: signature)


def _current_fingerprint():
    from betgsn.value_strategy import (
        BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, MARKETS, MAX_ODD, MIN_BOOKS,
        ODD_BANDS, validation_cache_fingerprint,
    )
    return validation_cache_fingerprint(
        max_odd=MAX_ODD, min_books=MIN_BOOKS, markets=MARKETS,
        closing=False, corpus_signature="corpus-1",
        bootstrap_resamples=BOOTSTRAP_RESAMPLES,
        bootstrap_seed=BOOTSTRAP_SEED, odd_bands=ODD_BANDS,
    )


def test_stale_cache_is_marked_not_served_as_current(monkeypatch, tmp_path):
    _patch_current_corpus(monkeypatch, "corpus-1")
    # fingerprint de OUTRA medicao (outro corpus)
    ev = betting_evidence(_bands(tmp_path), _value(tmp_path, _payload("fp-outro")))
    vv = ev["value_validation"]
    assert vv is not None
    assert vv["cache_stale"] is True
    # valores preservados como dado historico, com a marcacao explicita
    assert vv["roi"] == 0.02
    assert vv["n_bets"] == 1000
    assert "CACHE STALE" in vv["note"]
    assert "historico" in vv["note"]


def test_current_cache_is_not_stale(monkeypatch, tmp_path):
    _patch_current_corpus(monkeypatch, "corpus-1")
    ev = betting_evidence(
        _bands(tmp_path), _value(tmp_path, _payload(_current_fingerprint())))
    vv = ev["value_validation"]
    assert vv["cache_stale"] is False
    assert "CACHE STALE" not in vv["note"]


def test_cache_from_changed_corpus_is_stale(monkeypatch, tmp_path):
    """CSV novo chegou: a validacao antiga nao e a medicao atual."""
    _patch_current_corpus(monkeypatch, "corpus-1")
    fp = _current_fingerprint()
    _patch_current_corpus(monkeypatch, "corpus-2")  # corpus mudou
    ev = betting_evidence(_bands(tmp_path), _value(tmp_path, _payload(fp)))
    assert ev["value_validation"]["cache_stale"] is True


def test_legacy_cache_without_fingerprint_is_stale(monkeypatch, tmp_path):
    _patch_current_corpus(monkeypatch, "corpus-1")
    payload = _payload("irrelevante")
    payload.pop("cache_fingerprint")
    ev = betting_evidence(_bands(tmp_path), _value(tmp_path, payload))
    assert ev["value_validation"]["cache_stale"] is True


def test_missing_value_file_keeps_none(monkeypatch, tmp_path):
    _patch_current_corpus(monkeypatch, "corpus-1")
    ev = betting_evidence(_bands(tmp_path), tmp_path / "inexistente.json")
    assert ev["value_validation"] is None
