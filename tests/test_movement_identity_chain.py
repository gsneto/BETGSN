"""Cadeia de identidade das odds: captura -> store -> movement (C1/C2).

O QUE ESTA EM JOGO
------------------
`/api/odds/comparison` mostra odds atuais do snapshot (CSV), mas
`/api/movement` reportava n_observations=0 para TUDO. A investigacao
provou duas quebras reais:

    1. `movement()` so examinava `fixtures[:20]` — as primeiras 20
       fixtures com odds sao ligas obscuras de 18/09; as partidas COM
       observacoes no store estavam nas posicoes 383-598 da lista.
       O cap escondia dados reais que o store tinha.

    2. A captura com `--sports` NAO carregava fixtures: sem
       FixtureMatchIndex, eventos de provider com nomes divergentes dos
       nomes FDUK eram gravados sob a chave do PROVIDER — a leitura
       (que usa a event_key DO FIXTURE) nunca os encontrava.

A regra arquitetural deste teste: UMA identidade canonica —
`odds_normalize.event_key(home, away, kickoff_utc)` — usada na escrita
(fixture casado) e na leitura (fx.event_key). "Home vs Away" e rotulo
de exibicao, nunca chave de store.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache
from betgsn.football_data_uk import parse_fixture_row
from betgsn.odds_provider import OddsFetchRequest, OddsProviderFetch
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.timeutil import utc_key

_REPO = Path(__file__).resolve().parents[1]

MARKET = "Resultado Final (1X2)"

#: Observacoes REAIS do bookmaker (horario da observacao, nao da captura).
#: No passado em relacao ao relogio do teste: movement so ve observacoes
#: point-in-time (timestamp <= agora) — regra PIT preservada.
OBS_TS = "2026-09-20T10:00:00Z"
OBS_TS_LATER = "2026-09-21T10:00:00Z"

KICKOFF_LOCAL = ("26/09/2026", "15:00")          # Europe/London (BST)
KICKOFF_UTC = "2026-09-26T14:00:00Z"


# ------------------------------------------------------------------ fixtures


def _f1_row(home: str, away: str, date: str = KICKOFF_LOCAL[0],
            time_: str = KICKOFF_LOCAL[1]) -> dict[str, str]:
    """Linha no formato REAL do main.csv de fixtures (ligas principais)."""
    return {
        "Div": "F1", "Date": date, "Time": time_,
        "HomeTeam": home, "AwayTeam": away, "Referee": "",
        "B365H": "2.20", "B365D": "3.30", "B365A": "3.10",
    }


def _fixture(home="Lens", away="Lyon"):
    fx = parse_fixture_row(_f1_row(home, away))
    assert fx is not None, "parser real rejeitou a linha de fixture"
    return fx


def _provider_event(home="Lens", away="Lyon", price=2.20,
                    commence=KICKOFF_UTC, ts=OBS_TS) -> dict:
    """Evento cru no formato The Odds API com timestamp REAL por outcome."""
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": price, "timestamp": ts},
                    {"name": "Draw", "price": 3.40, "timestamp": ts},
                    {"name": away, "price": 3.20, "timestamp": ts},
                ]}],
            },
        ],
    }


class ContractProvider:
    """Provider de contrato (FASE B) que fala o shape The Odds API."""

    def __init__(self, name: str, events: list[dict]):
        self.name = name
        self._events = events

    def available(self) -> bool:
        return True

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return ("F1",) if scope == "soccer_france_ligue_one" else ()

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        from betgsn.odds_normalize import normalize_events

        quotes = normalize_events(
            self._events, self.name, request.fetched_at,
            sport_key="soccer_france_ligue_one")
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(self._events),
            snapshot_provider=f"{self.name.lower()}-live",
        )


def _capture(tmp_path: Path, providers, fixtures, now=None):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        providers, OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h",
        store=store,
        fixtures=fixtures,
    )
    report = capture.capture(
        ["soccer_france_ligue_one"],
        now=now or datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc),
    )
    return report, store


def _patch_for_api(monkeypatch, fixtures: list, db_path: Path) -> None:
    """Aponta a API para os fixtures e o store do teste."""
    from betgsn.football_data_uk import FootballDataClient
    import betgsn.odds_snapshots as snap_mod

    monkeypatch.setattr(FootballDataClient, "load_fixtures",
                        lambda self: list(fixtures))
    real = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(snap_mod, "OddsSnapshotStore",
                        lambda *a, **k: real(db_path))


def _movement_row(overview, match: str, outcome: str):
    return next(
        m for m in overview.movements
        if m.match == match and m.outcome == outcome
    )


# ==========================================================================
# 1. Cadeia inteira: fixture FDUK -> captura -> store -> movement
# ==========================================================================


def test_lens_vs_lyon_full_chain_capture_to_movement(tmp_path, monkeypatch):
    """Fixture real-like (parser FDUK), quote de provider com timestamp
    REAL, captura com indice de fixtures, e a LEITURA da API encontra a
    MESMA partida pelo rotulo 'Lens vs Lyon' — que resolve para a chave
    canonica `lens|lyon|...`, nunca por igualdade textual de 'vs'."""
    from betgsn.api.service import BetgsnService
    from betgsn.odds_normalize import event_key

    fx = _fixture("Lens", "Lyon")
    assert fx.event_key == event_key("Lens", "Lyon", KICKOFF_UTC)

    provider = ContractProvider("The Odds API", [_provider_event(price=2.20)])
    report, store = _capture(tmp_path, provider, [fx])
    assert report.errors == []
    assert report.observations_saved == 3

    # escrita: sob a event_key DO FIXTURE, com timestamp REAL preservado
    rows = store.all_observations(fx.event_key)
    assert len(rows) == 3
    assert all(r.timestamp == OBS_TS for r in rows)  # nunca o stamp da captura
    assert all(r.provider == "The Odds API" for r in rows)
    assert all(r.bookmaker == "Pinnacle" for r in rows)
    assert all(r.kickoff == KICKOFF_UTC for r in rows)

    _patch_for_api(monkeypatch, [fx], tmp_path / "odds.db")
    overview = BetgsnService().movement()

    # leitura: 'Lens vs Lyon' e o rotulo; a linha foi encontrada
    row = _movement_row(overview, "Lens vs Lyon", "1")
    assert row.n_observations == 1
    assert row.status == "INSUFFICIENT_DATA"   # 1 observacao: sem movimento
    assert row.price_delta is None             # nada inventado
    assert row.market_direction is None
    assert row.opening_odd == pytest.approx(2.20)
    assert row.current_odd == pytest.approx(2.20)
    assert row.n_books == 1

    # as demais linhas da mesma partida tambem encontram a observacao
    for oc in ("X", "2"):
        r = _movement_row(overview, "Lens vs Lyon", oc)
        assert r.n_observations == 1
        assert r.status == "INSUFFICIENT_DATA"


def test_identity_survives_divergent_representations(tmp_path):
    """Chave do provider (nomes dele) NAO e a chave do store quando ha
    fixture casado — e 'Home vs Away' nunca e chave de nada."""
    from betgsn.odds_normalize import event_key

    fx = _fixture("Lens", "Lyon")
    provider = ContractProvider("The Odds API", [_provider_event()])
    _, store = _capture(tmp_path, provider, [fx])

    provider_key = event_key("Lens", "Lyon", KICKOFF_UTC)
    # neste caso os nomes coincidem: a chave do provider E a do fixture.
    # a chave de EXIBICAO nunca encontra nada:
    assert store.all_observations("Lens vs Lyon") == []
    assert store.all_observations(fx.event_key)
    assert provider_key == fx.event_key


# ==========================================================================
# 2. Movement examina TODA a populacao (o cap [:20] escondia dados)
# ==========================================================================


def test_movement_covers_fixtures_beyond_position_20(tmp_path, monkeypatch):
    """Partida observada no fim de uma lista longa: movement encontra.

    Regressao do cap `fixtures[:20]`: as observacoes reais ficavam
    invisiveis quando a partida nao estava entre as 20 primeiras.
    """
    from betgsn.api.service import BetgsnService

    observed = _fixture("Lens", "Lyon")
    others = []
    for i in range(30):
        others.append(_fixture(f"Team {i:02d} Home", f"Team {i:02d} Away"))

    provider = ContractProvider("The Odds API", [_provider_event()])
    _, store = _capture(tmp_path, provider, [observed])
    assert store.all_observations(observed.event_key)

    # a partida observada esta na ULTIMA posicao da lista (posicao 31)
    fixtures = others + [observed]
    _patch_for_api(monkeypatch, fixtures, tmp_path / "odds.db")
    overview = BetgsnService().movement()

    row = _movement_row(overview, "Lens vs Lyon", "1")
    assert row.n_observations == 1
    # TODAS as fixtures com odds+kickoff viram linhas monitoradas
    matches = {m.match for m in overview.movements}
    assert "Team 00 Home vs Team 00 Away" in matches
    assert "Team 29 Home vs Team 29 Away" in matches
    assert "Lens vs Lyon" in matches


# ==========================================================================
# 3. Multi-provider: observacoes distintas coexistem, nada se perde
# ==========================================================================


def test_two_providers_same_line_different_timestamps_coexist(
        tmp_path, monkeypatch):
    """Provider A (ts1, odd 2.20) e provider B (ts2, odd 2.50), mesma
    partida, MESMA casa: duas observacoes semanticamente distintas
    coexistem no store; movement ve 2 observacoes com movimento REAL."""
    from betgsn.api.service import BetgsnService

    fx = _fixture("Lens", "Lyon")
    early = ContractProvider(
        "ParlayAPI", [_provider_event(price=2.20, ts=OBS_TS)])
    late = ContractProvider(
        "The Odds API", [_provider_event(price=2.50, ts=OBS_TS_LATER)])

    report, store = _capture(tmp_path, [early, late], [fx])
    assert report.observations_saved == 6  # 3 de cada, nenhuma descartada

    rows = sorted(
        (r for r in store.all_observations(fx.event_key) if r.outcome == "1"),
        key=lambda r: r.timestamp,
    )
    assert len(rows) == 2                       # coexistencia, sem perda
    assert [r.timestamp for r in rows] == [OBS_TS, OBS_TS_LATER]
    assert [r.provider for r in rows] == ["ParlayAPI", "The Odds API"]
    assert [r.odd for r in rows] == [2.20, 2.50]

    _patch_for_api(monkeypatch, [fx], tmp_path / "odds.db")
    row = _movement_row(BetgsnService().movement(), "Lens vs Lyon", "1")
    assert row.n_observations == 2
    assert row.status in {"MOVING", "STABLE"}   # movimento so com 2+
    assert row.opening_odd == pytest.approx(2.20)
    assert row.current_odd == pytest.approx(2.50)
    assert row.price_delta == pytest.approx(0.30)


def test_same_physical_line_same_timestamp_is_explicit_collision(tmp_path):
    """Chave FISICA identica (mesma linha, casa E timestamp) de providers
    diferentes: exatamente uma observacao fica, a outra entra na
    contabilidade de colisao — perda EXPLICITA, nunca silenciosa."""
    a = ContractProvider("ParlayAPI", [_provider_event(price=2.20)])
    b = ContractProvider("The Odds API", [_provider_event(price=2.05)])

    report, store = _capture(tmp_path, [a, b], [_fixture()])
    # 3 do primeiro provider; as 3 do segundo sao colisoes explicitas
    assert report.observations_saved == 3
    assert len(report.accounting) == 3
    assert all(e.action == "collision" for e in report.accounting)
    assert all(e.kept_by == "ParlayAPI" for e in report.accounting)
    assert len(store.all_observations(_fixture().event_key)) == 3


# ==========================================================================
# 4. PIT preservado: observacao pos-kickoff nunca e gravada
# ==========================================================================


def test_observation_at_or_after_kickoff_is_rejected(tmp_path):
    """Timestamp >= kickoff viola o contrato point-in-time: a observacao
    nao e gravada — nem corrigida, nem clamped."""
    fx = _fixture("Lens", "Lyon")
    at_kickoff = ContractProvider(
        "The Odds API",
        [_provider_event(ts=KICKOFF_UTC)],        # exatamente no kickoff
    )
    after = ContractProvider(
        "ParlayAPI",
        [_provider_event(commence=KICKOFF_UTC,
                         ts="2026-09-26T15:00:00Z")],  # depois
    )
    report, store = _capture(tmp_path, [at_kickoff, after], [fx])
    assert store.all_observations(fx.event_key) == []
    # as quotes foram recebidas mas nenhuma virou observacao utilizavel
    assert report.observations_saved == 0


# ==========================================================================
# 5. CLI --capture-odds: --sports tambem resolve identidade via fixtures
# ==========================================================================


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("betgsn_cli_identity", _REPO / "betgsn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_capture_with_sports_resolves_fixture_identity(
        monkeypatch, capsys, tmp_path):
    """"--sports" dado: a captura AINDA carrega os fixtures e grava sob a
    event_key DO FIXTURE quando o nome do provider diverge (alias).

    Regressao real: com --sports o CLI nao carregava fixtures; eventos
    com nomes divergentes ficavam sob a chave do provider e a leitura
    (fx.event_key) nunca os encontrava.
    """
    import betgsn.odds_registry as registry_mod
    from betgsn.config import output_root
    from betgsn.football_data_uk import FootballDataClient
    from betgsn.odds_normalize import event_key
    from betgsn.odds_registry import OddsProviderRegistry, ProviderSpec
    import betgsn.team_aliases as alias_mod

    monkeypatch.setenv("BETGSN_OUTPUT_DIR", str(tmp_path))

    fx = _fixture("Lens", "Lyon")
    monkeypatch.setattr(FootballDataClient, "load_fixtures",
                        lambda self: [fx])
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "test-sig")

    # provider publica nomes DIVERGENTES; alias versionado traduz
    monkeypatch.setattr(
        alias_mod, "load_team_aliases",
        lambda: {("F1", "RC Lens"): "Lens",
                 ("F1", "Olympique Lyonnais"): "Lyon"},
    )

    provider = ContractProvider(
        "The Odds API",
        [_provider_event(home="RC Lens", away="Olympique Lyonnais")],
    )
    registry = OddsProviderRegistry()
    registry.register(ProviderSpec(
        name="The Odds API", factory=lambda: provider, priority=1))
    monkeypatch.setattr(registry_mod, "default_odds_registry",
                        lambda: registry)

    cli = _load_cli_module()
    rc = cli.capture_odds_cli([
        "--sports=soccer_france_ligue_one",
        "--providers=The Odds API",
    ])
    assert rc == 0

    store = OddsSnapshotStore(output_root() / "odds_snapshots.db")
    # gravado sob a chave DO FIXTURE (nomes FDUK), nao sob a do provider
    rows = store.all_observations(fx.event_key)
    assert len(rows) == 3
    divergent_key = event_key("RC Lens", "Olympique Lyonnais", KICKOFF_UTC)
    assert store.all_observations(divergent_key) == []
    assert all(r.timestamp == OBS_TS for r in rows)
