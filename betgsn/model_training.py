"""Ajuste auditável por liga com shrinkage e fontes disponíveis no TRAIN."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import timedelta

from .backtest_data import HistoricalCorpus
from .model import Fixture, build_score_matrix, fit_ratings
from .production_policy import finite_number
from .timeutil import parse_kickoff, utc_key

TRAINING_GRID = dict(shrinkage=(0,10,30), decay=(None,180,365), home=(False,True),
                    blend=((1,0,0,0),(.5,.5,0,0),(.5,.25,.25,0),(.5,.25,0,.25)))


def select_train_params(matches, *, train_start, train_end, enabled):
    """Seleção sequencial predefinida em três folds internos, sem ROI."""
    unknown = set(enabled)-set(TRAINING_GRID)
    if unknown:
        raise ValueError(f'unknown training components: {sorted(unknown)}')
    rows = HistoricalCorpus(matches).available_before(train_end) if matches else []
    rows = [r for r in rows if utc_key(r.kickoff,r.timezone)>=utc_key(train_start)]
    days = sorted({utc_key(r.kickoff,r.timezone)[:10] for r in rows})
    params = TrainingParams()
    if len(rows)<600 or len(days)<12:
        return params
    def score(candidate):
        losses = []
        for part in range(1,4):
            block_days = set(days[len(days)*part//4:len(days)*(part+1)//4])
            cutoff = (parse_kickoff(min(block_days))-timedelta(days=2)).isoformat()
            fit = [r for r in rows if utc_key(r.kickoff,r.timezone)<utc_key(cutoff)]
            if len(fit)<300:
                continue
            snapshot = fit_train_snapshot(rows,train_start=train_start,train_end=cutoff,params=candidate)
            for r in rows:
                if utc_key(r.kickoff,r.timezone)[:10] not in block_days:
                    continue
                matrix = snapshot.matrix(Fixture(r.home,r.away,r.league,r.kickoff))
                if matrix is None:
                    continue
                ps = (matrix.prob_home_win(),matrix.prob_draw(),matrix.prob_away_win())
                y = 0 if r.home_goals>r.away_goals else 1 if r.home_goals==r.away_goals else 2
                brier = sum((p-int(i==y))**2 for i,p in enumerate(ps))/3
                losses.append(brier-math.log(max(1e-6,ps[y])))
        return sum(losses)/len(losses) if losses else math.inf
    for component in ('shrinkage','decay','home','blend'):
        if component not in enabled:
            continue
        field = dict(shrinkage='shrinkage',decay='decay_days',home='league_home',blend='blend_weights')[component]
        candidates = [replace(params,**{field:v}) for v in TRAINING_GRID[component]]
        values = [score(p) for p in candidates]
        if any(math.isfinite(x) for x in values):
            params = candidates[min(range(len(candidates)),key=lambda i:values[i])]
    return params


@dataclass(frozen=True)
class TrainingParams:
    shrinkage: float = 0.
    decay_days: float | None = None
    league_home: bool = False
    blend_weights: tuple[float,float,float,float] = (.5,.5,0.,0.)

    def __post_init__(self):
        if not finite_number(self.shrinkage) or self.shrinkage < 0:
            raise ValueError('invalid shrinkage')
        if self.decay_days is not None and (not finite_number(self.decay_days) or self.decay_days <= 0):
            raise ValueError('invalid half life')
        if (len(self.blend_weights)!=4 or any(not finite_number(w) or w<0 for w in self.blend_weights)
                or not math.isclose(sum(self.blend_weights),1)):
            raise ValueError('invalid blend weights')


@dataclass(frozen=True)
class TrainedSnapshot:
    ratings: dict
    home_by_league: dict
    global_home: float
    league_goals: dict
    attacks: dict
    audit: dict
    fingerprint: str

    def matrix(self, fixture: Fixture):
        if utc_key(fixture.kickoff) <= utc_key(self.audit['train_end']):
            raise ValueError('prediction must follow TRAIN')
        hk, ak = (fixture.league,fixture.home),(fixture.league,fixture.away)
        if hk not in self.ratings or ak not in self.ratings:
            return None
        home = self.home_by_league[fixture.league]
        base = self.league_goals[fixture.league]/(1+home)
        h = self.attacks[hk]*self.ratings[ak].defense*base*home
        a = self.attacks[ak]*self.ratings[hk].defense*base
        return build_score_matrix(max(.15,min(5.5,h)),max(.15,min(5.5,a)))


def fit_train_snapshot(matches, *, train_start: str, train_end: str,
                       params: TrainingParams) -> TrainedSnapshot:
    if utc_key(train_start) >= utc_key(train_end):
        raise ValueError('invalid TRAIN interval')
    eligible = HistoricalCorpus(matches).available_before(train_end) if matches else []
    eligible = [m for m in eligible if utc_key(m.kickoff,m.timezone)>=utc_key(train_start)]
    prior = []
    for m in eligible:
        if not finite_number(m.weight) or m.weight <= 0:
            raise ValueError('invalid match weight')
        age = (parse_kickoff(train_end)-parse_kickoff(m.kickoff,m.timezone)).total_seconds()/86400
        weight = m.weight*(math.exp(-math.log(2)*age/params.decay_days) if params.decay_days else 1)
        real_xg = m.xg_status=='REAL' and m.xg_source and m.xg_available_at
        prior.append(replace(m,weight=weight,home_xg=m.home_xg if real_xg else None,
                             away_xg=m.away_xg if real_xg else None))
    if not prior:
        raise ValueError('insufficient TRAIN')
    gh = sum(m.weight*m.home_goals for m in prior)
    ga = sum(m.weight*m.away_goals for m in prior)
    global_home = max(.5,min(2.,gh/ga)) if ga>0 else 1.18
    ratings, home_by_league, goals, attacks, fallback, effective, sample = {},{},{},{},{},{},{}
    for league in sorted({m.league for m in prior}):
        rows = [m for m in prior if m.league==league]
        home = 1.18
        if params.league_home:
            home = global_home
            if len(rows)>=100:
                total_weight = sum(m.weight for m in rows)
                league_h = sum(m.weight*m.home_goals for m in rows)/total_weight
                league_a = sum(m.weight*m.away_goals for m in rows)/total_weight
                # 200-match global prior; ratio of shrunk goal intensities.
                global_a = ga/sum(m.weight for m in prior)
                home = (len(rows)*league_h+200*global_a*global_home)/(len(rows)*league_a+200*global_a) if global_a>0 else global_home
            else:
                fallback[league] = 'INSUFFICIENT_LEAGUE_SAMPLE'
        home_by_league[league] = home
        goals[league] = max(1.2,sum(m.weight*(m.home_goals+m.away_goals) for m in rows)/sum(m.weight for m in rows))
        teams = sorted({m.home for m in rows}|{m.away for m in rows})
        fitted = fit_ratings(rows,teams,home_advantage=home,use_xg=False)
        has_xg = any(m.home_xg is not None and m.away_xg is not None for m in rows)
        weights = list(params.blend_weights)
        if not has_xg:
            weights[3] = 0
        mass = sum(weights)
        weights = [w/mass for w in weights] if mass else [1,0,0,0]
        effective[league] = weights
        for team in teams:
            appearances = [(m,m.home==team) for m in rows if team in (m.home,m.away)]
            ws = [m.weight for m,_ in appearances]
            n_eff = sum(ws)**2/sum(w*w for w in ws)
            strength = n_eff/(n_eff+params.shrinkage)
            r = fitted[team]
            r = replace(r,attack=1+strength*(r.attack-1),defense=1+strength*(r.defense-1),
                        matches_played=len(appearances))
            ratings[(league,team)] = r
            base = goals[league]/2
            recent = appearances[-5:]
            form = sum(m.home_goals if h else m.away_goals for m,h in recent)/len(recent)/base
            xg_rows = [(m.home_xg if h else m.away_xg,m.weight) for m,h in appearances
                       if (m.home_xg if h else m.away_xg) is not None]
            team_weights = weights[:]
            if not xg_rows:
                team_weights[3]=0
                mass=sum(team_weights)
                team_weights=[w/mass for w in team_weights] if mass else [1,0,0,0]
            xg = sum(x*w for x,w in xg_rows)/sum(w for _,w in xg_rows)/base if xg_rows else 1
            components = (r.attack,1+strength*(r.goals_for/base-1),
                          1+strength*(form-1),1+strength*(xg-1))
            attacks[(league,team)] = max(.4,min(2.4,sum(w*x for w,x in zip(team_weights,components))))
            sample[f'{league}|{team}'] = {'n':len(appearances),'n_eff':n_eff,'weights':team_weights}
    audit = dict(train_start=train_start,train_end=train_end,params=asdict(params),
                 n_matches=len(prior),xg_matches=sum(m.home_xg is not None for m in prior),
                 home_fallback=fallback,effective_weights=effective,samples=sample)
    identity = {'audit':audit,'matches':[asdict(m) for m in prior]}
    fingerprint = hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return TrainedSnapshot(ratings,home_by_league,global_home,goals,attacks,audit,fingerprint)
