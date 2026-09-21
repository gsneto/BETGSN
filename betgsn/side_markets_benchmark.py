"""Benchmark temporal de corners e cards (mercados secundários).

Disciplina point-in-time: cada partida só usa histórico cujo resultado já
estava disponível ANTES do kickoff (embargo de 48h via `result_time`).

Modelos comparados:
  - Poisson e Binomial Negativa: paramétricos sobre médias móveis temporais
    (sem etapa de fit — a "estimação" é a própria janela móvel point-in-time).
  - XGBoost e LightGBM: treinam com split temporal (train → early-stop → test).
  - Ensemble ingênuo (média de XGBoost + LightGBM): experimental.

Odds de corners/cards NÃO existem nos CSVs do football-data.co.uk (só 1X2,
O/U e AH). Por isso toda métrica de aposta fica explicitamente indisponível;
a avaliação é probabilística (LogLoss, Brier, ECE).

Overdispersion: variance/mean por liga x temporada. Se >> 1, Poisson pode ser
inadequado e a Binomial Negativa é a comparação honesta.
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

import numpy as np

from .football_data_uk import FootballDataClient
from .timeutil import utc_key
from .temporal import result_time
from .features.elo import Elo
from .models.base import TemporalBatch
from .models.xgboost_model import XGBoostModel
from .models.lightgbm_model import LightGBMModel
from .models.corners import PoissonCornersModel, NegBinCornersModel, CornersFeatures
from .models.cards import PoissonCardsModel, NegBinCardsModel, CardsFeatures
from .evaluation import score_predictions

CORNERS_LINES: tuple[float, ...] = (8.5, 9.5, 10.5)
CARDS_LINES: tuple[float, ...] = (3.5, 4.5, 5.5)
MIN_MATCHES_PER_DIVISION = 800
MIN_TEST = 150

STAT_KEYS = ("corners", "cards", "shots", "shots_on_target")


# --------------------------------------------------------------------------
# Rolling point-in-time por time
# --------------------------------------------------------------------------

def _side(m, team: str, stat: str):
    return getattr(m, ("home_" if m.home == team else "away_") + stat)


def _opp(m, team: str, stat: str):
    return getattr(m, ("away_" if m.home == team else "home_") + stat)


def _rolling(prior, team: str, stat: str, window: int | None, direction: str):
    src = _side if direction == "for" else _opp
    values = [src(m, team, stat) for m in (prior[-window:] if window else prior)]
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _team_history_add(history, avail, team, match, avail_key):
    history[team].append(match)
    avail[team].append(avail_key)


def _prior_available(history, avail, team, cutoff_key):
    idx = bisect_left(avail[team], cutoff_key)
    return history[team][:idx]


# --------------------------------------------------------------------------
# Coleta de features + labels
# --------------------------------------------------------------------------

def build_rows(raw, divisions):
    """Produz, por partida, o dicionário de features e os labels.

    raw: lista de CsvMatch ordenada por kickoff.
    """
    rows = []
    history: dict[str, list] = defaultdict(list)
    avail: dict[str, list] = defaultdict(list)
    elo = Elo()
    # Perfil de árbitro point-in-time: name -> (total_cards, n, home_cards)
    referee: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])

    for m in raw:
        if m.home_goals is None or m.away_goals is None:
            continue
        kick = utc_key(m.kickoff, m.timezone)
        home, away = m.home, m.away

        prior_h = _prior_available(history, avail, home, kick)
        prior_a = _prior_available(history, avail, away, kick)

        feats: dict[str, float | None] = {}
        feats["home_elo"] = elo.rating(home)
        feats["away_elo"] = elo.rating(away)
        feats["elo_difference"] = feats["home_elo"] - feats["away_elo"]

        for prefix, prior, team in (("home", prior_h, home), ("away", prior_a, away)):
            for stat in ("corners", "cards", "shots", "shots_on_target"):
                for direction in ("for", "against"):
                    full = _rolling(prior, team, stat, None, direction)
                    feats[f"{prefix}_{stat}_{direction}"] = full
                    for window in (5, 10, 20):
                        feats[f"{prefix}_{stat}_{direction}_{window}"] = _rolling(
                            prior, team, stat, window, direction)

        # Referee features (só histórico anterior).
        ref = m.referee or ""
        if ref:
            tc, n, hc = referee[ref]
            feats["referee_avg_cards"] = (tc / n) if n >= 5 else None
            feats["referee_n"] = float(n)
            feats["referee_home_bias"] = (hc / tc) if tc > 0 and n >= 5 else None
        else:
            feats["referee_avg_cards"] = None
            feats["referee_n"] = 0.0
            feats["referee_home_bias"] = None

        # Labels (total corners/cards). Sem dado -> skip da avaliação da linha.
        total_corners = (m.home_corners + m.away_corners
                         if m.home_corners is not None and m.away_corners is not None
                         else None)
        total_cards = (m.home_cards + m.away_cards
                       if m.home_cards is not None and m.away_cards is not None
                       else None)

        rows.append({
            "kick": kick,
            "home": home,
            "away": away,
            "division": m.division,
            "season": m.season,
            "features": feats,
            "total_corners": total_corners,
            "total_cards": total_cards,
            "referee": ref,
        })

        # Atualiza estado point-in-time para as próximas partidas.
        avail_key = result_time(m.to_historical())
        _team_history_add(history, avail, home, m, avail_key)
        _team_history_add(history, avail, away, m, avail_key)
        elo.update(m)
        if ref:
            tc, n, hc = referee[ref]
            referee[ref] = [tc + total_cards or 0, n + 1, hc + (m.home_cards or 0)]

    return rows


def dispersion_stats(rows, side):
    """mean/variance/dispersion do total por liga x temporada."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    key = "total_" + side
    for r in rows:
        v = r[key]
        if v is not None:
            groups[(r["division"], r["season"])].append(v)
    out = []
    for (division, season), values in sorted(groups.items()):
        if len(values) < 30:
            continue
        mean = np.mean(values)
        var = np.var(values, ddof=0)
        out.append({
            "division": division,
            "season": season,
            "n": len(values),
            "mean": round(float(mean), 3),
            "variance": round(float(var), 3),
            "dispersion_ratio": round(float(var / mean), 3) if mean > 0 else None,
        })
    return out


