"""BETGSN :: alpha_lab_run — executa o laboratório e grava artefatos.

Fluxo (point-in-time, sem fabricar dado):

    store (observações reais)
      → replay PIT dos sinais (thresholds de produção)
      → observação forward (movimento 5/15/30/60m + closing)
      → avaliação estatística (n, IC bootstrap, consistência)
      → market audit (RAW vs FAIR por mercado)
      → CLV prospectivo + execution gap
      → signal registry
      → artefatos em output/engineering/quant/alpha_lab/

Artefatos:
    alpha_registry.json         hipóteses + suporte de dados
    alpha_evaluations.json      veredito por sinal
    market_audit.json           MARKET_RAW vs MARKET_FAIR
    clv_evidence.json           CLV prospectivo (n/mean/median/beat-close)
    execution_gap.json          execution gap/erosão
    signal_registry.json        status central por sinal
    robustness_report.json      consistência por dimensão
    promotion_report.json       gate de promoção consolidado

Uso:
    python tools/alpha_lab_run.py [--stride=1800] [--markets=1x2,ou,btts]
                                  [--max-matches=N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.alpha_lab import (  # noqa: E402
    default_alpha_registry,
    evaluate_all,
    line_shopping_ablation,
)
from betgsn.alpha_replay import replay_signals  # noqa: E402
from betgsn.clv_dataset import clv_dataset, clv_progress  # noqa: E402
from betgsn.market_audit import audit_all_markets  # noqa: E402
from betgsn.market_dataset import dataset_fingerprint  # noqa: E402
from betgsn.odds_normalize import NormalizedQuote  # noqa: E402
from betgsn.odds_snapshots import OddsSnapshotStore  # noqa: E402
from betgsn.quota_scheduler import QuotaScheduler  # noqa: E402
from betgsn.signal_registry import build_registry, registry_to_dict  # noqa: E402
from betgsn.timeutil import now_utc  # noqa: E402

OUT = ROOT / "output" / "engineering" / "quant" / "alpha_lab"

MARKET_KEYS = {
    "1x2": "Resultado Final (1X2)",
    "ou": "Total de Gols",
    "btts": "Ambas Marcam",
}


def _quotes_from_store(store: OddsSnapshotStore, markets) -> list[NormalizedQuote]:
    quotes: list[NormalizedQuote] = []
    for o in store.observations_for_markets(markets):
        quotes.append(NormalizedQuote(
            event_id=o.match_key, provider=o.provider or "store",
            sport_key="", league="", home_team="", away_team="",
            kickoff=o.kickoff, bookmaker=o.bookmaker, market=o.market,
            selection=o.outcome, price=o.odd, timestamp=o.timestamp, line=None,
        ))
    return quotes


def _execution_gap(store: OddsSnapshotStore) -> dict:
    from betgsn.priced_signals import execution_diagnostics, execution_erosion

    executions = store.load_executions()
    rows = []
    for (_mk, _mkt, _oc), rec in executions.items():
        rows.append(execution_diagnostics(
            rec.get("observed_price"), rec["executed_price"], None,
            executed_at=rec["executed_at"],
            decision_timestamp=rec.get("decision_timestamp"),
        ))
    erosion = execution_erosion(rows)
    measured = sum(1 for r in rows if r["execution_status"] == "EXECUTED")
    return {
        "n_executions": len(executions),
        "n_measured": measured,
        "status": erosion["status"],
        "erosion_ratio": erosion["ratio"],
        "clv_before": erosion["clv_before"],
        "clv_after": erosion["clv_after"],
        "note": (
            "Sem execução registrada, o gap é UNKNOWN — nunca presumido. "
            "Erosão > 50% marca EXECUTION_EROSION e impede promoção."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stride", type=int, default=1800,
                        help="stride (s) entre instantes de decisão")
    parser.add_argument("--markets", default="1x2,ou,btts")
    parser.add_argument("--max-matches", type=int, default=0)
    args = parser.parse_args(argv)

    markets = [MARKET_KEYS[k] for k in args.markets.split(",") if k in MARKET_KEYS]
    store = OddsSnapshotStore()

    print("=== ALPHA LAB RUN ===")
    print(f"  mercados: {', '.join(markets)}")
    print(f"  stride  : {args.stride}s")

    started = time.time()
    observations = replay_signals(
        store, markets=markets,
        max_matches=(args.max_matches or None),
        decision_stride_seconds=args.stride,
    )
    print(f"  observações: {len(observations)} ({time.time()-started:.0f}s)")

    evaluations = evaluate_all(observations)

    # fingerprint do dataset
    events = {o.event_key for o in observations}
    books = {o.bookmaker for o in observations}
    periods = sorted({o.signal_timestamp for o in observations})
    fingerprint = dataset_fingerprint(
        event_keys=events, markets=markets, bookmakers=books,
        n_observations=len(observations),
        period=(periods[0], periods[-1]) if periods else ("", ""),
    )

    # market audit
    quotes = _quotes_from_store(store, markets)
    audit = {m: a.to_dict() for m, a in audit_all_markets(quotes, markets).items()}

    # CLV prospectivo + progresso + dataset + execução
    from betgsn.value_walkforward import prospective_clv_evidence

    clv = prospective_clv_evidence(store)
    clv_prog = clv_progress(store)
    clv_ds = clv_dataset(store)
    execution = _execution_gap(store)
    ablation = line_shopping_ablation(observations)

    # estado de captura (quota-aware)
    scheduler = QuotaScheduler.from_store(store)
    operational = ["The Odds API", "ParlayAPI", "OddsPapi"]
    readiness = scheduler.overall(operational)
    external_block = readiness["status"] == "WAITING_FOR_PROVIDER_QUOTA"

    registry = build_registry(
        alpha_evaluations={k: v.to_dict() for k, v in evaluations.items()},
        clv_evidence=clv,
        execution_gap=execution,
        fingerprint=fingerprint,
        now=now_utc(),
    )

    OUT.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "generated_at": now_utc(),
        "dataset_fingerprint": fingerprint,
        "n_observations": len(observations),
        "stride_seconds": args.stride,
        "markets": markets,
        "max_matches": args.max_matches,
    }

    def _write(name: str, payload: dict) -> None:
        (OUT / name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    _write("alpha_registry.json", {
        "kind": "alpha_registry", **run_meta,
        "alphas": {k: v.to_dict() for k, v in default_alpha_registry().items()}})
    _write("alpha_evaluations.json", {
        "kind": "alpha_evaluations", **run_meta,
        "evaluations": {k: v.to_dict() for k, v in evaluations.items()}})
    _write("market_audit.json", {"kind": "market_audit", **run_meta,
                                 "markets": audit})
    _write("clv_evidence.json", {"kind": "clv_evidence", **run_meta,
                                 "clv": clv, "progress": clv_prog})
    _write("clv_dataset.json", {"kind": "clv_dataset", **run_meta,
                                "fingerprint_clv": clv_ds["fingerprint"],
                                "progress": clv_ds["progress"],
                                "rows": clv_ds["rows"]})
    _write("execution_gap.json", {"kind": "execution_gap", **run_meta,
                                  "execution": execution})
    _write("signal_registry.json", {"kind": "signal_registry", **run_meta,
                                    "signals": registry_to_dict(registry)})
    _write("robustness_report.json", {
        "kind": "robustness_report", **run_meta,
        "by_signal": {
            k: {
                "consistency": v.consistency,
                "by_market": v.by_market,
                "by_league": v.by_league,
                "by_book": v.by_book,
                "by_odds_band": v.by_odds_band,
                "by_ttk_band": v.by_ttk_band,
                "by_gap_band": v.by_gap_band,
                "by_book_band": v.by_book_band,
            }
            for k, v in evaluations.items()
        }})
    _write("line_shopping_ablation.json", {
        "kind": "line_shopping_ablation", **run_meta, "ablation": ablation})
    _write("capture_readiness.json", {
        "kind": "capture_readiness", **run_meta, "readiness": readiness})

    clv_ok = (
        int(clv.get("n") or 0) >= 200 and (clv.get("mean") or 0) > 0
        and (clv.get("median") or 0) > 0 and (clv.get("positive_rate") or 0) >= 0.55
    )
    production_eligible = (
        clv_ok and execution["status"] == "MEASURED"
        and any(v.status == "VALIDATED" for v in evaluations.values())
    )
    if external_block:
        verdict = "BLOCKED_EXTERNAL_PROVIDER_QUOTA"
    elif production_eligible:
        verdict = "REVIEW"
    else:
        verdict = "NO_BET"
    _write("promotion_report.json", {
        "kind": "promotion_report", **run_meta,
        "clv_ok": clv_ok,
        "clv_n": clv.get("n"),
        "clv_closed": clv_prog["closed"],
        "clv_target": clv_prog["target"],
        "execution_status": execution["status"],
        "validated_alphas": [k for k, v in evaluations.items()
                             if v.status == "VALIDATED"],
        "production_eligible": production_eligible,
        "capture_readiness": readiness["status"],
        "verdict": verdict,
        "note": (
            "production_eligible exige OOS + CLV n>=200 + execução medida. "
            "Enquanto faltar qualquer gate, NO_BET. Sem quota de provider, "
            "BLOCKED_EXTERNAL_PROVIDER_QUOTA (o store acumulado segue "
            "analisável)."
        ),
    })

    print("\n=== VEREDITO POR SINAL ===")
    for signal_type, ev in sorted(evaluations.items()):
        metric = (f"{ev.primary_label}={ev.primary_metric:.4f}"
                  if ev.primary_metric is not None else "n/d")
        ci = (f"[{ev.ci_low:.4f},{ev.ci_high:.4f}]"
              if ev.ci_low is not None else "n/d")
        print(f"  {signal_type:22s} n={ev.n:5d} {metric} CI={ci} -> {ev.status}")
    print("\n=== CLV ===")
    print(f"  closed={clv_prog['closed']}/{clv_prog['target']} "
          f"pending={clv_prog['pending']} no_close={clv_prog['no_close']} "
          f"invalid={clv_prog['invalid']} -> {clv_prog['status']}")
    print(f"  EXECUTION: {execution['status']} "
          f"(n_measured={execution['n_measured']})")
    print(f"  CAPTURE: {readiness['status']} "
          f"(blocked={readiness['blocked']})")
    print(f"  VERDICT: {verdict}")
    print(f"\nartefatos: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
