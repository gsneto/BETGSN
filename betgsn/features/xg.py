from dataclasses import replace
from ..timeutil import utc_key


def visible_xg(match, cutoff):
    """Publicação do xG pode ser posterior à publicação do resultado."""
    if (match.xg_status != "REAL" or not match.xg_source or not match.xg_available_at
            or utc_key(match.xg_available_at) >= utc_key(cutoff)):
        return replace(match, home_xg=None, away_xg=None, home_xg_against=None,
                       away_xg_against=None, xg_status="UNAVAILABLE")
    return match
