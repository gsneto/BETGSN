from dataclasses import replace

from betgsn.odds_snapshots import OddsObservation, OddsSnapshotStore


def setup(store):
    quote = OddsObservation("event", "1x2", "1", "book", 2.2,
                            "2020-01-01T08:00:00Z", "2020-01-01T12:00:00Z")
    store.add([quote])
    store.register_entry("event", "1x2", "1", 2.2, quote.timestamp, 1,
                         quote.kickoff, "2020-01-01T11:00:00Z")
    return quote, store.clv_entries()[0]


def test_close_cannot_precede_decision(tmp_path):
    store = OddsSnapshotStore(tmp_path / "s.db")
    q, entry = setup(store)
    store.add([replace(q, timestamp="2020-01-01T10:30:00Z", odd=2.0)])
    lc = store.clv_lifecycle(entry, now="2020-01-01T13:00:00Z")
    assert lc.state != "CLOSED"
    assert lc.result is None or lc.result.clv_percentage is None


def test_close_is_not_final_before_kickoff(tmp_path):
    store = OddsSnapshotStore(tmp_path / "s.db")
    q, entry = setup(store)
    store.add([replace(q, timestamp="2020-01-01T11:30:00Z", odd=2.0)])
    lc = store.clv_lifecycle(entry, now="2020-01-01T11:45:00Z")
    assert lc.state == "PENDING"
    assert lc.result is None or lc.result.clv_percentage is None


def test_wrong_kickoff_under_same_key_cannot_close(tmp_path):
    store = OddsSnapshotStore(tmp_path / "s.db")
    q, entry = setup(store)
    store.add([replace(q, timestamp="2020-01-02T11:30:00Z",
                       kickoff="2020-01-02T12:00:00Z", odd=2.0)])
    lc = store.clv_lifecycle(entry, now="2020-01-03T13:00:00Z")
    assert lc.state != "CLOSED"


def test_all_closing_constituents_are_after_decision(tmp_path):
    store = OddsSnapshotStore(tmp_path / "s.db")
    q, entry = setup(store)
    store.add([replace(q, timestamp="2020-01-01T10:30:00Z", odd=9.0),
               replace(q, bookmaker="other", timestamp="2020-01-01T11:30:00Z", odd=2.0)])
    lc = store.clv_lifecycle(entry, now="2020-01-01T13:00:00Z")
    assert lc.state == "CLOSED"
    assert lc.result.closing_odd == 2.0
    assert lc.result.n_books_closing == 1
    provenance = store.clv_provenance(entry)
    assert provenance["execution_price"] is None
    assert provenance["execution_status"] == "UNKNOWN"
    assert provenance["closing_candidates"][0]["bookmaker"] == "other"
    assert len(provenance["closing_candidates"]) == 1


def test_original_bookmaker_must_exist(tmp_path):
    store = OddsSnapshotStore(tmp_path / "s.db")
    q, entry = setup(store)
    store.add([replace(q, timestamp="2020-01-01T11:30:00Z", odd=2.0)])
    entry = replace(entry, entry_bookmaker="unobserved")
    assert store.clv_lifecycle(entry, now="2020-01-01T13:00:00Z").state == "MISMATCH"
