from ..model import build_score_matrix


def predict(lambdas):
    m = build_score_matrix(*lambdas, rho=0)
    return [m.prob_home_win(), m.prob_draw(), m.prob_away_win()]
