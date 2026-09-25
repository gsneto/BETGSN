"""Cache do /api/signals: honestidade + trava.

Contrato:
  - primeira request (cache miss): resposta NAO tem bloco `cache`
    (contrato "computo fresco NAO expoe cache").
  - segunda request identica dentro do TTL: `cache.status='STALE'`,
    `age_seconds >= 0`, `key_fingerprint` estavel, `computed_at` bate
    com o `generated_at` do relatorio original.
  - `LIVE fresco` nao existe como valor: `Literal['STALE','EXPIRED']`.
  - snapshot recalculado (generated_at muda) invalida a chave — nao
    devolve STALE contaminado, computa fresco de novo.
  - trava por chave: N requests concorrentes disparam UMA computacao.
  - source=synthetic NAO passa pelo cache (sempre computa).
"""
from __future__ import annotations

import time as _time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import betgsn.api.server as server_mod
from betgsn.api.server import _signals_cache_reset_for_tests, app
from betgsn.api import schemas as S


@pytest.fixture(autouse=True)
def _reset_cache():
    _signals_cache_reset_for_tests()
    yield
    _signals_cache_reset_for_tests()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# Fake report leve — o teste de trava e frescor nao precisa do pipeline
# real (60s). Substituimos `real_signal_report` no service por um stub
# controlavel que conta chamadas e devolve um SignalReport valido.
def _empty_kpis() -> S.SignalsKpis:
    return S.SignalsKpis(
        total=0, strong=0, medium=0, weak=0,
        strong_pct=0.0, medium_pct=0.0, weak_pct=0.0,
        expected_profit=0.0, expected_profit_pct=0.0,
        avg_stake_pct=0.0, total_exposure=0.0, total_exposure_pct=0.0,
        worst_case_loss=0.0,
        gross_profit_if_all_win=0.0, exposure_scaled_by=0.0, max_ev=0.0,
    )


def _stub_report(counter: dict) -> S.SignalReport:
    counter["calls"] += 1
    return S.SignalReport(
        provenance=S.Provenance(),
        generated_at="2026-09-25T12:00:00Z",
        bankroll=1000.0,
        kpis=_empty_kpis(),
        signals=[],
        top_tips=[],
        source="real",
        source_detail="stub",
        skipped_no_rating=0,
        skipped_insufficient_books=0,
    )


def _install_stub(monkeypatch, counter, *,
                  snapshot_generated_at="2026-09-25T11:59:00Z"):
    """Substitui o pipeline pesado por um stub barato e forca o snapshot
    para um valor conhecido (chave estavel). Nao dispara o pipeline real:
    a config vem do dataclass default do schema."""
    from betgsn.api.server import _svc

    svc = _svc()
    monkeypatch.setattr(svc, "real_signal_report",
                        lambda **kw: _stub_report(counter))

    fake_config = S.ModelConfiguration()  # defaults do schema

    class _FakeSnap:
        source = "real"
        generated_at = snapshot_generated_at
        computed_in_ms = 1.0
        config = fake_config

    monkeypatch.setattr(server_mod, "_snapshot", lambda: _FakeSnap())


def test_cache_miss_nao_expoe_bloco_cache(client, monkeypatch):
    counter = {"calls": 0}
    _install_stub(monkeypatch, counter)

    r = client.get("/api/signals", params={"source": "real"})
    assert r.status_code == 200
    body = r.json()
    assert counter["calls"] == 1
    assert body.get("cache") is None, "computo fresco NAO expoe bloco cache"


def test_hit_dentro_do_ttl_marca_stale(client, monkeypatch):
    counter = {"calls": 0}
    _install_stub(monkeypatch, counter)

    r1 = client.get("/api/signals", params={"source": "real"})
    assert r1.status_code == 200 and counter["calls"] == 1

    r2 = client.get("/api/signals", params={"source": "real"})
    assert r2.status_code == 200
    assert counter["calls"] == 1, "segunda leitura dentro do TTL nao recomputa"

    cache = r2.json()["cache"]
    assert cache is not None
    assert cache["status"] == "STALE"
    assert cache["age_seconds"] >= 0.0
    assert cache["ttl_seconds"] == server_mod.SIGNALS_CACHE_TTL_SECONDS
    assert cache["key_fingerprint"], "fingerprint da chave nao pode ser vazio"
    assert cache["computed_at"] == r1.json()["generated_at"]


def test_status_nunca_e_live_fresco(client, monkeypatch):
    """O contrato proibe 'LIVE fresco' como valor. O schema garante isso
    via Literal['STALE','EXPIRED'] e o path fresco NAO seta o bloco."""
    counter = {"calls": 0}
    _install_stub(monkeypatch, counter)
    _ = client.get("/api/signals", params={"source": "real"})
    r2 = client.get("/api/signals", params={"source": "real"})
    assert r2.json()["cache"]["status"] in ("STALE", "EXPIRED")
    assert r2.json()["cache"]["status"] != "LIVE"


