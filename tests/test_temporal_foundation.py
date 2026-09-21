from dataclasses import replace
from betgsn.model import HistoricalMatch
from betgsn.backtest_data import HistoricalCorpus
from betgsn.backtest_engine import BacktestConfig, run_backtest
from betgsn.data import build_dataset
from betgsn.real_signals import _fit_on_real_history
from betgsn.football_data_uk import CsvMatch


def test_unfinished_or_unpublished_result_excluded():
    m = HistoricalMatch("A", "B", 9, 0, kickoff="2025-01-01T12:00:00Z",
                        result_available_at="2025-01-01T14:00:00Z")
    c = HistoricalCorpus([m])
    assert c.available_before("2025-01-01T13:00:00Z") == []
    assert c.available_before("2025-01-01T14:00:00Z") == []
    assert c.available_before("2025-01-01T14:00:01Z") == [m]


def test_future_team_universe_cannot_change_prediction():
    history = build_dataset().history
    config = BacktestConfig(min_history=300, market_keys=("1x2",))
    base = run_backtest(HistoricalCorpus(history), config)
    future = HistoricalMatch("FUTURE_A", "FUTURE_B", 99, 0, kickoff="2030-01-01")
    altered = run_backtest(HistoricalCorpus(history + [future]), config)
    assert base.signals
    assert [s.signal for s in base.signals] == [s.signal for s in altered.signals[:len(base.signals)]]


def test_real_fit_excludes_future_and_is_sensitive_to_past(monkeypatch):
    import betgsn.real_signals as mod
    monkeypatch.setattr(mod, "MIN_HISTORY", 1)
    past = CsvMatch("E0", "EPL", "2024", "2024-01-01", "12:00", "UTC", "A", "B", 1, 0)
    future = replace(past, date="2026-01-01", home_goals=90)
    class Client:
        def load_matches(self):
            return self.rows
    c = Client()
    c.rows = [past]
    expected = _fit_on_real_history(c, cutoff="2025-01-01")
    c.rows = [past, future]
    assert _fit_on_real_history(c, cutoff="2025-01-01") == expected
    c.rows = [replace(past, home_goals=5), future]
    assert _fit_on_real_history(c, cutoff="2025-01-01") != expected
