"""Adaptação de contratos antigos para o único motor walk-forward."""
from .backtest_data import HistoricalCorpus
from .backtest_engine import BacktestConfig, run_backtest, simulate_bankroll
from .markets import LABEL_TO_KEY


def run_legacy(dataset, split=.7, bankroll=1000., min_ev=.03, kelly_frac=.25,
               markets=("Resultado Final (1X2)", "Total de Gols", "Ambas Marcam"),
               odds_source="naive_synthetic"):
    from .backtest import BacktestResult, Betting, BetRecord, calibrate_1x2, outcome_1x2, _summary
    corpus = HistoricalCorpus(dataset.history)
    cut = int(len(corpus.matches) * split)
    if cut < 10 or len(corpus.matches) - cut < 10:
        raise ValueError("split precisa >= 10 partidas em treino/teste")
    start = corpus.utc_key_of(corpus.matches[cut])[:10]
    config = BacktestConfig(start_date=start, min_history=cut,
                           market_keys=tuple(LABEL_TO_KEY[m] for m in markets),
                           bankroll=bankroll, min_ev=min_ev, kelly_fraction=kelly_frac,
                           odds_source=odds_source)
    run = run_backtest(corpus, config)
    sim = simulate_bankroll(run.signals, config)
    rows = [r for r in run.predictions if "Resultado Final (1X2)" in r["prediction"]["markets"]]
    cal = calibrate_1x2([r["prediction"]["markets"]["Resultado Final (1X2)"] for r in rows],
                        [outcome_1x2(r["home_goals"], r["away_goals"]) for r in rows])
    ss = run.signals
    betting = Betting(bankroll, sim.final_bankroll, sim.n_bets, sim.n_wins,
                      sim.n_wins / sim.n_bets if sim.n_bets else 0, sim.total_staked,
                      sim.profit, sim.roi,
                      sum(s.signal.ev for s in ss) / len(ss) if ss else 0,
                      sum(s.realized_return or 0 for s in ss) / len(ss) if ss else 0,
                      sim.max_drawdown,
                      [BetRecord(f"{s.signal.home} vs {s.signal.away}", s.signal.outcome,
                                 s.signal.market, s.signal.model_prob, s.signal.best_odd,
                                 s.signal.best_book, s.signal.ev, s.signal.stake, s.won,
                                 s.profit or 0) for s in ss])
    summary = _summary(cut, len(rows), cal, betting, min_ev).replace("split temporal", "walk-forward")
    return BacktestResult(split, cut, len(rows), cal, betting, summary)
