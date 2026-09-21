"""Benchmark temporal reproduzível: train -> early-stop -> ensemble -> calibration -> test.

Nunca promove modelos automaticamente. CSV financeiro é cenário exploratório:
preços reais sem timestamp de publicação, portanto CLV e execução estrita null.
"""
import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
import numpy as np
from .features import FeatureBuilder
from .model import Fixture, fit_ratings
from .pipeline import analyze_fixture
from .backtest_data import HistoricalCorpus
from .football_data_uk import FootballDataClient
from .timeutil import utc_key, parse_kickoff
from .temporal import result_time
from .models.base import TemporalBatch, matrix, OUTCOMES, MARKETS
from .models.xgboost_model import XGBoostModel
from .models.lightgbm_model import LightGBMModel
from .models.elo import EloModel
from .models.ensemble import WeightedEnsemble
from .models.calibration import TemporalCalibrator
from .models.poisson import predict as poisson
from .evaluation import score_predictions
from .signals import generate_signals, scale_stakes_to_cap
from .backtest_data import settle_outcome, MatchResult, profit_for


def financial_scenario(rows, probabilities, market):
    """Mesmos sinais/Kelly do scanner; odds CSV sem prova temporal, explicitamente rotulado."""
    bankroll = peak = 1000.
    total_stake = profit = max_dd = 0.
    n = wins = 0
    by_day = {}
    for row, p in zip(rows, probabilities):
        by_day.setdefault(row["time"][:10], []).append((row,p))
    for day, items in sorted(by_day.items()):
        pending = []
        for row,p in items:
            m = row["match"]
            fx = Fixture(m.home,m.away,m.league,row["time"])
            probs = {MARKETS[market]: dict(zip(OUTCOMES[market], map(float,p)))}
            signals = generate_signals(fx,probs,m.odds_opening,bankroll,stake_cap=.01)
            pending.extend((s,m) for s in signals)
        stakes,_ = scale_stakes_to_cap([s.stake for s,_ in pending],bankroll*.25)
        day_profit=0.
        for (s,m),stake in zip(pending,stakes):
            outcome=settle_outcome(s.market,s.outcome,MatchResult(m.home_goals,m.away_goals))
            if outcome is None or stake <= 0:
                continue
            n+=1
            wins+=outcome == "win"
            total_stake+=stake
            day_profit+=profit_for(outcome,s.best_odd,stake)
        bankroll+=day_profit
        peak=max(peak,bankroll)
        max_dd=max(max_dd,(peak-bankroll)/peak)
    profit=bankroll-1000
    return {"n_bets":n,"profit":profit,"roi":profit/1000,
            "yield":profit/total_stake if total_stake else None,
            "drawdown":max_dd,"hit_rate":wins/n if n else None,
            "clv":None,"odds_timestamp":None,
            "execution_status":"EXPLORATORY_CSV_UNTIMESTAMPED"}


def block_ci(values, dates, resamples=500):
    """Bootstrap por mês para preservar dependência intramês."""
    groups={}
    for value,day in zip(values,dates):
        groups.setdefault(day[:7],[]).append(float(value))
    blocks=list(groups.values())
    if len(blocks)<2:
        return None
    rng=np.random.default_rng(6767)
    means=[np.mean([v for i in rng.integers(0,len(blocks),len(blocks)) for v in blocks[i]]) for _ in range(resamples)]
    return list(map(float,np.quantile(means,[.025,.975])))


