from .form import form


def h2h(entries, home, away, cutoff, minimum=3, window=10):
    prior = [m for m in entries if {m.home, m.away} == {home, away}][-window:]
    if len(prior) < minimum:
        return {"n": len(prior), "points": None, "total_goals": None}
    f = form(prior, home, cutoff, window=window, half_life_days=365)
    return {"n": len(prior), "points": f["points"], "total_goals": f["goals_for"] + f["goals_against"]}