# --------------------------------------------------------------------------
# Modelos paramétricos
# --------------------------------------------------------------------------

def _corners_features(row):
    f = row["features"]
    return CornersFeatures(
        home_corners_for=f.get("home_corners_for") or 5.0,
        home_corners_against=f.get("home_corners_against") or 5.0,
        away_corners_for=f.get("away_corners_for") or 5.0,
        away_corners_against=f.get("away_corners_against") or 5.0,
        home_shots=f.get("home_shots_for") or 12.0,
        away_shots=f.get("away_shots_for") or 12.0,
        home_shots_on_target=f.get("home_shots_on_target_for") or 4.0,
        away_shots_on_target=f.get("away_shots_on_target_for") or 4.0,
        home_elo=f.get("home_elo") or 1500.0,
        away_elo=f.get("away_elo") or 1500.0,
    )


def _cards_features(row):
    f = row["features"]
    return CardsFeatures(
        home_cards_for=f.get("home_cards_for") or 2.0,
        home_cards_against=f.get("home_cards_against") or 2.0,
        away_cards_for=f.get("away_cards_for") or 2.0,
        away_cards_against=f.get("away_cards_against") or 2.0,
        referee_avg_cards=f.get("referee_avg_cards"),
        referee_home_bias=f.get("referee_home_bias"),
        home_elo=f.get("home_elo") or 1500.0,
        away_elo=f.get("away_elo") or 1500.0,
        elo_difference=f.get("elo_difference") or 0.0,
    )


# --------------------------------------------------------------------------
# Matriz de features para ML
# --------------------------------------------------------------------------

def to_matrix(rows, columns=None):
    columns = list(columns) if columns else sorted(rows[0]["features"].keys())
    return np.array(
        [[r["features"].get(c, np.nan) for c in columns] for r in rows],
        dtype=float,
    ), columns


