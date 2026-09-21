from betgsn.api.service import service
from betgsn.features.xg import visible_xg
from betgsn.model import HistoricalMatch
from betgsn.backtest_sources import apply_statistics


def test_production_service_is_real():
    assert service.source == "real"


def test_unknown_stats_identity_is_not_inferred_from_ids():
    m=HistoricalMatch("A","B",1,0)
    assert apply_statistics(m,[{"team":{"id":1}}, {"team":{"id":2}}]) == m


def test_xg_published_after_prediction_is_hidden():
    m=HistoricalMatch("A","B",1,0,home_xg=9,away_xg=8, xg_status="REAL",xg_source="provider",xg_available_at="2025-02-01")
    assert visible_xg(m,"2025-01-01").home_xg is None