def run_benchmark(division="E0", start_year=2015, test_year=2025):
    raw=FootballDataClient().load_matches([division])
    raw=sorted([m for m in raw if start_year <= int(m.date[:4]) <= test_year],key=lambda m:utc_key(m.kickoff,m.timezone))
    history=[m.to_historical() for m in raw]
    corpus=HistoricalCorpus(history)
    builder=FeatureBuilder(history)
    rows=[]
    cached_day=None
    for i,(csv,m) in enumerate(zip(raw,history)):
        timestamp=utc_key(m.kickoff,m.timezone)
        fx=Fixture(m.home,m.away,m.league,timestamp)
        features=builder.build(fx)
        if timestamp[:10] != cached_day:
            prior=corpus.available_before(timestamp)
            lower=parse_kickoff(timestamp)-timedelta(days=1095)
            prior=[p for p in prior if parse_kickoff(p.kickoff,p.timezone)>=lower]
            teams=sorted({p.home for p in prior}|{p.away for p in prior})
            ratings=fit_ratings(prior,teams) if len(prior)>=300 else {}
            lg=sum(p.home_goals+p.away_goals for p in prior)/len(prior) if prior else 0
            cached_day=timestamp[:10]
        if m.home not in ratings or m.away not in ratings:
            continue
        a=analyze_fixture(fx,ratings[m.home],ratings[m.away],lg,1.18,attack_blend=0,market_keys=("1x2","ou","btts"))
        labels={"1x2":0 if m.home_goals>m.away_goals else 1 if m.home_goals==m.away_goals else 2,
                "ou":int(m.home_goals+m.away_goals>2.5),"btts":int(m.home_goals>0 and m.away_goals>0)}
        baseline={k:np.array([a.markets[MARKETS[k]][o] for o in OUTCOMES[k]]) for k in OUTCOMES}
        rows.append({"time":timestamp,"available":result_time(m),"features":features,
                     "labels":labels,"baseline":baseline,"poisson":poisson(a.lambdas),"match":csv})
        if i%500 == 0:
            print(f"features {i}/{len(raw)}",flush=True)
    # Quatro blocos anteriores ao ano de teste. Dois dias de embargo em cada fronteira.
    bounds=[f"{test_year-3}-01-01",f"{test_year-2}-01-01",f"{test_year-1}-01-01",f"{test_year}-01-01",f"{test_year+1}-01-01"]
    train=[r for r in rows if r["available"]<utc_key(bounds[0])]
    groups=[train]
    for lo,hi in zip(bounds,bounds[1:]):
        groups.append([r for r in rows if utc_key(lo)<=r["time"] and r["available"]<utc_key(hi)])
    if any(len(g)<60 for g in groups):
        raise ValueError(f"amostra insuficiente nos blocos temporais: {[len(g) for g in groups]}")
    xs=[]
    columns=None
    for g in groups:
        x,columns=matrix([r["features"] for r in g],columns)
        xs.append(x)
    # Elo probabilístico usa apenas diferença e ratings casa/fora.
    elo_cols=[columns.index(c) for c in ("elo_difference","home_elo_home","away_elo_away")]
    results={}
    predictions_out=[]
    for market in OUTCOMES:
        ys=[np.array([r["labels"][market] for r in g]) for g in groups]
        batches=[TemporalBatch(x,y,tuple(r["time"] for r in g)) for x,y,g in zip(xs,ys,groups)]
        models={"XGBoost":XGBoostModel().fit(batches[0],batches[1]),
                "LightGBM":LightGBMModel().fit(batches[0],batches[1])}
        predictions={i:{"BASELINE_V1":np.array([r["baseline"][market] for r in groups[i]])} for i in (2,3,4)}
        for name,model in models.items():
            for i in (2,3,4):
                predictions[i][name]=model.predict_proba(xs[i])
        if market == "1x2":
            elo=EloModel().fit(TemporalBatch(xs[0][:,elo_cols]/400,ys[0],batches[0].times))
            for i in (2,3,4):
                predictions[i]["Elo"]=elo.predict_proba(xs[i][:,elo_cols]/400)
                predictions[i]["Poisson"]=np.array([r["poisson"] for r in groups[i]])
        ensemble=WeightedEnsemble().fit(predictions[2],ys[2],batches[2].times,batches[1].times[-1])
        for i in (3,4):
            predictions[i]["Ensemble"]=ensemble.predict_proba(predictions[i],batches[i].times[0])
        result={}
        for name,p in predictions[4].items():
            variants={name:p}
            for method in ("platt","isotonic"):
                c=TemporalCalibrator(method).fit(predictions[3][name],ys[3],batches[3].times,batches[2].times[-1])
                variants[name+"_"+method]=c.predict_proba(p,batches[4].times[0])
            for variant,probs in variants.items():
                metrics=score_predictions(probs,ys[4])
                metrics.update(financial_scenario(groups[4],probs,market))
                loss=[-np.log(max(1e-12,p[y])) for p,y in zip(probs,ys[4])]
                metrics["logloss_ci95_month_block"]=block_ci(loss,batches[4].times)
                metrics["promotion"]="EXPERIMENTAL_NOT_PROMOTED"
                result[variant]=metrics
                for index,(r,prob) in enumerate(zip(groups[4],probs)):
                    predictions_out.append({"kickoff":r["time"],"prediction_timestamp":r["time"],
                        "model_version":variant,"market":market,"home":r["match"].home,"away":r["match"].away,
                        "features":asdict(r["features"]),"probability":list(map(float,prob)),
                        "raw_probability":list(map(float,p[index])),
                        "result":r["labels"][market],"odds_timestamp":None,"data_source":"football-data.co.uk"})
        results[market]={"models":result,"ensemble_weights":dict(zip(ensemble.names,map(float,ensemble.weights)))}
    fingerprint=hashlib.sha256(json.dumps([(r["time"],r["labels"],r["match"].odds_opening) for r in rows],sort_keys=True).encode()).hexdigest()
    report={"division":division,"test_year":test_year,"data_version":fingerprint,
            "windows":[{"n":len(g),"start":g[0]["time"],"end":g[-1]["time"]} for g in groups],
            "window_roles":["train","early_stopping","ensemble_train","calibration_train","test"],
            "refit":"baseline diário/rolling 1095d; ML congelado antes do ensemble/calibração/teste",
            "financial_limitation":"Odds CSV reais sem timestamps: ROI/Yield apenas exploratórios; CLV null",
            "production_model":"BASELINE_V1","results":results}
    return report,predictions_out


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--division",default="E0")
    parser.add_argument("--start-year",type=int,default=2015)
    parser.add_argument("--test-year",type=int,default=2025)
    parser.add_argument("--output",default="output/engineering/benchmark")
    args=parser.parse_args()
    report,rows=run_benchmark(args.division,args.start_year,args.test_year)
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    (out/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    with (out/"predictions.jsonl").open("w",encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row,allow_nan=False)+"\n")
    print(json.dumps({"output":str(out),"windows":report["windows"]},indent=2),flush=True)


if __name__ == "__main__":
    main()
