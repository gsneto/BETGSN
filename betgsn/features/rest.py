from ..timeutil import parse_kickoff


def rest(entries, cutoff):
    ages = [(parse_kickoff(cutoff) - parse_kickoff(m.kickoff, m.timezone)).total_seconds() / 86400 for m in entries]
    if any(a <= 0 for a in ages):
        raise ValueError("descanso recebeu informação futura")
    n7, n14 = sum(a <= 7 for a in ages), sum(a <= 14 for a in ages)
    return {"days_since_last": min(ages) if ages else None,
            "matches_7d": n7, "matches_14d": n14, "congestion": n7 / 7}