# --------------------------------------------------------------------------
# Avaliação de uma linha
# --------------------------------------------------------------------------

def binary_metrics(prob_over, labels):
    """labels: array 0/1. prob_over: array P(over)."""
    p = np.column_stack([1.0 - np.asarray(prob_over), np.asarray(prob_over)])
    return score_predictions(p, labels)


def _logloss(p_over, y):
    p = np.clip(np.asarray(p_over, dtype=float), 1e-12, 1 - 1e-12)
    return float(-np.mean(np.where(y == 1, np.log(p), np.log(1 - p))))


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def run(divisions, start_year, test_year, side, output):
    client = FootballDataClient()
    raw = client.load_matches(list(divisions))
    raw = sorted(
        [m for m in raw if start_year <= int(m.date[:4]) <= test_year],
        key=lambda m: utc_key(m.kickoff, m.timezone),
    )
    if len(raw) < MIN_MATCHES_PER_DIVISION:
        return {"skipped": f"amostra insuficiente: {len(raw)} partidas"}

    rows = build_rows(raw, divisions)
    rows = [r for r in rows if r["total_" + side] is not None]

    test_start = f"{test_year}-01-01"
    val_start = f"{test_year - 1}-01-01"
    test = [r for r in rows if r["kick"] >= utc_key(test_start)]
    val = [r for r in rows if utc_key(val_start) <= r["kick"] < utc_key(test_start)]
    train = [r for r in rows if r["kick"] < utc_key(val_start)]

    if len(train) < 200 or len(val) < 100 or len(test) < MIN_TEST:
        return {
            "skipped": "blocos temporais insuficientes: "
            f"train={len(train)} val={len(val)} test={len(test)}",
        }

    lines = CORNERS_LINES if side == "corners" else CARDS_LINES
    report = {
        "kind": "side_markets_benchmark",
        "side": side,
        "divisions": list(divisions),
        "test_year": test_year,
        "lines": list(lines),
        "n_matches": len(rows),
        "windows": {
            "train": len(train), "validation": len(val), "test": len(test),
        },
        "overdispersion": dispersion_stats(rows, side),
        "financial_limitation": "odds de corners/cards ausentes nos CSVs: sem métricas de aposta",
        "results": {},
        "feature_importance": {},
    }

    for line in lines:
        y_train = np.array([int(r["total_" + side] > line) for r in train])
        y_val = np.array([int(r["total_" + side] > line) for r in val])
        y_test = np.array([int(r["total_" + side] > line) for r in test])

        x_train, columns = to_matrix(train)
        x_val, _ = to_matrix(val, columns)
        x_test, _ = to_matrix(test, columns)

        predictions = {}

        # Paramétricos: probabilidade de Over no teste.
        if side == "corners":
            poisson_model = PoissonCornersModel()
            negbin_model = NegBinCornersModel()
            predictions["Poisson"] = np.array([
                poisson_model.predict(_corners_features(r), total_lines=(line,))
                .total_probabilities[f"Cantos Over {line}"]
                for r in test
            ])
            predictions["NegBin"] = np.array([
                negbin_model.predict(_corners_features(r), total_lines=(line,))
                .total_probabilities[f"Cantos Over {line}"]
                for r in test
            ])
        else:
            poisson_model = PoissonCardsModel()
            negbin_model = NegBinCardsModel()
            predictions["Poisson"] = np.array([
                poisson_model.predict(_cards_features(r), total_lines=(line,))
                .total_probabilities[f"Cartoes Over {line}"]
                for r in test
            ])
            predictions["NegBin"] = np.array([
                negbin_model.predict(_cards_features(r), total_lines=(line,))
                .total_probabilities[f"Cartoes Over {line}"]
                for r in test
            ])

        # ML: treina por linha (classificação binária Over/Under).
        tb_train = TemporalBatch(x_train, y_train, tuple(r["kick"] for r in train))
        tb_val = TemporalBatch(x_val, y_val, tuple(r["kick"] for r in val))
        xgb = XGBoostModel().fit(tb_train, tb_val)
        lgbm = LightGBMModel().fit(tb_train, tb_val)
        predictions["XGBoost"] = xgb.predict_proba(x_test)[:, 1]
        predictions["LightGBM"] = lgbm.predict_proba(x_test)[:, 1]
        predictions["Ensemble"] = (predictions["XGBoost"] + predictions["LightGBM"]) / 2.0

        line_result = {}
        for name, prob_over in predictions.items():
            metrics = binary_metrics(prob_over, y_test)
            line_result[name] = {
                "logloss": round(metrics["logloss"], 4),
                "brier": round(metrics["brier"], 4),
                "ece": round(metrics["ece"], 4),
                "n": int(metrics["n"]),
                "p_over_mean": round(float(np.mean(prob_over)), 4),
                "actual_over_rate": round(float(np.mean(y_test)), 4),
            }
        report["results"][f"{side}_{line}"] = line_result

        # Importância (native + permutation) no último modelo ML por linha.
        if line == lines[0]:
            report["feature_importance"] = _importance(xgb, lgbm, x_test, y_test, columns)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def _importance(xgb, lgbm, x_test, y_test, columns):
    """Native + permutation importance para os dois boosters."""
    result = {}
    for name, model in (("XGBoost", xgb), ("LightGBM", lgbm)):
        native = list(map(float, model.model.feature_importances_))
        order = np.argsort(native)[::-1]
        result[name] = {
            "native": [{"feature": columns[i], "importance": native[i]} for i in order[:15]],
            "permutation": _permutation(model, x_test, y_test, columns),
        }
    return result


