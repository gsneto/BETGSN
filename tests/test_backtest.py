"""Testes do modulo de backtest do BETGSN.

O bloco mais importante e o de DATA LEAKAGE: o backtest nao pode, em
nenhuma circunstancia, usar informacao posterior ao kickoff.

Estrategia de teste
-------------------
1. Testes diretos: o corte temporal exclui o proprio jogo e tudo depois.
2. Testes de sensibilidade: alterar o resultado de uma partida FUTURA nao
   pode mudar o sinal de uma partida anterior (prova de ausencia de
   vazamento); alterar o resultado de uma partida PASSADA DEVE mudar o
   sinal (prova de que o teste nao e trivialmente verdadeiro).
3. Testes de agregacao: medias da liga e ratings usam so o passado.

Rodar:
    .venv\\Scripts\\python -m pytest tests -q
"""

from __future__ import annotations

import pytest

from betgsn.backtest_data import (
    HistoricalCorpus,
    MatchResult,
    NaiveSyntheticOddsSource,
    build_context,
    league_average_rating,
    league_goals_before,
    match_seed,
    realized_return,
    settle_outcome,
)
from betgsn.backtest_engine import (
    BacktestConfig,
    FrozenSignal,
    run_backtest,
    simulate_bankroll,
)
from betgsn.backtest_metrics import (
    MIN_SAMPLE,
    aggregate_metrics,
    brier_score,
    calibration_bins,
    compute_metrics,
    ev_buckets,
    log_loss,
    segment_rows,
    wilson_interval,
)
from betgsn.backtest_store import BacktestStore, compare_runs, simulation_to_json
from betgsn.data import build_dataset
from betgsn.model import HistoricalMatch, fit_ratings


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _match(day: str, home: str, away: str, hg: int, ag: int, **kw) -> HistoricalMatch:
    return HistoricalMatch(
        home=home, away=away, home_goals=hg, away_goals=ag,
        home_xg=kw.get("home_xg", float(hg)), away_xg=kw.get("away_xg", float(ag)),
        home_corners=kw.get("home_corners", 5), away_corners=kw.get("away_corners", 4),
        home_cards=kw.get("home_cards", 2), away_cards=kw.get("away_cards", 2),
        kickoff=f"{day} 16:00", league="Liga Teste", season="2025",
    )


@pytest.fixture
def abc_corpus() -> HistoricalCorpus:
    """Cenario A(01/05) -> B(05/05) -> C(10/05)."""
    return HistoricalCorpus([
        _match("2025-05-01", "Alpha", "Beta", 2, 1),
        _match("2025-05-05", "Alpha", "Gamma", 1, 1),
        _match("2025-05-10", "Beta", "Gamma", 3, 0),
    ])


@pytest.fixture(scope="module")
def local_corpus() -> HistoricalCorpus:
    return HistoricalCorpus(build_dataset().history)


@pytest.fixture(scope="module")
def standard_run(local_corpus):
    """Execucao padrao reutilizada por varios testes (evita refazer o fit)."""
    return run_backtest(local_corpus, BacktestConfig(min_history=200, min_ev=0.02))


# ==========================================================================
# 1. DATA LEAKAGE — corte temporal
# ==========================================================================


def test_matches_before_excludes_cutoff_and_future(abc_corpus):
    """Prever B (05/05) so pode ver A (01/05). C (10/05) e futuro."""
    prior = abc_corpus.matches_before("2025-05-05 16:00")
    assert len(prior) == 1
    assert prior[0].home == "Alpha" and prior[0].away == "Beta"
    assert all(m.kickoff < "2025-05-05 16:00" for m in prior)


def test_matches_before_is_strictly_less_than(abc_corpus):
    """O proprio jogo nunca entra no proprio contexto."""
    for cutoff in ("2025-05-01 16:00", "2025-05-05 16:00", "2025-05-10 16:00"):
        prior = abc_corpus.matches_before(cutoff)
        assert all(m.kickoff < cutoff for m in prior)


def test_simultaneous_matches_are_excluded():
    """Partidas com o MESMO kickoff nao entram: o resultado e desconhecido."""
    corpus = HistoricalCorpus([
        _match("2025-06-01", "A", "B", 1, 0),
        _match("2025-06-01", "C", "D", 2, 2),
        _match("2025-06-08", "A", "C", 0, 1),
    ])
    prior = corpus.matches_before("2025-06-01 16:00")
    assert prior == []
    prior2 = corpus.matches_before("2025-06-08 16:00")
    assert len(prior2) == 2  # ambas de 01/06 ja aconteceram


