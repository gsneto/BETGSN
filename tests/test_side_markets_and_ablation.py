"""Testes dos benchmarks de corners/cards e da ablação de features.

Foco na integridade temporal (point-in-time), na matemática de
overdispersion e na estrutura dos relatórios. Os benchmarks completos rodam
separadamente (custam minutos); aqui validamos a lógica em amostras pequenas.
"""
from __future__ import annotations

import pytest

from betgsn.side_markets_benchmark import (
    _rolling,
    _prior_available,
    _team_history_add,
    build_rows,
    dispersion_stats,
    to_matrix,
)
from betgsn.ablation import group_members, GROUPS, UNAVAILABLE_GROUPS
from betgsn.features.elo import Elo


# --------------------------------------------------------------------------
# Rolling point-in-time
# --------------------------------------------------------------------------

class _M:
    def __init__(self, home, away, hc=None, ac=None, hy=None, ay=None,
                 kickoff="2025-01-01 16:00", timezone="Europe/London",
                 division="E0", season="2425", referee="", date="2025-01-01"):
        self.home, self.away = home, away
        self.home_corners, self.away_corners = hc, ac
        self.home_cards, self.away_cards = hy, ay
        self.home_goals, self.away_goals = 1, 0
        self.kickoff, self.timezone = kickoff, timezone
        self.division, self.season = division, season
        self.referee = referee
        self.date = date
        self.result_available_at = None
        self.home_shots = self.away_shots = None
        self.home_shots_on_target = self.away_shots_on_target = None

    def to_historical(self):
        return self


def test_rolling_averages_only_prior_matches():
    prior = [_M("A", "B", hc=5, ac=4), _M("A", "C", hc=7, ac=6)]
    # A como mandante teve 5 e 7 cantos a favor.
    assert _rolling(prior, "A", "corners", None, "for") == pytest.approx(6.0)


def test_rolling_ignores_none_values():
    prior = [_M("A", "B", hc=5), _M("A", "C", hc=None)]
    assert _rolling(prior, "A", "corners", None, "for") == pytest.approx(5.0)


def test_rolling_empty_returns_none():
    assert _rolling([], "A", "corners", None, "for") is None


def test_rolling_against_uses_opponent_stat():
    prior = [_M("A", "B", hc=5, ac=4)]
    # cantos contra do A = cantos a favor do B = 4
    assert _rolling(prior, "A", "corners", None, "against") == pytest.approx(4.0)


def test_window_limits_to_last_n():
    prior = [_M("A", f"B{i}", hc=1) for i in range(10)]
    assert _rolling(prior, "A", "corners", 5, "for") == pytest.approx(1.0)


def test_prior_available_respects_embargo():
    history: dict[str, list] = {"A": []}
    avail: dict[str, list] = {"A": []}
    _team_history_add(history, avail, "A", _M("A", "B"), "2025-01-03T00:00:00Z")
    _team_history_add(history, avail, "A", _M("A", "C"), "2025-01-05T00:00:00Z")
    # cutoff em 04/01: só a partida com disponibilidade 03/01 entra
    assert len(_prior_available(history, avail, "A", "2025-01-04T00:00:00Z")) == 1
    # cutoff em 06/01: ambas
    assert len(_prior_available(history, avail, "A", "2025-01-06T00:00:00Z")) == 2


# --------------------------------------------------------------------------
# build_rows
# --------------------------------------------------------------------------

def test_build_rows_skips_missing_goals():
    m = _M("A", "B", hc=5, ac=4, hy=2, ay=1)
    m.home_goals = None
    rows = build_rows([m], ["E0"])
    assert rows == []


def test_build_rows_produces_expected_features():
    m = _M("A", "B", hc=5, ac=4, hy=2, ay=1, referee="M Oliver")
    rows = build_rows([m], ["E0"])
    assert len(rows) == 1
    f = rows[0]["features"]
    assert f["home_elo"] == 1500.0  # Elo inicial, sem histórico anterior
    assert rows[0]["total_corners"] == 9
    assert rows[0]["total_cards"] == 3
    assert rows[0]["referee"] == "M Oliver"


def test_build_rows_referee_feature_is_point_in_time():
    # O primeiro jogo não pode usar o próprio árbitro como feature.
    m = _M("A", "B", hc=5, ac=4, hy=2, ay=1, referee="M Oliver")
    rows = build_rows([m], ["E0"])
    assert rows[0]["features"]["referee_avg_cards"] is None


# --------------------------------------------------------------------------
# Dispersion
# --------------------------------------------------------------------------

def test_dispersion_ratio_math():
    rows = [
        {"division": "E0", "season": "2425", "total_corners": 8},
        {"division": "E0", "season": "2425", "total_corners": 10},
        {"division": "E0", "season": "2425", "total_corners": 12},
    ]
    rows = rows * 10  # 30 observações (limiar mínimo do relatório)
    stats = dispersion_stats(rows, "corners")
    assert len(stats) == 1
    # mean=10, var=8/3≈2.667, ratio≈0.267
    assert stats[0]["mean"] == pytest.approx(10.0)
    assert stats[0]["dispersion_ratio"] == pytest.approx(0.2667, abs=1e-3)


def test_dispersion_skips_small_samples():
    rows = [{"division": "E0", "season": "2425", "total_corners": 8}]
    assert dispersion_stats(rows, "corners") == []


# --------------------------------------------------------------------------
# Matriz de features
# --------------------------------------------------------------------------

def test_to_matrix_aligns_columns():
    rows = [
        {"features": {"a": 1.0, "b": 2.0}},
        {"features": {"a": 3.0, "b": 4.0}},
    ]
    x, cols = to_matrix(rows)
    assert cols == ["a", "b"]
    assert x.shape == (2, 2)
    assert x[0][1] == 2.0


def test_to_matrix_fills_missing_with_nan():
    rows = [
        {"features": {"a": 1.0, "b": None}},
        {"features": {"a": 3.0, "b": 4.0}},
    ]
    x, cols = to_matrix(rows, ["a", "b"])
    import numpy as np
    assert np.isnan(x[0][1])


# --------------------------------------------------------------------------
# Ablação: grupos de features
# --------------------------------------------------------------------------

def test_group_members_classify_correctly():
    cols = [
        "elo_difference", "home_elo", "home_elo_home",
        "home_form_5_overall_points", "home_opponent_elo",
        "home_attack_adjusted", "home_days_since_last", "h2h_n",
    ]
    assert group_members(cols, "Elo") == [0, 1, 2]
    assert group_members(cols, "Form") == [3]
    assert group_members(cols, "OpponentStrength") == [4, 5]
    assert group_members(cols, "Rest") == [6]
    assert group_members(cols, "H2H") == [7]


def test_group_members_does_not_confuse_opponent_elo_with_elo():
    # "home_opponent_elo" contém "_elo", mas deve ir para OpponentStrength.
    cols = ["home_elo", "home_opponent_elo"]
    assert group_members(cols, "Elo") == [0]
    assert group_members(cols, "OpponentStrength") == [1]


def test_groups_declared_available():
    assert set(GROUPS) == {"Elo", "Form", "OpponentStrength", "Rest", "H2H"}


def test_unavailable_groups_are_declared_not_tested():
    assert "xG" in UNAVAILABLE_GROUPS
    assert "OddsMovement" in UNAVAILABLE_GROUPS