def test_expira_apos_ttl_recomputa_fresco(client, monkeypatch):
    counter = {"calls": 0}
    _install_stub(monkeypatch, counter)
    monkeypatch.setattr(server_mod, "SIGNALS_CACHE_TTL_SECONDS", 0.05)

    r1 = client.get("/api/signals", params={"source": "real"})
    assert r1.status_code == 200 and counter["calls"] == 1
    _time.sleep(0.10)
    r2 = client.get("/api/signals", params={"source": "real"})
    assert r2.status_code == 200
    assert counter["calls"] == 2, "apos o TTL a request seguinte recomputa"
    assert r2.json().get("cache") is None, "recomputo NAO devolve STALE"


def test_snapshot_novo_invalida_a_chave(client, monkeypatch):
    counter = {"calls": 0}
    _install_stub(monkeypatch, counter,
                  snapshot_generated_at="2026-09-25T11:59:00Z")

    r1 = client.get("/api/signals", params={"source": "real"})
    assert counter["calls"] == 1 and r1.json().get("cache") is None

    # Muda o snapshot -> muda a chave -> cache nao aplica.
    _install_stub(monkeypatch, counter,
                  snapshot_generated_at="2026-09-25T12:05:00Z")
    r2 = client.get("/api/signals", params={"source": "real"})
    assert counter["calls"] == 2
    assert r2.json().get("cache") is None, "chave nova = computo fresco"


def test_chave_separa_por_market_keys(client, monkeypatch):
    counter = {"calls": 0}
    _install_stub(monkeypatch, counter)

    client.get("/api/signals", params={"source": "real"})
    client.get("/api/signals", params={"source": "real",
                                       "market_keys": "h2h"})
    client.get("/api/signals", params={"source": "real",
                                       "market_keys": "h2h"})
    assert counter["calls"] == 2, "keys diferentes = chaves diferentes"


def test_trava_por_chave_serializa_computo_concorrente(monkeypatch):
    """N threads em paralelo pedindo a MESMA chave => 1 computacao.

    NAO usa TestClient (Starlette serializa requests para o mesmo app).
    Chama o endpoint como funcao Python — a lock e do modulo, entao o
    contrato de trava por chave e o que estamos verificando.
    """
    counter = {"calls": 0}

    def _slow_report(**_kw):
        counter["calls"] += 1
        _time.sleep(0.3)  # simula pipeline pesado
        return _stub_report({"calls": 0})  # counter isolado

    from betgsn.api.server import _svc, signals as signals_endpoint

    svc = _svc()
    monkeypatch.setattr(svc, "real_signal_report", _slow_report)

    class _FakeSnap:
        source = "real"
        generated_at = "2026-09-25T13:00:00Z"
        computed_in_ms = 1.0
        config = S.ModelConfiguration()

    monkeypatch.setattr(server_mod, "_snapshot", lambda: _FakeSnap())

    def _fire():
        return signals_endpoint(source="real", bankroll=None,
                                min_ev=None, use_xg=None, market_keys=None)

    slow_started = _time.monotonic()
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _i: _fire(), range(5)))
    dt = _time.monotonic() - slow_started

    assert counter["calls"] == 1, (
        f"trava violada: {counter['calls']} computacoes para a mesma chave"
    )
    # 5 threads paralelas com pipeline de 0.3s: primeira computa, as 4
    # seguintes esperam a mesma lock e leem do cache => tempo <=~ 0.5s +
    # overhead de scheduling. Se serializasse tudo daria ~1.5s.
    assert dt < 1.5, f"threads serializaram alem do razoavel ({dt:.2f}s)"

    fresh = [r for r in results if r.cache is None]
    stale = [r for r in results if r.cache is not None]
    assert len(fresh) == 1, f"mais de uma resposta fresca: {len(fresh)}"
    assert len(stale) == 4
    for r in stale:
        assert r.cache.status == "STALE"


def test_synthetic_nao_passa_pelo_cache(client, monkeypatch):
    """Modo demo nao pode cachear (masca bugs de reprodutibilidade)."""
    # Chamada 1
    r1 = client.get("/api/signals", params={"source": "synthetic"})
    assert r1.status_code == 200
    assert r1.json().get("cache") is None
    # Chamada 2
    r2 = client.get("/api/signals", params={"source": "synthetic"})
    assert r2.status_code == 200
    assert r2.json().get("cache") is None, "synthetic nunca recebe cache"