def test_future_result_change_does_not_affect_earlier_signal(local_corpus):
    """PROVA DE AUSENCIA DE LEAKAGE.

    Altera o resultado de partidas FUTURAS em relacao a um sinal e exige
    que o sinal congelado permaneca identico, campo por campo.
    """
    config = BacktestConfig(min_history=120, min_ev=0.02)
    base = run_backtest(local_corpus, config)

    # escolhe um sinal e corrompe TODAS as partidas posteriores a ele
    target = base.signals[len(base.signals) // 2]
    cutoff = target.signal.kickoff

    tampered = [
        HistoricalMatch(
            home=m.home, away=m.away,
            home_goals=(9 if m.kickoff > cutoff else m.home_goals),
            away_goals=(9 if m.kickoff > cutoff else m.away_goals),
            home_xg=m.home_xg, away_xg=m.away_xg,
            home_corners=m.home_corners, away_corners=m.away_corners,
            home_cards=m.home_cards, away_cards=m.away_cards,
            weight=m.weight, kickoff=m.kickoff, league=m.league, season=m.season,
        )
        for m in local_corpus.matches
    ]
    tampered_run = run_backtest(HistoricalCorpus(tampered), config)

    same = [
        s for s in tampered_run.signals
        if s.signal.kickoff == cutoff
        and s.signal.home == target.signal.home
        and s.signal.away == target.signal.away
    ]
    original = [
        s for s in base.signals
        if s.signal.kickoff == cutoff
        and s.signal.home == target.signal.home
        and s.signal.away == target.signal.away
    ]
    assert len(original) == len(same) > 0
    assert [s.signal for s in original] == [s.signal for s in same], (
        "sinal passado mudou quando o FUTURO foi alterado -> data leakage"
    )


def test_past_result_change_does_affect_later_signal(local_corpus):
    """SENSIBILIDADE: alterar o passado DEVE mudar o sinal (senao o teste
    anterior nao provaria nada)."""
    config = BacktestConfig(min_history=120, min_ev=0.02)
    base = run_backtest(local_corpus, config)
    target = base.signals[len(base.signals) // 2]
    cutoff = target.signal.kickoff

    tampered = [
        HistoricalMatch(
            home=m.home, away=m.away,
            home_goals=(7 if m.kickoff < cutoff else m.home_goals),
            away_goals=(0 if m.kickoff < cutoff else m.away_goals),
            home_xg=m.home_xg, away_xg=m.away_xg,
            home_corners=m.home_corners, away_corners=m.away_corners,
            home_cards=m.home_cards, away_cards=m.away_cards,
            weight=m.weight, kickoff=m.kickoff, league=m.league, season=m.season,
        )
        for m in local_corpus.matches
    ]
    tampered_run = run_backtest(HistoricalCorpus(tampered), config)

    def pick(run):
        return [
            s.signal for s in run.signals
            if s.signal.kickoff == cutoff
            and s.signal.home == target.signal.home
            and s.signal.away == target.signal.away
        ]

    original, changed = pick(base), pick(tampered_run)
    assert original and changed
    assert original != changed, (
        "alterar o passado nao mudou o sinal -> o contexto pode estar fixo/errado"
    )


def test_corpus_rejects_matches_without_kickoff():
    """Sem timestamp nao existe corte confiavel: o corpus recusa."""
    bad = HistoricalMatch(home="A", away="B", home_goals=1, away_goals=0, kickoff="")
    with pytest.raises(ValueError, match="sem kickoff"):
        HistoricalCorpus([bad])


def test_corpus_rejects_empty_history():
    with pytest.raises(ValueError, match="corpus vazio"):
        HistoricalCorpus([])


def test_league_goals_uses_only_prior(abc_corpus):
    """Agregacao nao pode usar o futuro."""
    prior = abc_corpus.matches_before("2025-05-10 16:00")
    assert len(prior) == 2
    # apenas A (2+1=3) e B (1+1=2) -> media 2.5; C (3+0=3) fica de fora
    assert league_goals_before(prior) == pytest.approx(2.5)
    # se C entrasse, a media seria 8/3
    assert league_goals_before(abc_corpus.matches) == pytest.approx(8 / 3)


def test_context_never_contains_future_information(abc_corpus):
    prior = abc_corpus.matches_before("2025-05-05 16:00")
    ctx = build_context(prior, cutoff="2025-05-05 16:00", market_keys=("1x2",))
    assert ctx.n_prior_matches == 1
    assert ctx.cutoff == "2025-05-05 16:00"
    # media de gols do unico jogo anterior (A 2x1 B)
    assert ctx.league_goals == pytest.approx(3.0)


def test_frozen_signal_has_no_result_field():
    """Garantia estrutural: o registro congelado nao tem campo de resultado."""
    campos = set(FrozenSignal.__dataclass_fields__)
    proibidos = {
        "outcome_result", "realized_return", "profit",
        "result_home_goals", "result_away_goals", "won",
    }
    assert not (campos & proibidos), (
        f"FrozenSignal expoe campo de resultado: {campos & proibidos}"
    )


def test_frozen_signals_are_immutable():
    """Congelado de verdade: nao da para mutar depois."""
    import dataclasses

    assert dataclasses.fields(FrozenSignal)
    assert FrozenSignal.__dataclass_params__.frozen  # type: ignore[attr-defined]


# ==========================================================================
# 2. Liquidacao (settlement)
# ==========================================================================


@pytest.mark.parametrize(
    ("market", "outcome", "hg", "ag", "expected"),
    [
        ("Resultado Final (1X2)", "1", 2, 1, "win"),
        ("Resultado Final (1X2)", "1", 1, 1, "loss"),
        ("Resultado Final (1X2)", "X", 1, 1, "win"),
        ("Resultado Final (1X2)", "2", 0, 1, "win"),
        ("Dupla Chance", "1X", 1, 1, "win"),
        ("Dupla Chance", "12", 1, 1, "loss"),
        ("Dupla Chance", "X2", 0, 3, "win"),
        ("Total de Gols", "Over 2.5", 2, 1, "win"),
        ("Total de Gols", "Over 2.5", 1, 1, "loss"),
        ("Total de Gols", "Under 2.5", 1, 1, "win"),
        ("Ambas Marcam", "BTTS Sim", 1, 1, "win"),
        ("Ambas Marcam", "BTTS Sim", 2, 0, "loss"),
        ("Ambas Marcam", "BTTS Nao", 2, 0, "win"),
        ("Total por Time", "Casa Over 1.5", 2, 0, "win"),
        ("Total por Time", "Casa Over 1.5", 1, 0, "loss"),
        ("Total por Time", "Fora Under 0.5", 3, 0, "win"),
    ],
)
def test_settlement_basic_markets(market, outcome, hg, ag, expected):
    assert settle_outcome(market, outcome, MatchResult(hg, ag)) == expected


@pytest.mark.parametrize(
    ("outcome", "hg", "ag", "expected"),
    [
        ("AH Casa -1.5", 3, 1, "win"),      # margem +0.5
        ("AH Casa -1.5", 2, 1, "loss"),     # margem -0.5
        ("AH Casa +1.5", 1, 2, "win"),      # margem +0.5
        ("AH Fora -1.5", 1, 3, "win"),
        ("AH Fora +1.5", 3, 1, "loss"),
        ("AH Casa -1", 2, 1, "push"),       # margem exatamente 0
        ("AH Fora -1", 1, 2, "push"),
        ("AH Casa +1", 1, 2, "push"),
        ("AH Casa -1", 3, 1, "win"),
        ("AH Casa -1", 1, 1, "loss"),
    ],
)
def test_settlement_asian_handicap_with_push(outcome, hg, ag, expected):
    assert settle_outcome("Handicap Asiatico", outcome, MatchResult(hg, ag)) == expected


def test_settlement_corners_and_cards():
    r = MatchResult(1, 0, home_corners=7, away_corners=4, home_cards=3, away_cards=2)
    assert settle_outcome("Escanteios", "Cantos Over 9.5", r) == "win"
    assert settle_outcome("Escanteios", "Cantos Under 9.5", r) == "loss"
    assert settle_outcome("Escanteios", "Casa Cantos Over 5.5", r) == "win"
    assert settle_outcome("Escanteios", "Fora Cantos Over 5.5", r) == "loss"
    assert settle_outcome("Cartoes", "Cartoes Over 3.5", r) == "win"
    assert settle_outcome("Cartoes", "Cartoes Under 3.5", r) == "loss"


def test_settlement_missing_data_returns_none_not_loss():
    """Dado ausente nunca vira derrota silenciosa."""
    sem_cantos = MatchResult(1, 0)
    assert settle_outcome("Escanteios", "Cantos Over 9.5", sem_cantos) is None
    assert settle_outcome("Cartoes", "Cartoes Over 3.5", sem_cantos) is None


def test_settlement_unknown_market_or_outcome_returns_none():
    r = MatchResult(1, 0, home_corners=1, away_corners=1, home_cards=1, away_cards=1)
    assert settle_outcome("Mercado Inexistente", "1", r) is None
    assert settle_outcome("Total de Gols", "Over banana", r) is None


def test_realized_return_and_push():
    assert realized_return("win", 2.5) == pytest.approx(1.5)
    assert realized_return("loss", 2.5) == pytest.approx(-1.0)
    assert realized_return("push", 2.5) == pytest.approx(0.0)


# ==========================================================================
# 3. Metricas
# ==========================================================================


def test_wilson_interval_bounds():
    lo, hi = wilson_interval(5, 10)
    assert lo < 0.5 < hi
    assert 0.0 <= lo <= hi <= 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_interval_shrinks_with_n():
    narrow = wilson_interval(500, 1000)
    wide = wilson_interval(5, 10)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_brier_score_known_values():
    assert brier_score([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
    assert brier_score([(0.0, 1), (1.0, 0)]) == pytest.approx(1.0)
    assert brier_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)
    assert brier_score([]) == 0.0


def test_log_loss_known_values():
    assert log_loss([(1.0, 1)]) == pytest.approx(0.0, abs=1e-6)
    assert log_loss([(0.5, 1)]) == pytest.approx(0.6931, abs=1e-4)
    assert log_loss([]) == 0.0


def test_calibration_and_metrics_from_run(standard_run):
    run = standard_run
    config = run.config
    sim = simulate_bankroll(run.signals, config)
    metrics = compute_metrics(run, sim, signal_limit=10)

    agg = metrics.aggregate
    assert agg.n_signals == len(run.signals)
    assert agg.n_wins + agg.n_losses == agg.n_settled
    assert 0.0 <= agg.hit_rate <= 1.0
    assert 0.0 <= agg.brier <= 1.0
    assert agg.logloss > 0.0
    # soma dos bins de calibracao == sinais liquidaveis
    assert sum(b.n for b in metrics.calibration) == agg.n_settled
    assert sum(b.n for b in metrics.ev_buckets) == agg.n_settled
    assert all(0.0 <= b.observed_rate <= 1.0 for b in metrics.calibration)
    assert len(metrics.signals) == 10


def test_calibration_bins_are_ordered_and_bounded(standard_run):
    bins = calibration_bins(standard_run.signals)
    assert bins, "sem bins de calibracao"
    assert [b.lower for b in bins] == sorted(b.lower for b in bins)
    for b in bins:
        assert b.lower <= b.avg_predicted <= b.upper
        assert b.ci_low <= b.observed_rate <= b.ci_high


def test_ev_buckets_partition_all_signals(standard_run):
    run = standard_run
    buckets = ev_buckets(run.signals)
    assert sum(b.n for b in buckets) == sum(
        1 for s in run.signals if s.settled and s.outcome_result in ("win", "loss")
    )


def test_segments_cover_all_dimensions(standard_run):
    rows = segment_rows(standard_run.signals)
    dims = {r.dimension for r in rows}
    for expected in (
        "competicao", "temporada", "mercado", "confianca",
        "faixa_odd", "faixa_probabilidade", "faixa_ev", "mes",
    ):
        assert expected in dims, f"dimensao ausente: {expected}"


def test_small_sample_is_flagged():
    """N pequeno precisa ser marcado, nao tratado como conclusao."""
    config = BacktestConfig(min_history=370, min_ev=0.02,
                            market_keys=("1x2",))
    corpus = HistoricalCorpus(build_dataset().history)
    run = run_backtest(corpus, config)
    if not run.signals:
        pytest.skip("poucos sinais para o teste")
    agg = aggregate_metrics(run.signals)
    if agg.n_settled < MIN_SAMPLE:
        assert not agg.sample_sufficient


# ==========================================================================
# 4. Filtros e parametros
# ==========================================================================


def test_min_ev_filter_is_respected(local_corpus):
    config = BacktestConfig(min_history=200, min_ev=0.08)
    run = run_backtest(local_corpus, config)
    assert run.signals
    assert all(s.signal.ev >= 0.08 for s in run.signals)


def test_market_selection_limits_signals(local_corpus):
    only_1x2 = run_backtest(
        local_corpus,
        BacktestConfig(min_history=200, min_ev=0.02, market_keys=("1x2",)),
    )
    several = run_backtest(
        local_corpus,
        BacktestConfig(min_history=200, min_ev=0.02, market_keys=("1x2", "ou", "btts")),
    )
    assert {s.signal.market for s in only_1x2.signals} == {"Resultado Final (1X2)"}
    assert len(several.signals) > len(only_1x2.signals)


def test_unknown_market_key_raises(local_corpus):
    with pytest.raises(ValueError, match="mercado"):
        run_backtest(
            local_corpus,
            BacktestConfig(min_history=200, market_keys=("mercado_fake",)),
        )


def test_min_confidence_filter(local_corpus):
    fraca = run_backtest(
        local_corpus, BacktestConfig(min_history=200, min_ev=0.02, min_confidence="FRACA"),
    )
    forte = run_backtest(
        local_corpus, BacktestConfig(min_history=200, min_ev=0.02, min_confidence="FORTE"),
    )
    assert all(s.signal.confidence == "FORTE" for s in forte.signals)
    assert len(forte.signals) <= len(fraca.signals)


def test_period_filter_limits_matches(local_corpus):
    config = BacktestConfig(min_history=120, start_date="2025-09-01", end_date="2025-09-30")
    run = run_backtest(local_corpus, config)
    assert run.n_matches_in_period < len(local_corpus.matches)
    assert all("2025-09-01" <= s.signal.kickoff[:10] <= "2025-09-30" for s in run.signals)


def test_competition_filter(local_corpus):
    run = run_backtest(local_corpus, BacktestConfig(min_history=120, competitions=["Serie A"]))
    assert run.n_matches_evaluated > 0
    vazio = run_backtest(
        local_corpus, BacktestConfig(min_history=120, competitions=["Liga Inexistente"]),
    )
    assert vazio.n_matches_in_period == 0
    assert vazio.signals == []


def test_min_history_skips_early_matches(local_corpus):
    run = run_backtest(local_corpus, BacktestConfig(min_history=300, min_ev=0.02))
    assert run.n_matches_skipped > 0
    assert run.skipped_reasons.get("historico_insuficiente", 0) > 0
    assert all(s.signal.n_prior_matches >= 300 for s in run.signals)


def test_invalid_config_is_rejected():
    with pytest.raises(ValueError):
        BacktestConfig(bankroll=0)
    with pytest.raises(ValueError):
        BacktestConfig(kelly_fraction=0)
    with pytest.raises(ValueError):
        BacktestConfig(min_confidence="FORTEZA")
    with pytest.raises(ValueError):
        BacktestConfig(start_date="2025-12-01", end_date="2025-01-01")


def test_config_fingerprint_is_stable_and_sensitive():
    a = BacktestConfig()
    b = BacktestConfig()
    c = BacktestConfig(min_ev=0.05)
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()


# ==========================================================================
# 5. Ordenacao cronologica e reuso do motor
# ==========================================================================


def test_signals_are_chronological(standard_run):
    kickoffs = [s.signal.kickoff for s in standard_run.signals]
    assert kickoffs == sorted(kickoffs)


def test_prior_matches_grow_monotonically(standard_run):
    """O contexto so cresce: nunca "encolhe" nem pula."""
    run = standard_run
    por_jogo: dict[str, int] = {}
    for s in run.signals:
        key = f"{s.signal.kickoff}|{s.signal.home}|{s.signal.away}"
        por_jogo[key] = s.signal.n_prior_matches
    sequencia = [por_jogo[k] for k in sorted(por_jogo)]
    assert sequencia == sorted(sequencia)


def test_backtest_reuses_scanner_engine(local_corpus):
    """Paridade: o sinal congelado bate com o motor do Scanner chamado a mao."""
    from betgsn.data import LEAGUE_HOME_ADVANTAGE
    from betgsn.pipeline import analyze_fixture

    config = BacktestConfig(min_history=200, min_ev=0.02, market_keys=("1x2",))
    run = run_backtest(local_corpus, config)
    assert run.signals

    target = run.signals[0].signal
    prior = local_corpus.matches_before(target.kickoff)
    teams = sorted({m.home for m in local_corpus.matches} | {m.away for m in local_corpus.matches})
    ratings = fit_ratings(prior, teams, home_advantage=LEAGUE_HOME_ADVANTAGE)
    match = next(
        m for m in local_corpus.matches
        if m.kickoff == target.kickoff and m.home == target.home and m.away == target.away
    )
    from betgsn.backtest_data import build_fixture

    analysis = analyze_fixture(
        build_fixture(match),
        ratings[match.home],
        ratings[match.away],
        league_goals_before(prior),
        LEAGUE_HOME_ADVANTAGE,
        attack_blend=config.attack_blend,
        market_keys=config.market_keys,
    )
    assert target.lambda_home == pytest.approx(analysis.lambdas[0])
    assert target.lambda_away == pytest.approx(analysis.lambdas[1])
    assert target.home_attack == pytest.approx(analysis.ratings_home.attack)
    assert target.league_goals == pytest.approx(league_goals_before(prior))


def test_odds_source_is_deterministic(local_corpus):
    """Mesmo corpus + mesma config -> odds identicas (seed por partida)."""
    config = BacktestConfig(min_history=200, min_ev=0.02, market_keys=("1x2",))
    a = run_backtest(local_corpus, config)
    b = run_backtest(local_corpus, config)
    assert [s.signal for s in a.signals] == [s.signal for s in b.signals]


def test_match_seed_is_stable_and_distinct():
    m1 = _match("2025-05-01", "Alpha", "Beta", 2, 1)
    m2 = _match("2025-05-01", "Alpha", "Beta", 2, 1)
    m3 = _match("2025-05-02", "Alpha", "Beta", 2, 1)
    assert match_seed(1, m1) == match_seed(1, m2)
    assert match_seed(1, m1) != match_seed(1, m3)
    assert match_seed(1, m1) != match_seed(2, m1)


def test_naive_market_ignores_team_strength(abc_corpus):
    """O mercado ingenuo usa so medias da liga — nao conhece forca de time."""
    prior_a = abc_corpus.matches_before("2025-05-05 16:00")
    prior_b = abc_corpus.matches_before("2025-05-10 16:00")
    src = NaiveSyntheticOddsSource(base_seed=7)
    m = abc_corpus.matches[-1]
    odds_a = src.odds_for(m, build_context(prior_a, m.kickoff, ("1x2",)).reference_probs)
    odds_b = src.odds_for(m, build_context(prior_b, m.kickoff, ("1x2",)).reference_probs)
    assert odds_a and odds_b
    # contextos diferentes -> odds diferentes, mas ambos sao 1X2 multi-casa
    assert set(odds_a) == {"Resultado Final (1X2)"}
    assert len(odds_a["Resultado Final (1X2)"]) >= 5


def test_real_odds_source_fails_loudly_without_cache(local_corpus, tmp_path):
    """Sem odds reais importadas o backtest nao roda: falha explicita.

    O source NUNCA cai para o mercado sintetico em silencio, porque isso
    misturaria duas hipoteses diferentes no mesmo resultado.
    """
    from betgsn.backtest_data import RealHistoricalOddsSource
    from betgsn.backtest_sources import OddsHistoryCache
    from betgsn.providers import ProviderError

    src = RealHistoricalOddsSource(cache=OddsHistoryCache(tmp_path / "vazio"))
    with pytest.raises(ProviderError, match="Sem odds historicas reais"):
        src.odds_for(local_corpus.matches[0], {})
    assert src.last_as_of is None
    assert src.misses


# ==========================================================================
# 6. Simulacao de banca
# ==========================================================================


def test_simulation_accounting_is_consistent(local_corpus):
    config = BacktestConfig(min_history=200, min_ev=0.05, market_keys=("1x2", "ou"))
    run = run_backtest(local_corpus, config)
    sim = simulate_bankroll(run.signals, config)
    assert sim.n_bets == sim.n_wins + sim.n_losses + sim.n_pushes
    assert sim.final_bankroll == pytest.approx(
        config.bankroll + sim.profit, abs=0.02
    )
    assert sim.max_drawdown >= 0.0
    assert len(sim.equity) > 0
    assert sim.equity[-1].bankroll == pytest.approx(sim.final_bankroll, abs=0.02)


def test_simulation_respects_stake_cap(local_corpus):
    config = BacktestConfig(min_history=200, min_ev=0.02, stake_cap=0.01,
                            max_exposure=0.25)
    run = run_backtest(local_corpus, config)
    sim = simulate_bankroll(run.signals, config)
    # o teto de exposicao e proporcional a banca VIGENTE (mesma semantica do
    # Scanner), nao a banca inicial
    anterior = config.bankroll
    for point in sim.equity:
        assert point.staked <= anterior * config.max_exposure + 1.0, (
            f"{point.day}: apostou {point.staked} com banca {anterior}"
        )
        anterior = point.bankroll


def test_simulation_is_deterministic(local_corpus):
    config = BacktestConfig(min_history=200, min_ev=0.05, market_keys=("1x2",))
    run = run_backtest(local_corpus, config)
    a = simulate_bankroll(run.signals, config)
    b = simulate_bankroll(run.signals, config)
    assert a == b


# ==========================================================================
# 7. Persistencia e comparacao
# ==========================================================================


@pytest.fixture
def store(tmp_path) -> BacktestStore:
    return BacktestStore(tmp_path / "bt.db")


def test_store_roundtrip(local_corpus, store):
    config = BacktestConfig(min_history=250, min_ev=0.05, market_keys=("1x2",))
    run = run_backtest(local_corpus, config)
    sim = simulate_bankroll(run.signals, config)
    metrics = compute_metrics(run, sim)
    store.save_run(run, metrics, simulation_to_json(sim))

    assert store.count_runs() == 1
    detail = store.get_run(run.run_id)
    assert detail is not None
    assert detail["config_hash"] == config.fingerprint()
    assert detail["n_signals"] == len(run.signals)

    saved = store.get_metrics(run.run_id)
    assert saved["aggregate"]["n_signals"] == metrics.aggregate.n_signals
    assert len(saved["calibration"]) == len(metrics.calibration)

    page = store.get_signals(run.run_id, limit=10)
    assert page["total"] == len(run.signals)
    assert len(page["items"]) == 10
    item = page["items"][0]
    for field in ("kickoff", "home", "away", "market", "model_prob", "ev",
                  "n_prior_matches", "odds_source", "odds_as_of", "config_hash"):
        assert field in item


def test_store_search_and_filters(local_corpus, store):
    config = BacktestConfig(min_history=250, min_ev=0.05, market_keys=("1x2",))
    run = run_backtest(local_corpus, config)
    sim = simulate_bankroll(run.signals, config)
    store.save_run(run, compute_metrics(run, sim), simulation_to_json(sim))

    por_mercado = store.get_signals(run.run_id, market="Resultado Final (1X2)")
    assert por_mercado["total"] > 0
    assert all(i["market"] == "Resultado Final (1X2)" for i in por_mercado["items"])

    vazio = store.get_signals(run.run_id, search="zzzznaoexiste")
    assert vazio["total"] == 0


def test_store_delete_run(local_corpus, store):
    config = BacktestConfig(min_history=300, min_ev=0.05, market_keys=("1x2",))
    run = run_backtest(local_corpus, config)
    sim = simulate_bankroll(run.signals, config)
    store.save_run(run, compute_metrics(run, sim), simulation_to_json(sim))
    assert store.delete_run(run.run_id) is True
    assert store.get_run(run.run_id) is None
    assert store.count_runs() == 0


def test_compare_runs_detects_difference(local_corpus, store):
    base = BacktestConfig(min_history=250, min_ev=0.05, market_keys=("1x2",))
    other = BacktestConfig(min_history=250, min_ev=0.10, market_keys=("1x2",))
    ra = run_backtest(local_corpus, base)
    rb = run_backtest(local_corpus, other)
    sa = simulate_bankroll(ra.signals, base)
    sb = simulate_bankroll(rb.signals, other)
    store.save_run(ra, compute_metrics(ra, sa), simulation_to_json(sa))
    store.save_run(rb, compute_metrics(rb, sb), simulation_to_json(sb))

    cmp = compare_runs(store, ra.run_id, rb.run_id)
    assert cmp["same_config"] is False
    assert cmp["metrics"]["n_signals"]["delta"] != 0
    assert cmp["metrics"]["brier"]["a"] > 0


def test_compare_same_config_flags_identical(local_corpus, store):
    config = BacktestConfig(min_history=300, min_ev=0.05, market_keys=("1x2",))
    run = run_backtest(local_corpus, config)
    sim = simulate_bankroll(run.signals, config)
    store.save_run(run, compute_metrics(run, sim), simulation_to_json(sim))
    cmp = compare_runs(store, run.run_id, run.run_id)
    assert cmp["same_config"] is True
    assert cmp["metrics"]["brier"]["delta"] == pytest.approx(0.0)


# ==========================================================================
# 8. Casos sem dados
# ==========================================================================


def test_run_with_no_matches_in_period(local_corpus):
    run = run_backtest(
        local_corpus,
        BacktestConfig(min_history=100, start_date="2030-01-01", end_date="2030-12-31"),
    )
    assert run.n_matches_in_period == 0
    assert run.signals == []
    sim = simulate_bankroll(run.signals, BacktestConfig())
    assert sim.final_bankroll == sim.initial_bankroll
    assert sim.n_bets == 0
    metrics = compute_metrics(run, sim)
    assert metrics.aggregate.n_signals == 0
    assert metrics.calibration == []
    assert metrics.ev_buckets == []


def test_metrics_on_empty_signal_list():
    agg = aggregate_metrics([])
    assert agg.n_signals == 0
    assert agg.hit_rate == 0.0
    assert agg.brier == 0.0
    assert agg.hit_rate_ci == (0.0, 0.0)


def test_league_average_rating_handles_empty():
    r = league_average_rating([])
    assert r.attack == 1.0
    assert r.defense == 1.0
    assert league_goals_before([]) == 0.0


# ==========================================================================
# 9. EQUIVALENCIA DO AJUSTE INDEXADO
# ==========================================================================


def _fit_ratings_naive(matches, teams, home_advantage=1.18):
    """Implementacao de referencia, O(times x jogos), sem indice.

    Existe apenas para provar que a versao indexada de `fit_ratings`
    produz EXATAMENTE o mesmo resultado. Se divergir, a otimizacao
    alterou o modelo.
    """
    from betgsn.model import _clip, _normalize, _wmean

    matches = list(matches)
    teams = list(teams)
    league_home = _wmean([m.home_goals for m in matches], [m.weight for m in matches])
    league_away = _wmean([m.away_goals for m in matches], [m.weight for m in matches])
    league_goals = max(0.6, (league_home + league_away) / 2.0)

    attack = {t: 1.0 for t in teams}
    defense = {t: 1.0 for t in teams}

    for _ in range(6):
        new_attack, new_defense = {}, {}
        for t in teams:
            num, den = 0.0, 0.0
            for m in matches:
                if m.home == t:
                    num += m.weight * (m.home_xg if m.home_xg is not None else m.home_goals)
                    den += m.weight * (defense[m.away] * home_advantage)
                elif m.away == t:
                    num += m.weight * (m.away_xg if m.away_xg is not None else m.away_goals)
                    den += m.weight * defense[m.home]
            new_attack[t] = _clip(num / den / league_goals, 0.45, 2.2) if den > 0 else 1.0

            num, den = 0.0, 0.0
            for m in matches:
                if m.home == t:
                    num += m.weight * (m.away_xg if m.away_xg is not None else m.away_goals)
                    den += m.weight * attack[m.away]
                elif m.away == t:
                    num += m.weight * (m.home_xg if m.home_xg is not None else m.home_goals)
                    den += m.weight * (attack[m.home] * home_advantage)
            new_defense[t] = _clip(num / den / league_goals, 0.45, 2.2) if den > 0 else 1.0

        attack, defense = _normalize(new_attack), _normalize(new_defense)
    return attack, defense


def test_indexed_fit_matches_naive_bit_for_bit():
    """A otimizacao do fit nao pode mudar nenhum numero.

    Compara attack/defense com igualdade EXATA (nao approx): a versao
    indexada preserva a ordem das somas, entao os floats sao identicos.
    """
    ds = build_dataset()
    ratings = fit_ratings(ds.history, ds.teams, home_advantage=ds.home_advantage)
    naive_attack, naive_defense = _fit_ratings_naive(
        ds.history, ds.teams, home_advantage=ds.home_advantage
    )
    for t in ds.teams:
        assert ratings[t].attack == naive_attack[t], f"attack divergiu em {t}"
        assert ratings[t].defense == naive_defense[t], f"defense divergiu em {t}"


def test_indexed_fit_is_fast_on_large_history():
    """Historico grande nao pode tornar o fit proibitivo."""
    import time

    ds = build_dataset()
    big = ds.history * 8          # ~3000 partidas
    t0 = time.perf_counter()
    fit_ratings(big, ds.teams, home_advantage=ds.home_advantage)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"fit lento demais: {elapsed:.2f}s"