def _permutation(model, x, y, columns, n_repeats=5, seed=6767):
    rng = np.random.default_rng(seed)
    base = _logloss(model.predict_proba(x)[:, 1], y)
    scores = []
    for j in range(x.shape[1]):
        deltas = []
        for _ in range(n_repeats):
            xp = x.copy()
            rng.shuffle(xp[:, j])
            deltas.append(_logloss(model.predict_proba(xp)[:, 1], y) - base)
        scores.append((columns[j], float(np.mean(deltas))))
    scores.sort(key=lambda t: -t[1])
    return [{"feature": f, "delta_logloss": round(d, 6)} for f, d in scores[:15]]


def main():
    parser = argparse.ArgumentParser(description="Benchmark temporal de corners/cards.")
    parser.add_argument("--side", choices=["corners", "cards"], required=True)
    parser.add_argument("--divisions", default="E0,SP1,I1,D1,F1")
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--test-year", type=int, default=2025)
    parser.add_argument("--output", default="output/engineering/benchmark")
    args = parser.parse_args()

    divisions = [d.strip() for d in args.divisions.split(",") if d.strip()]
    report = run(divisions, args.start_year, args.test_year, args.side,
                 f"{args.output}/{args.side}.json")

    if "skipped" in report:
        print(f"PULADO: {report['skipped']}")
        return

    print(f"{args.side} | teste {args.test_year} | {report['n_matches']} partidas | "
          f"linhas {report['lines']}")
    print("odds ausentes nos CSVs: métricas são apenas probabilísticas\n")
    for line_key, line_result in report["results"].items():
        print(f"--- {line_key} (n={line_result.get('Poisson', {}).get('n')}) ---")
        for model, m in line_result.items():
            print(f"  {model:<12} logloss={m['logloss']:.4f}  brier={m['brier']:.4f}  "
                  f"ece={m['ece']:.4f}  over={m['actual_over_rate']:.3f}")

    disp = report["overdispersion"]
    if disp:
        print("\nOverdispersion (variance/mean):")
        for d in disp[:10]:
            print(f"  {d['division']} {d['season']}: mean={d['mean']} "
                  f"var={d['variance']} ratio={d['dispersion_ratio']}")
        high = [d for d in disp if d["dispersion_ratio"] and d["dispersion_ratio"] > 1.3]
        if high:
            print(f"\n{len(high)} segmentos com dispersion_ratio > 1.3: "
                  "Poisson pode ser inadequado; NegBin é a comparação honesta.")


if __name__ == "__main__":
    main()
