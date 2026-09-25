"""Relatório de monitoramento do CLV prospectivo (ETAPA 11 do ciclo de evidência).

Lê EXCLUSIVAMENTE o caminho prospectivo do store operacional:

    entradas registradas no instante da decisão (FIRST-WINS, guardas PIT)
    → fechamento dentro da janela (closing_line)
    → clv_prospective (entry < closing < kickoff)

Reporta: n, média, mediana, distribuição, cobertura, período, partidas e
bookmakers observados. Sem interpretação automática de "vantagem":

    n < MIN_CLV_SAMPLE (200)  → BLOCKED (o Promotion Gate não pode consumir)
    n >= MIN_CLV_SAMPLE       → READY (pode alimentar o gate — nunca promove
                                sozinho)

CLV válido exige CLOSED + closing_price real + closing_timestamp válido.
PENDING / NO_CLOSE / INVALID / MISMATCH NÃO contam. n=0 aparece como
n=0, mean=null — nunca como zero.

Uso:
    python tools/clv_report.py
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.odds_snapshots import OddsSnapshotStore  # noqa: E402
from betgsn.value_walkforward import prospective_clv_evidence  # noqa: E402

#: MESMO limite do Promotion Gate — importado de `models.promotion`, que
#: por sua vez le `production_thresholds()['min_clv_sample']` (=200). Nao
#: redefinir aqui: dois numeros que divergem deixariam o monitor dizer
#: READY enquanto o gate ainda exige amostra maior (divergencia corrigida).
from betgsn.models.promotion import MIN_CLV_SAMPLE  # noqa: E402


def _pct(x: float) -> str:
    return f"{x:+.2%}" if x is not None else "n/d"


def build_report(store: OddsSnapshotStore | None = None) -> dict:
    store = store or OddsSnapshotStore()
    evidence = prospective_clv_evidence(store)

    # ciclo de vida operacional: PENDING / NO_CLOSE / CLOSED / INVALID /
    # MISMATCH (leitura idempotente — rodar duas vezes nao cria nada)
    sweep = store.clv_lifecycle_sweep()
    lifecycle = sweep.by_state

    # distribuição e cobertura a partir das entradas reais
    entries = store.clv_entries()
    results = []
    for lc in sweep.lifecycles:
        if lc.result is not None:
            results.append((lc.entry, lc.result))

    valid = [(rec, r) for rec, r in results if r.status == "OK"]
    pcts = [float(r.clv_percentage) for _, r in valid]
    no_closing = lifecycle["PENDING"] + lifecycle["NO_CLOSE"]
    before_entry = sum(
        1 for _, r in results if r.status == "CLOSING_BEFORE_ENTRY")

    matches = sorted({rec.match_key for rec in entries})
    kickoffs = sorted({rec.kickoff for rec in entries})
    books = sorted({
        r.closing_bookmaker for _, r in valid if r.closing_bookmaker
    })

    def _quantile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(q * len(ordered)))
        return ordered[idx]

    n = len(pcts)
    if not n:
        evidence = dict(evidence, mean=None)
    status = "BLOCKED" if n < MIN_CLV_SAMPLE else "READY"
    return {
        "kind": "clv_prospective_monitor",
        "status": status,
        "clv": evidence,
        "n_entries": len(entries),
        "n_valid": n,
        "n_no_closing": no_closing,
        "n_closing_before_entry": before_entry,
        "lifecycle": lifecycle,
        "lifecycle_note": (
            "PENDING = kickoff no futuro (fechamento ainda pode chegar); "
            "NO_CLOSE = kickoff passou sem fechamento valido; CLOSED = CLV "
            "calculado; INVALID = dado inconsistente; MISMATCH = entrada "
            "sem observacao correspondente. Ausencia de fechamento nunca "
            "vira CLV=0."
        ),
        "mean": (statistics.fmean(pcts) if pcts else None),
        "median": (statistics.median(pcts) if pcts else None),
        "p10": _quantile(pcts, 0.10),
        "p25": _quantile(pcts, 0.25),
        "p75": _quantile(pcts, 0.75),
        "p90": _quantile(pcts, 0.90),
        "positive_rate": (
            sum(1 for p in pcts if p > 0) / len(pcts) if pcts else None
        ),
        "coverage": (
            n / len(entries) if entries else None
        ),
        "n_matches": len(matches),
        "period": {
            "first_kickoff": kickoffs[0] if kickoffs else None,
            "last_kickoff": kickoffs[-1] if kickoffs else None,
        },
        "closing_bookmakers": books,
        "note": (
            "CLV prospectivo: entradas registradas no instante da decisão "
            "(FIRST-WINS), fechamento posterior ao entry, ambos anteriores "
            "ao kickoff. Nada de CLV retrospectivo."
        ),
        "gate": (
            f"n={n} < {MIN_CLV_SAMPLE}: Promotion Gate permanece BLOCKED "
            "por amostra insuficiente (MIN_CLV_SAMPLE)"
            if n < MIN_CLV_SAMPLE else
            f"n={n} >= {MIN_CLV_SAMPLE}: pode alimentar o Promotion Gate "
            "(decisão continua sendo do gate, nunca automática)"
        ),
    }


def print_report(report: dict) -> None:
    print("=== CLV PROSPECTIVO — monitoramento ===")
    print(f"  status: {report['status']}")
    print(f"  entradas registradas: {report['n_entries']}")
    print(f"  com CLV válido:       {report['n_valid']}"
          f"  (cobertura {report['coverage']:.0%})"
          if report["coverage"] is not None else
          f"  com CLV válido:       {report['n_valid']}")
    print(f"  sem fechamento:       {report['n_no_closing']}"
          f"  | fech. antes do entry: {report['n_closing_before_entry']}")
    if report["n_valid"]:
        print(f"  média: {_pct(report['mean'])}  "
              f"mediana: {_pct(report['median'])}  "
              f"positivas: {report['positive_rate']:.0%}")
        print(f"  distribuição: p10 {_pct(report['p10'])} | "
              f"p25 {_pct(report['p25'])} | p75 {_pct(report['p75'])} | "
              f"p90 {_pct(report['p90'])}")
    period = report["period"]
    print(f"  partidas: {report['n_matches']}  "
          f"kickoffs: {period['first_kickoff']} .. {period['last_kickoff']}")
    print(f"  casas de fechamento: {', '.join(report['closing_bookmakers']) or 'n/d'}")
    print(f"\n  GATE: {report['gate']}")


def main() -> int:
    report = build_report()
    print_report(report)
    from betgsn.config import output_root

    out = output_root() / "engineering" / "quant" / "clv_monitor.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nrelatório: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
