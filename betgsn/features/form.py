"""Forma temporal; ausência de estatística não vira zero."""
from math import exp, log
from ..timeutil import parse_kickoff


def form(entries, team, cutoff, window=10, venue=None, half_life_days=90):
    if half_life_days <= 0 or window < 1:
        raise ValueError("janela e meia-vida devem ser positivas")
    chosen = [m for m in entries if venue is None or (m.home == team) == (venue == "home")][-window:]
    sums, weights = {}, {}
    for m in chosen:
        home = m.home == team
        side, opp = ("home", "away") if home else ("away", "home")
        gf, ga = getattr(m, side + "_goals"), getattr(m, opp + "_goals")
        age = (parse_kickoff(cutoff) - parse_kickoff(m.kickoff, m.timezone)).total_seconds() / 86400
        if age <= 0:
            raise ValueError("forma recebeu informação futura")
        w = exp(-log(2) * age / half_life_days)
        vals = {"points": 3 if gf > ga else 1 if gf == ga else 0, "goals_for": gf, "goals_against": ga}
        for stat in ("shots", "shots_on_target", "corners", "cards"):
            vals[stat] = getattr(m, side + "_" + stat)
        # Somente observação real, já disponibilizada, entra como feature xG.
        vals["xg"] = getattr(m, side + "_xg") if m.xg_status == "REAL" and m.xg_source else None
        for name, value in vals.items():
            if value is not None:
                sums[name] = sums.get(name, 0) + w * value
                weights[name] = weights.get(name, 0) + w
    names = ("points", "goals_for", "goals_against", "shots", "shots_on_target", "corners", "cards", "xg")
    return {"n": len(chosen), **{k: sums[k] / weights[k] if weights.get(k) else None for k in names}}
