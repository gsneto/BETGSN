"""Ablação de features — impacto marginal de cada grupo, out-of-sample.

Pergunta: cada grupo de features adiciona informação ao modelo de 1X2, ou é
redundante/prejudicial? Mede-se em métrica probabilística (LogLoss, Brier,
RPS, ECE), nunca em ROI de amostra pequena.

Duas análises:
  A. Forward addition:  Elo -> +Form -> +OpponentStrength -> +Rest -> +H2H
  B. Leave-one-group-out: modelo completo menos cada grupo

O "BASELINE" de referência é o modelo de produção (Poisson/Dixon-Coles), que
não usa nenhuma dessas features. Grupos sem dado point-in-time (xG, injuries,
lineups, odds movement) ficam FORA e declarados como indisponíveis.

Grupos de features são identificados por prefixo de coluna, exatamente como o
FeatureBuilder os nomeia.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from pathlib import Path

import numpy as np

# Evita oversubscription de threads OpenMP/joblib (os boosters usam n_jobs=2)
# que pode travar o fit em sequências longas no Windows.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

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
from .evaluation import score_predictions

#: Grupos disponíveis point-in-time. Ordem define a forward addition.
GROUPS: tuple[str, ...] = ("Elo", "Form", "OpponentStrength", "Rest", "H2H")

#: Grupos indisponíveis (sem dado point-in-time no CSV), declarados, não testados.
UNAVAILABLE_GROUPS: tuple[str, ...] = ("xG", "Injuries", "Lineups", "OddsMovement")


def group_members(columns: list[str], group: str) -> list[int]:
    idx = []
    for i, c in enumerate(columns):
        if group == "Elo":
            if c == "elo_difference" or c.startswith("home_elo") or c.startswith("away_elo"):
                idx.append(i)
        elif group == "Form":
            if c.startswith("home_form") or c.startswith("away_form"):
                idx.append(i)
        elif group == "OpponentStrength":
            if "adjusted" in c or "opponent_elo" in c:
                idx.append(i)
        elif group == "Rest":
            if ("days_since_last" in c or "matches_7d" in c or "matches_14d" in c
                    or "congestion" in c):
                idx.append(i)
        elif group == "H2H":
            if c.startswith("h2h_"):
                idx.append(i)
    return idx


def _build_rows(division, start_year, test_year):
    """Replica a construção de features do benchmark, retornando rows + labels."""
    raw = FootballDataClient().load_matches([division])
    raw = sorted(
        [m for m in raw if start_year <= int(m.date[:4]) <= test_year],
        key=lambda m: utc_key(m.kickoff, m.timezone),
    )
    history = [m.to_historical() for m in raw]
    corpus = HistoricalCorpus(history)
    builder = FeatureBuilder(history)
    rows = []
    cached_day = None
    ratings = {}
    lg = 0.0
    for csv, m in zip(raw, history):
        timestamp = utc_key(m.kickoff, m.timezone)
        fx = Fixture(m.home, m.away, m.league, timestamp)
        features = builder.build(fx)
        if timestamp[:10] != cached_day:
            prior = corpus.available_before(timestamp)
            lower = parse_kickoff(timestamp) - timedelta(days=1095)
            prior = [p for p in prior if parse_kickoff(p.kickoff, p.timezone) >= lower]
            teams = sorted({p.home for p in prior} | {p.away for p in prior})
            ratings = fit_ratings(prior, teams) if len(prior) >= 300 else {}
            lg = sum(p.home_goals + p.away_goals for p in prior) / len(prior) if prior else 0
            cached_day = timestamp[:10]
        if m.home not in ratings or m.away not in ratings:
            continue
        a = analyze_fixture(fx, ratings[m.home], ratings[m.away], lg, 1.18,
                            attack_blend=0, market_keys=("1x2",))
        baseline = np.array([a.markets[MARKETS["1x2"]][o] for o in OUTCOMES["1x2"]])
        label = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
        rows.append({"time": timestamp, "available": result_time(m),
                     "features": features, "label": label, "baseline": baseline})
    return rows


def _split(rows, test_year):
    val_start = f"{test_year - 1}-01-01"
    test_start = f"{test_year}-01-01"
    train = [r for r in rows if r["available"] < utc_key(val_start)]
    val = [r for r in rows if utc_key(val_start) <= r["time"] < utc_key(test_start)]
    test = [r for r in rows if r["time"] >= utc_key(test_start)]
    return train, val, test


def _fit_evaluate(train, val, test, columns_subset, columns_all, model_cls, y_label=0):
    """Treina um booster nas colunas escolhidas e avalia logloss/brier/rps/ece."""
    idx = [columns_all.index(c) for c in columns_subset]
    x_train = np.array([[r["features"].values.get(c, np.nan) for c in columns_subset]
                        for r in train], dtype=float)
    x_val = np.array([[r["features"].values.get(c, np.nan) for c in columns_subset]
                      for r in val], dtype=float)
    x_test = np.array([[r["features"].values.get(c, np.nan) for c in columns_subset]
                       for r in test], dtype=float)
    y_train = np.array([r["label"] for r in train])
    y_val = np.array([r["label"] for r in val])
    y_test = np.array([r["label"] for r in test])
    model = model_cls().fit(
        TemporalBatch(x_train, y_train, tuple(r["time"] for r in train)),
        TemporalBatch(x_val, y_val, tuple(r["time"] for r in val)),
    )
    proba = model.predict_proba(x_test)
    m = score_predictions(proba, y_test)
    return {"logloss": m["logloss"], "brier": m["brier"],
            "rps": m["rps"], "ece": m["ece"]}, proba


def run_ablation(divisions, start_year, test_years, output):
    """Roda as duas análises e agrega por liga x temporada.

    Escreve o relatório de forma INCREMENTAL após cada segmento: se um
    segmento falhar, os anteriores já estão persistidos.
    """
    all_segments = []  # por (division, year): dict com métricas de cada config

    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)

    for division in divisions:
        for test_year in test_years:
            print(f"ablation {division} {test_year} ...", flush=True)
            try:
                segment = _run_segment(division, start_year, test_year)
            except Exception as exc:  # noqa: BLE001 — isola um segmento ruim
                print(f"  FALHA {division} {test_year}: {exc}", flush=True)
                all_segments.append({"division": division, "test_year": test_year,
                                     "error": str(exc)})
                continue
            if segment is None:
                continue
            all_segments.append(segment)
            # Persistência incremental.
            report = {
                "kind": "feature_ablation",
                "market": "1x2",
                "groups_tested": list(GROUPS),
                "groups_unavailable": list(UNAVAILABLE_GROUPS),
                "method": (
                    "forward_addition e leave_one_group_out; metricas "
                    "probabilisticas; baseline = Poisson/Dixon-Coles de "
                    "producao (sem features)"
                ),
                "segments": all_segments,
                "aggregate": _aggregate([s for s in all_segments if "models" in s]),
            }
            (out / "ablation.json").write_text(
                json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")

    return {
        "kind": "feature_ablation",
        "market": "1x2",
        "groups_tested": list(GROUPS),
        "groups_unavailable": list(UNAVAILABLE_GROUPS),
        "segments": all_segments,
        "aggregate": _aggregate([s for s in all_segments if "models" in s]),
    }


def _run_segment(division, start_year, test_year):
    rows = _build_rows(division, start_year, test_year)
    train, val, test = _split(rows, test_year)
    if len(train) < 200 or len(val) < 100 or len(test) < 150:
        print(f"  skip {division} {test_year}: blocos insuficientes "
              f"({len(train)},{len(val)},{len(test)})", flush=True)
        return None

    y_test = np.array([r["label"] for r in test])
    baseline_metrics = score_predictions(
        np.array([r["baseline"] for r in test]), y_test)

    columns_all = sorted(train[0]["features"].values.keys())
    all_cols = columns_all[:]

    segment = {
        "division": division,
        "test_year": test_year,
        "n_train": len(train),
        "n_val": len(val),
        "n_test": len(test),
        "baseline": {k: round(baseline_metrics[k], 4)
                     for k in ("logloss", "brier", "rps", "ece")},
        "models": {},
    }

    for model_name, model_cls in (("XGBoost", XGBoostModel),
                                  ("LightGBM", LightGBMModel)):
        current = []
        forward = {}
        for group in GROUPS:
            current += [all_cols[i] for i in group_members(all_cols, group)]
            if not current:
                continue
            m, _ = _fit_evaluate(train, val, test, current, all_cols, model_cls)
            forward["+" + group] = {k: round(v, 4) for k, v in m.items()}

        leaveout = {}
        for group in GROUPS:
            mask = set(group_members(all_cols, group))
            subset = [c for i, c in enumerate(all_cols) if i not in mask]
            m, _ = _fit_evaluate(train, val, test, subset, all_cols, model_cls)
            leaveout["-" + group] = {k: round(v, 4) for k, v in m.items()}

        full, _ = _fit_evaluate(train, val, test, all_cols, all_cols, model_cls)
        forward["FULL"] = {k: round(v, 4) for k, v in full.items()}
        leaveout["FULL"] = {k: round(v, 4) for k, v in full.items()}

        segment["models"][model_name] = {
            "forward_addition": forward,
            "leave_one_out": leaveout,
            "full": {k: round(v, 4) for k, v in full.items()},
        }

    return segment


def _aggregate(segments):
    """Agrega o Δ (vs modelo completo) de cada configuração entre segmentos.

    Forward addition ("+Grupo") e leave-one-out ("-Grupo") são tratadas
    separadamente: a primeira mede o ganho ao ADICIONAR o grupo sobre os
    anteriores; a segunda mede a perda ao REMOVER o grupo do modelo completo.
    """
    segments = [s for s in segments if "models" in s]
    forward_configs = set()
    leaveout_configs = set()
    for seg in segments:
        for model in seg["models"].values():
            forward_configs.update(model["forward_addition"].keys())
            leaveout_configs.update(model["leave_one_out"].keys())

    agg = {}
    full_metrics = {"logloss", "brier", "rps", "ece"}
    for model_name in ("XGBoost", "LightGBM"):
        agg[model_name] = {"forward": {}, "leave_one_out": {}}
        for config in sorted(forward_configs):
            agg[model_name]["forward"][config] = _avg_delta(
                segments, model_name, "forward_addition", config, full_metrics)
        for config in sorted(leaveout_configs):
            agg[model_name]["leave_one_out"][config] = _avg_delta(
                segments, model_name, "leave_one_out", config, full_metrics)
    return agg


def _avg_delta(segments, model_name, section, config, full_metrics):
    deltas = {k: [] for k in full_metrics}
    for seg in segments:
        if model_name not in seg["models"]:
            continue
        entry = seg["models"][model_name][section].get(config)
        if entry is None:
            continue
        full = seg["models"][model_name]["full"]
        for k in full_metrics:
            deltas[k].append(entry[k] - full[k])
    return {
        k: {
            "mean_delta": round(float(np.mean(v)), 5) if v else None,
            "n_segments": len(v),
        }
        for k, v in deltas.items()
    }


def _print(agg):
    print("\nΔ vs modelo completo (negativo = melhor). Média entre segmentos.")
    for model_name in ("XGBoost", "LightGBM"):
        print(f"\n=== {model_name} ===")
        for section, title in (("forward", "Forward addition (+grupo)"),
                               ("leave_one_out", "Leave-one-group-out (-grupo)")):
            print(f"  {title}")
            header = f"    {'config':<16}{'ΔLogLoss':>12}{'ΔBrier':>10}{'ΔRPS':>10}{'ΔECE':>10}{'seg':>5}"
            print(header)
            print("    " + "-" * (len(header) - 4))
            for config, metrics in agg[model_name][section].items():
                ll = metrics["logloss"]["mean_delta"]
                br = metrics["brier"]["mean_delta"]
                rp = metrics["rps"]["mean_delta"]
                ec = metrics["ece"]["mean_delta"]
                n = metrics["logloss"]["n_segments"]
                fmt = lambda x: f"{x:+.5f}" if x is not None else "    n/d"
                print(f"    {config:<16}{fmt(ll):>12}{fmt(br):>10}{fmt(rp):>10}{fmt(ec):>10}{n:>5}")


def main():
    parser = argparse.ArgumentParser(description="Ablação de features 1X2.")
    parser.add_argument("--divisions", default="E0,SP1,I1,D1,F1")
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--test-years", default="2024,2025")
    parser.add_argument("--output", default="output/engineering/feature_ablation")
    args = parser.parse_args()

    divisions = [d.strip() for d in args.divisions.split(",") if d.strip()]
    test_years = [int(y) for y in args.test_years.split(",") if y.strip()]
    report = run_ablation(divisions, args.start_year, test_years, args.output)
    _print(report["aggregate"])
    print(f"\nrelatorio: {Path(args.output)/'ablation.json'}")


if __name__ == "__main__":
    main()
