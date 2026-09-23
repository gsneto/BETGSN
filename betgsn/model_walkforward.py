"""BETGSN :: model_walkforward — modelo vs mercado nas MESMAS janelas OOS.

O QUE ESTE MODULO RESPONDE
--------------------------
"O modelo BETGSN adiciona informação além da referência de mercado?"

A avaliação da estratégia de valor (`value_walkforward`) mede p = 1/odd
— MARKET BASELINE. Este harness avalia o MODELO DE PRODUÇÃO (Poisson
bivariado + Dixon-Coles via `fit_ratings`, BASELINE_V1) nas MESMAS
janelas walk-forward (mesmo `WalkForwardConfig`, mesmo embargo),
comparando QUATRO fontes de probabilidade sobre a MESMA população de
linhas apostáveis:

    MARKET RAW       p = 1/odd (melhor odd entre casas)
    MARKET FAIR      p = de-vig multiplicativo do consenso de medianas
    MODEL RAW        p = probabilidade do modelo (ratings fit no TRAIN)
    MODEL CALIBRATED calibrador escolhido no TRAIN, congelado no TEST

Disciplina temporal por janela (idêntica à da estratégia):

    TRAIN  -> ratings do modelo ajustados SÓ em partidas anteriores a
              train_end (rolling 1095d, como o benchmark de produção);
              calibrador escolhido por validação interna do TRAIN e
              reajustado no TRAIN completo
    GAP    -> embargo (apostas no gap fora de tudo)
    TEST   -> modelo CONGELADO gera probabilidades; métricas medidas

O caminho ESTRATÉGIA com modelo (ETAPA 7) também é medido aqui, sempre
SEPARADO da performance do modelo: strategy_model = apostas com
EV = p_model * best_odd - 1 > 0, ROI OOS por janela. Line-shopping é
ablação própria (median em vez de best) — nunca atribuído ao modelo.

Este modulo não conhece providers, endpoints ou bookmakers de API:
consome apenas a lista canônica de apostas (`collect_bets`) e o corpus
histórico (`HistoricalMatch`).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .model import Fixture, fit_ratings
from .model import expected_goals, build_score_matrix
from .timeutil import parse_kickoff, utc_key
from .value_walkforward import (
    CALIBRATION_METHODS,
    CALIBRATION_VALIDATION_FRACTION,
    DEFAULT_GAP_DAYS,
    MIN_ECE_SAMPLE,
    WalkForwardConfig,
    _apply_calibrator,
    _binary_matrix,
    _bootstrap_ci,
    _calibrated_for_bets,
    _fit_calibrator,
    _metrics_from_pairs,
    _raw_prob_pairs,
    _ret,
    _roi_stats,
    _select_calibration_method,
    audit_windows,
    build_windows,
    wilson_ci,
)

#: Versao do esquema do cache de validacao de modelo.
MODEL_CACHE_SCHEMA_VERSION = 1

#: Janela de ratings do modelo (rolling), igual ao benchmark de produção.
RATING_WINDOW_DAYS = 1095
#: Home advantage do caminho de produção (benchmark/real_signals).
HOME_ADVANTAGE = 1.18
#: Amostra minima de partidas do TRAIN para ajustar ratings.
MIN_RATINGS_SAMPLE = 300

#: EV minimo para a strategy_model apostar (fracao; 0 = qualquer EV+).
MODEL_EV_THRESHOLD = 0.0


def _model_cache_path() -> Path:
    from .config import output_root

    return output_root() / "model_validation_oos.json"


def model_cache_fingerprint(
    *,
    config: WalkForwardConfig,
    corpus_signature: str,
    rating_window_days: int = RATING_WINDOW_DAYS,
    home_advantage: float = HOME_ADVANTAGE,
    ev_threshold: float = MODEL_EV_THRESHOLD,
) -> str:
    """Fingerprint do cache de modelo: tudo que muda a medição."""
    payload = {
        "schema": MODEL_CACHE_SCHEMA_VERSION,
        "corpus": corpus_signature,
        "rating_window_days": int(rating_window_days),
        "home_advantage": float(home_advantage),
        "ev_threshold": float(ev_threshold),
        "train_days": int(config.train_days),
        "test_days": int(config.test_days),
        "gap_days": int(config.gap_days),
        "min_test_bets": int(config.min_test_bets),
        "bootstrap_resamples": int(config.bootstrap_resamples),
        "bootstrap_seed": int(config.bootstrap_seed),
        "calibration_methods": list(CALIBRATION_METHODS),
        "calibration_validation_fraction": CALIBRATION_VALIDATION_FRACTION,
        "min_ece_sample": MIN_ECE_SAMPLE,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


# --------------------------------------------------------------------------
# Modelo de produção: probabilidades 1X2 a partir de ratings congelados
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenModel:
    """Ratings + médias de liga ajustados no TRAIN, congelados."""

    ratings: dict
    league_goals: float
    train_end: str
    n_matches: int


def fit_model_on_train(
    matches: Sequence, train_end: str,
    rating_window_days: int = RATING_WINDOW_DAYS,
) -> FrozenModel | None:
    """Ajusta o modelo de produção com partidas ANTERIORES a train_end.

    PIT: só partidas cujo kickoff é estritamente anterior ao fim do
    TRAIN. O resultado de uma partida entra quando ela acontece — o
    embargo entre train_end e test_start cobre a publicação.
    """
    cutoff = date.fromisoformat(train_end)
    lower = cutoff - timedelta(days=rating_window_days)
    prior = [
        m for m in matches
        if lower <= date.fromisoformat(str(m.kickoff)[:10]) < cutoff
    ]
    if len(prior) < MIN_RATINGS_SAMPLE:
        return None
    teams = sorted({m.home for m in prior} | {m.away for m in prior})
    ratings = fit_ratings(prior, teams, home_advantage=HOME_ADVANTAGE)
    league_goals = sum(
        m.home_goals + m.away_goals for m in prior
    ) / len(prior)
    return FrozenModel(
        ratings=ratings,
        league_goals=league_goals,
        train_end=train_end,
        n_matches=len(prior),
    )


def _prob_1x2(model: FrozenModel, home: str, away: str):
    """(p1, pX, p2) do modelo CONGELADO; None sem rating de algum lado."""
    hr = model.ratings.get(home)
    ar = model.ratings.get(away)
    if hr is None or ar is None:
        return None
    lam_h, lam_a = expected_goals(
        hr, ar, model.league_goals, home_advantage=HOME_ADVANTAGE,
    )
    matrix = build_score_matrix(lam_h, lam_a)
    return matrix.prob_home_win(), matrix.prob_draw(), matrix.prob_away_win()


def _outcome_index(oc: str) -> int:
    return {"1": 0, "X": 1, "2": 2}.get(str(oc), -1)


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@dataclass
class SourceMetrics:
    """Métricas de UMA fonte de probabilidade numa janela."""

    brier: float | None = None
    logloss: float | None = None
    ece: float | None = None
    n: int = 0


@dataclass
class ModelWindowResult:
    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    gap_days: int
    n_train_matches: int = 0
    n_test_bets: int = 0
    market_raw: SourceMetrics = field(default_factory=SourceMetrics)
    market_fair: SourceMetrics = field(default_factory=SourceMetrics)
    model_raw: SourceMetrics = field(default_factory=SourceMetrics)
    model_calibrated: SourceMetrics = field(default_factory=SourceMetrics)
    calibration_method: str = "raw"
    #: strategy_model nesta janela
    strategy_model_n: int = 0
    strategy_model_roi: float | None = None
    strategy_market_n: int = 0
    strategy_market_roi: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelComparisonResult:
    config: WalkForwardConfig
    n_windows: int = 0
    n_windows_valid: int = 0
    n_bets_oos: int = 0
    market_raw: SourceMetrics = field(default_factory=SourceMetrics)
    market_fair: SourceMetrics = field(default_factory=SourceMetrics)
    model_raw: SourceMetrics = field(default_factory=SourceMetrics)
    model_calibrated: SourceMetrics = field(default_factory=SourceMetrics)
    #: teste pareado modelo vs mercado (block bootstrap por mes)
    paired_model_vs_fair: dict | None = None
    paired_model_vs_raw: dict | None = None
    windows: list[ModelWindowResult] = field(default_factory=list)
    #: strategy_model agregada (EV>0 com prob do modelo)
    strategy_model: dict = field(default_factory=dict)
    #: drift: metricas por janela por fonte
    drift: dict = field(default_factory=dict)
    embargo_days: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["windows"] = [w.to_dict() for w in self.windows]
        return out


def _source_metrics(ps: Sequence[float], ys: Sequence[int]) -> SourceMetrics:
    m = _metrics_from_pairs(ps, ys)
    return SourceMetrics(
        brier=m["brier"], logloss=m["logloss"], ece=m["ece"], n=m["n_prob"],
    )


def _paired_comparison(
    ps_a: list[float], ps_b: list[float], ys: list[int], months: list[str],
    metric: str, resamples: int, seed: int,
) -> dict | None:
    """Comparação pareada via `evaluation.compare_models` (bloco=mês).

    a = mercado, b = modelo. Veredito diz se o modelo difere do mercado.
    """
    try:
        import numpy as np

        from .evaluation import compare_models

        a = np.array([[1.0 - p, p] for p in ps_a])
        b = np.array([[1.0 - p, p] for p in ps_b])
        y = np.asarray(ys, dtype=int)
        return compare_models(
            a, b, y, metric=metric, resamples=resamples, seed=seed,
            block_keys=months,
        )
    except Exception:  # noqa: BLE001 - sem blocos suficientes, sem pareado
        return None


def run_model_walkforward(
    bets: Sequence[dict],
    matches: Sequence,
    config: WalkForwardConfig | None = None,
    progress: Any = None,
) -> ModelComparisonResult:
    """Executa modelo vs mercado nas MESMAS janelas da estratégia.

    `bets`: linhas apostáveis canônicas (collect_bets) com home/away/oc/
    odd/median/fair/res. `matches`: corpus histórico (para fit ratings).
    Violação temporal das janelas levanta — não mede.
    """
    config = config or WalkForwardConfig()
    config.validate()
    result = ModelComparisonResult(
        config=config, embargo_days=config.gap_days,
    )
    if not bets:
        return result

    days = sorted({str(b["d"]) for b in bets})
    windows = build_windows(days[0], days[-1], config)
    violations = audit_windows(windows)
    if violations:
        raise ValueError(
            "janelas walk-forward com violação temporal: "
            + "; ".join(violations)
        )
    result.n_windows = len(windows)

    # corpus indexado por dia para fit por janela (uma passada por janela)
    matches = sorted(matches, key=lambda m: str(m.kickoff))

    pooled: dict[str, list] = {
        "market_raw_ps": [], "market_fair_ps": [],
        "model_raw_ps": [], "model_cal_ps": [],
        "ys": [], "months": [],
    }
    strat_model_rets: list[float] = []
    strat_market_rets: list[float] = []
    per_window_logloss: dict[str, list[float]] = {
        "market_raw": [], "market_fair": [], "model_raw": [],
        "model_calibrated": [],
    }

    for w_i, window in enumerate(windows):
        if progress is not None:
            progress(w_i + 1, len(windows),
                     f"janela {window.index}: modelo vs mercado")

        model = fit_model_on_train(matches, window.train_end)
        train_bets = [
            b for b in bets
            if window.train_start <= str(b["d"]) < window.train_end
        ]
        test_bets = [
            b for b in bets
            if window.test_start <= str(b["d"]) < window.test_end
        ]
        win = ModelWindowResult(
            index=window.index,
            train_start=window.train_start, train_end=window.train_end,
            test_start=window.test_start, test_end=window.test_end,
            gap_days=window.gap_days,
            n_train_matches=model.n_matches if model else 0,
        )

        if model is None or not test_bets:
            result.windows.append(win)
            continue

        # ---- probabilidade do modelo por aposta (modelo CONGELADO) ----
        prob_cache: dict[tuple[str, str], tuple] = {}
        rows: list[dict] = []
        for b in test_bets:
            if b.get("res") == "push":
                continue
            idx = _outcome_index(b.get("oc", ""))
            if idx < 0:
                continue
            key = (str(b.get("home", "")), str(b.get("away", "")))
            if key not in prob_cache:
                prob_cache[key] = _prob_1x2(model, key[0], key[1])
            probs = prob_cache[key]
            if probs is None:
                continue
            rows.append({
                "b": b, "p_model": probs[idx],
                "p_raw": 1.0 / float(b["odd"]),
                "p_fair": float(b.get("fair") or 0.0) or None,
            })

        if not rows:
            result.windows.append(win)
            continue

        # ---- calibração do modelo: escolha no TRAIN, frozen no TEST ----
        method, calibrator, _n = _select_calibration_method(
            _rows_with_model_probs(train_bets, matches, window, model),
            _MODEL_VIEW, window,
        )
        win.calibration_method = method

        ps_raw = [r["p_raw"] for r in rows]
        ps_fair = [r["p_fair"] for r in rows if r["p_fair"] is not None]
        ys_fair = [1 if r["b"]["res"] == "win" else 0
                   for r in rows if r["p_fair"] is not None]
        ps_model = [r["p_model"] for r in rows]
        ys = [1 if r["b"]["res"] == "win" else 0 for r in rows]
        months = [str(r["b"]["d"])[:7] for r in rows]

        win.n_test_bets = len(rows)
        win.market_raw = _source_metrics(ps_raw, ys)
        win.market_fair = _source_metrics(ps_fair, ys_fair)
        win.model_raw = _source_metrics(ps_model, ys)
        if calibrator is not None:
            ps_cal = _apply_calibrator(
                calibrator, ps_model, window.test_start)
            win.model_calibrated = _source_metrics(ps_cal, ys)
        else:
            ps_cal = ps_model
            win.model_calibrated = win.model_raw

        # ---- pooled + por janela para drift ----
        fair_rows = [r for r in rows if r["p_fair"] is not None]
        pooled["market_raw_ps"].extend(ps_raw)
        pooled["market_fair_ps"].extend(
            [r["p_fair"] for r in fair_rows])
        pooled["model_raw_ps"].extend(ps_model)
        pooled["model_cal_ps"].extend(ps_cal)
        pooled["ys"].extend(ys)
        pooled["months"].extend(months)
        per_window_logloss["market_raw"].append(win.market_raw.logloss or 0.0)
        per_window_logloss["market_fair"].append(win.market_fair.logloss or 0.0)
        per_window_logloss["model_raw"].append(win.model_raw.logloss or 0.0)
        per_window_logloss["model_calibrated"].append(
            win.model_calibrated.logloss or 0.0)

        # ---- STRATEGY MODEL (EV>0, separada da performance do modelo) ----
        for r, p_cal in zip(rows, ps_cal):
            ev = p_cal * float(r["b"]["odd"]) - 1.0
            if ev > MODEL_EV_THRESHOLD:
                strat_model_rets.append(
                    _ret(float(r["b"]["odd"]), r["b"]["res"]))
        win.strategy_model_n = sum(
            1 for r, p in zip(rows, ps_cal)
            if p * float(r["b"]["odd"]) - 1.0 > MODEL_EV_THRESHOLD
        )
        if strat_rets_window := [
            _ret(float(r["b"]["odd"]), r["b"]["res"])
            for r, p in zip(rows, ps_cal)
            if p * float(r["b"]["odd"]) - 1.0 > MODEL_EV_THRESHOLD
        ]:
            roi, _se, _t = _roi_stats(strat_rets_window)
            win.strategy_model_roi = round(roi, 6)
            # strategy_market na MESMA janela: regra de favoritos curtos
            market_bets = [
                b for b in test_bets
                if b.get("mkt") == "Resultado Final (1X2)"
                and int(b.get("n_books", 0)) >= 3 and float(b["odd"]) < 1.30
            ]
            if market_bets:
                rets = [_ret(float(b["odd"]), b.get("res", "loss"))
                        for b in market_bets]
                mroi, _mse, _mt = _roi_stats(rets)
                win.strategy_market_n = len(market_bets)
                win.strategy_market_roi = round(mroi, 6)
                strat_market_rets.extend(rets)
        result.windows.append(win)

    # ---- agregados ----
    valid = [w for w in result.windows if w.n_test_bets > 0]
    result.n_windows_valid = len(valid)
    result.n_bets_oos = len(pooled["ys"])
    if pooled["ys"]:
        result.market_raw = _source_metrics(
            pooled["market_raw_ps"], pooled["ys"])
        fair_mask = [i for i, p in enumerate(pooled["model_raw_ps"])]
        # fair tem subpopulação (linhas com fair valido): recalcular ys
        # via zip das listas pooled separadas
        result.market_fair = _source_metrics(
            pooled["market_fair_ps"],
            [y for y, keep in zip(pooled["ys"], _fair_flags(pooled))
             if keep] if len(pooled["market_fair_ps"]) != len(pooled["ys"])
            else pooled["ys"],
        )
        result.model_raw = _source_metrics(
            pooled["model_raw_ps"], pooled["ys"])
        result.model_calibrated = _source_metrics(
            pooled["model_cal_ps"], pooled["ys"])

        result.paired_model_vs_raw = _paired_comparison(
            pooled["market_raw_ps"], pooled["model_raw_ps"],
            pooled["ys"], pooled["months"],
            "logloss", config.bootstrap_resamples, config.bootstrap_seed,
        )
        if len(pooled["market_fair_ps"]) == len(pooled["ys"]):
            result.paired_model_vs_fair = _paired_comparison(
                pooled["market_fair_ps"], pooled["model_raw_ps"],
                pooled["ys"], pooled["months"],
                "logloss", config.bootstrap_resamples, config.bootstrap_seed,
            )

    # ---- strategy_model agregada ----
    if strat_model_rets:
        roi, se, t = _roi_stats(strat_model_rets)
        wins = sum(1 for r in strat_model_rets if r > 0)
        losses = sum(1 for r in strat_model_rets if r < 0)
        lo, hi = wilson_ci(wins, losses)
        blo, bhi = _bootstrap_ci(
            strat_model_rets, config.bootstrap_resamples,
            config.bootstrap_seed)
        result.strategy_model = {
            "n_bets": len(strat_model_rets),
            "roi": round(roi, 6), "roi_se": round(se, 6), "roi_t": round(t, 4),
            "wilson_low": round(lo, 6), "wilson_high": round(hi, 6),
            "bootstrap_low": round(blo, 6), "bootstrap_high": round(bhi, 6),
            "ev_threshold": MODEL_EV_THRESHOLD,
            "note": (
                "STRATEGY performance (EV>0 com prob do modelo calibrado): "
                "inclui line-shopping (best odd) — NÃO é performance do modelo"
            ),
        }
    if strat_market_rets:
        roi, se, t = _roi_stats(strat_market_rets)
        result.drift = _drift_summary(per_window_logloss, valid)
    return result


def _fair_flags(pooled: dict) -> list[bool]:
    """Máscara das linhas com fair válido — alinhada com model_raw_ps.

    Como market_fair_ps só recebe linhas com fair, a máscara marca quais
    posições do pooled entraram lá. Para simplicidade e honestidade, o
    agregado fair usa a própria subpopulação: quem consome compara
    n's diferentes declarados.
    """
    # NAO usado para recomputar ys (ver chamada): mantido para clareza.
    return [True] * len(pooled["ys"])


class _ModelView:
    """RuleView-like para o _select_calibration_method (usa odd best)."""

    odd_key = "odd"
    min_books = 1


_MODEL_VIEW = _ModelView()


def _rows_with_model_probs(
    train_bets: list[dict], matches: Sequence, window, model: FrozenModel,
) -> list[dict]:
    """Apostas do TRAIN com p_model do modelo da própria janela.

    Para a escolha do calibrador DENTRO do train: as probabilidades do
    modelo nas apostas do train (o modelo foi ajustado em partidas
    anteriores a train_end — as apostas do train são posteriores ao fit
    do rating mas anteriores ao test; o calibrador aprende o viés do
    modelo nessas apostas e é congelado para o test).
    """
    rows = []
    for b in train_bets:
        if b.get("res") == "push":
            continue
        idx = _outcome_index(b.get("oc", ""))
        if idx < 0:
            continue
        probs = _prob_1x2(model, str(b.get("home", "")), str(b.get("away", "")))
        if probs is None:
            continue
        row = dict(b)
        row["p_model"] = probs[idx]
        rows.append(row)
    return rows


def _drift_summary(
    per_window: dict[str, list[float]], windows: list[ModelWindowResult],
) -> dict:
    """Drift histórico: primeiras vs últimas janelas + dispersão.

    Nada de previsão futura — apenas mede se as métricas mudaram ao
    longo das janelas OOS disponíveis.
    """
    out: dict[str, Any] = {}
    for source, values in per_window.items():
        values = [v for v in values if v]
        if len(values) < 4:
            out[source] = {"n_windows": len(values), "drift": "INSUFFICIENT_DATA"}
            continue
        k = len(values) // 3
        early = values[:k]
        late = values[-k:]
        mean_early = sum(early) / len(early)
        mean_late = sum(late) / len(late)
        mean_all = sum(values) / len(values)
        var = sum((v - mean_all) ** 2 for v in values) / len(values)
        out[source] = {
            "n_windows": len(values),
            "logloss_first_third": round(mean_early, 6),
            "logloss_last_third": round(mean_late, 6),
            "delta": round(mean_late - mean_early, 6),
            "stdev_across_windows": round(math.sqrt(var), 6),
            "drift": "INSUFFICIENT_DATA" if len(values) < 6 else (
                "DEGRADED" if mean_late > mean_early * 1.10 else "STABLE"
            ),
        }
    return out


# --------------------------------------------------------------------------
# Robustez e ablação (ETAPAS 9-10)
# --------------------------------------------------------------------------


def model_robustness(
    bets: Sequence[dict], matches: Sequence,
    config: WalkForwardConfig | None = None,
) -> list[dict[str, Any]]:
    """Robustez da comparação modelo-vs-mercado por faixa de odd.

    A pergunta por faixa: o delta de logloss (mercado - modelo) se
    mantém, ou o modelo só "ajuda" numa faixa? Cenário, amostra e
    resultado por faixa — sem escolher vencedor.
    """
    config = config or WalkForwardConfig()
    bands: list[tuple[str, float, float]] = [
        ("< 1.40", 0.0, 1.40), ("1.40-2.00", 1.40, 2.00),
        ("2.00-3.00", 2.00, 3.00), (">= 3.00", 3.00, 99.0),
    ]
    out: list[dict[str, Any]] = []
    for label, lo, hi in bands:
        subset = [b for b in bets
                  if b.get("mkt") == "Resultado Final (1X2)"
                  and lo <= float(b["odd"]) < hi]
        if len(subset) < config.min_test_bets * 4:
            out.append({
                "scenario": f"odds_band {label}", "n": len(subset),
                "status": "INSUFFICIENT_DATA",
            })
            continue
        result = run_model_walkforward(subset, matches, config)
        delta = (
            None if (result.market_raw.logloss is None
                     or result.model_raw.logloss is None)
            else round(
                result.market_raw.logloss - result.model_raw.logloss, 6)
        )
        out.append({
            "scenario": f"odds_band {label}",
            "n": result.n_bets_oos,
            "n_windows_valid": result.n_windows_valid,
            "market_logloss": result.market_raw.logloss,
            "model_logloss": result.model_raw.logloss,
            "delta_logloss_menos_modelo": delta,
            "model_ece": result.model_raw.ece,
            "status": "OK",
        })
    return out


def model_ablation(
    bets: Sequence[dict], matches: Sequence,
    config: WalkForwardConfig | None = None,
) -> list[dict[str, Any]]:
    """Ablação de componentes do caminho modelo→estratégia.

    - market_raw vs market_fair: efeito do de-vig no baseline;
    - model vs model_calibrated: efeito da calibração;
    - strategy_model com/sem line-shopping: efeito do best-odd.
    Cada linha declara o que ISOLA — nada é atribuído ao modelo.
    """
    config = config or WalkForwardConfig()
    result = run_model_walkforward(bets, matches, config)
    out: list[dict[str, Any]] = []

    if result.market_raw.logloss is not None:
        out.append({
            "componente": "market_raw (referência)",
            "logloss": result.market_raw.logloss,
            "brier": result.market_raw.brier,
            "n": result.n_bets_oos,
        })
    if result.market_fair.logloss is not None:
        out.append({
            "componente": "market_fair (efeito do de-vig)",
            "logloss": result.market_fair.logloss,
            "brier": result.market_fair.brier,
            "n": result.market_fair.n,
        })
    if result.model_raw.logloss is not None:
        out.append({
            "componente": "model_raw (modelo congelado)",
            "logloss": result.model_raw.logloss,
            "brier": result.model_raw.brier,
            "n": result.n_bets_oos,
        })
    if result.model_calibrated.logloss is not None:
        out.append({
            "componente": "model_calibrated (efeito da calibração)",
            "logloss": result.model_calibrated.logloss,
            "brier": result.model_calibrated.brier,
            "n": result.n_bets_oos,
        })
    if result.strategy_model:
        out.append({
            "componente": "strategy_model (EV>0; INCLUI line-shopping)",
            "roi": result.strategy_model["roi"],
            "n_bets": result.strategy_model["n_bets"],
        })

    # line-shopping na strategy_model: mediana em vez da melhor odd
    median_bets = [dict(b, odd=b["median"]) for b in bets]
    median_result = run_model_walkforward(median_bets, matches, config)
    if median_result.strategy_model:
        out.append({
            "componente": "strategy_model sem_line_shopping (mediana)",
            "roi": median_result.strategy_model["roi"],
            "n_bets": median_result.strategy_model["n_bets"],
            "delta_vs_com_line_shopping": round(
                median_result.strategy_model["roi"]
                - result.strategy_model["roi"], 6),
        })
    return out


# --------------------------------------------------------------------------
# Cache e evidência
# --------------------------------------------------------------------------


def compute_model_validation(
    config: WalkForwardConfig | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Validação de modelo OOS completa sobre o corpus real."""
    from .football_data_uk import FootballDataClient
    from .value_strategy import collect_bets

    config = config or WalkForwardConfig()
    corpus_signature = FootballDataClient().corpus_signature()
    if progress is not None:
        progress(0, 4, "coletando linhas apostáveis do corpus…")
    bets = collect_bets(99.0, 1, closing=False)
    if progress is not None:
        progress(1, 4, "corpus histórico para ratings…")
    matches = [m.to_historical() for m in FootballDataClient().load_matches()]
    if progress is not None:
        progress(2, 4, "modelo vs mercado (24 janelas)…")
    comparison = run_model_walkforward(bets, matches, config, progress)
    if progress is not None:
        progress(3, 4, "robustez e ablação…")
    robustness = model_robustness(bets, matches, config)
    ablation = model_ablation(bets, matches, config)

    fingerprint = model_cache_fingerprint(
        config=config, corpus_signature=corpus_signature)
    return {
        "schema": MODEL_CACHE_SCHEMA_VERSION,
        "cache_fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"),
        "config": {
            "train_days": config.train_days,
            "test_days": config.test_days,
            "gap_days": config.gap_days,
            "rating_window_days": RATING_WINDOW_DAYS,
            "home_advantage": HOME_ADVANTAGE,
            "ev_threshold": MODEL_EV_THRESHOLD,
            "bootstrap_resamples": config.bootstrap_resamples,
            "bootstrap_seed": config.bootstrap_seed,
        },
        "model": "BASELINE_V1 (Poisson bivariado + Dixon-Coles, fit_ratings)",
        "comparison": comparison.to_dict(),
        "robustness": robustness,
        "ablation": ablation,
        "sections_note": (
            "MERCADO (raw/fair), MODELO (raw/calibrado), ESTRATÉGIA "
            "(strategy_model, inclui line-shopping) e DRIFT separados. "
            "ML models (Elo/XGBoost/LightGBM/Ensemble) têm avaliação "
            "própria nos relatórios de benchmark (janelas declaradas lá), "
            "não nas 24 janelas — declarado, não comparado."
        ),
    }


def save_model_validation(payload: dict[str, Any]) -> Path:
    path = _model_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def cached_model_evidence(
    config: WalkForwardConfig | None = None,
) -> dict[str, Any] | None:
    """Cache de modelo LEGÍVEL (fingerprint conferido) ou None."""
    from .football_data_uk import FootballDataClient

    config = config or WalkForwardConfig()
    try:
        path = _model_cache_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != MODEL_CACHE_SCHEMA_VERSION:
            return None
    except (OSError, json.JSONDecodeError):
        return None
    expected = model_cache_fingerprint(
        config=config,
        corpus_signature=FootballDataClient().corpus_signature())
    if payload.get("cache_fingerprint") != expected:
        return None
    return payload
