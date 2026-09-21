"""Força adversária medida ANTES de cada confronto, nunca pelo rating final."""


def strength(entries, team, pre_ratings, window=10):
    opponents, attack, defense, points = [], [], [], []
    for m in entries[-window:]:
        h, a = pre_ratings[id(m)]
        home = m.home == team
        opponent = a if home else h
        factor = 10 ** ((opponent - 1500) / 400)
        gf, ga = (m.home_goals, m.away_goals) if home else (m.away_goals, m.home_goals)
        opponents.append(opponent)
        attack.append(gf * factor)
        defense.append(ga / factor)
        points.append((3 if gf > ga else 1 if gf == ga else 0) * factor)
    avg = lambda vs: sum(vs) / len(vs) if vs else None
    return {"opponent_elo": avg(opponents), "attack_adjusted": avg(attack),
            "defense_adjusted": avg(defense), "performance_adjusted": avg(points)}
