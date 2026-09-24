"""Referência oficial do benchmark — congela a Etapa 19 sem recalcular.

Lê os caches fingerprintados (estratégia OOS, modelo OOS, full-sample),
confere os fingerprints contra o corpus atual e grava o manifesto em
output/engineering/quant/benchmark_manifest.json.

    VALID   = cache legível para a configuração atual sobre o corpus
              atual — a referência é reproduzível agora;
    STALE   = corpus/parâmetros mudaram — a medição precisa ser
              reexecutada pela tool offline correspondente;
    MISSING = cache ausente.

O manifesto não altera nenhum resultado histórico: é fotografia.

Uso:
    python tools/benchmark_manifest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.benchmark_manifest import build_benchmark_manifest  # noqa: E402


def _fmt(value) -> str:
    if value is None:
        return "n/d"
    if isinstance(value, float):
        return f"{value:+.4%}" if abs(value) < 1 else f"{value:.4f}"
    return str(value)


def main() -> int:
    manifest = build_benchmark_manifest()

    print("=== BENCHMARK MANIFEST (referência ETAPA_19) ===")
    print(f"  gerado em:        {manifest['generated_at']}")
    print(f"  código:           {manifest['code_version']}")
    corpus = manifest["corpus"]
    print(f"  corpus:           {corpus['signature']}")
    print(f"                    {corpus.get('main_files')} arquivos main, "
          f"{corpus.get('extra_files')} extras, "
          f"{corpus.get('total_mb')} MB")
    proto = manifest["protocol"]
    print(f"  protocolo:        train {proto['train_days']}d / "
          f"test {proto['test_days']}d / embargo {proto['gap_days_embargo']}d")
    print(f"                    bandas candidatas {proto['candidate_max_odds']}")
    print(f"                    bootstrap {proto['bootstrap_resamples']} "
          f"reamostragens, seed {proto['bootstrap_seed']}")
    print(f"                    regra: max_odd {proto['rule']['max_odd']}, "
          f"min_books {proto['rule']['min_books']}")

    if "windows" in manifest:
        w = manifest["windows"]
        print(f"  janelas:          {w['n_windows_valid']}/{w['n_windows']} "
              f"válidas | {w['n_bets_oos']} apostas OOS | "
              f"embargo {w['embargo_days']}d")

    print("\n  caches:")
    for name, entry in manifest["caches"].items():
        print(f"    {name:32s} {entry['status']:8s} "
              f"fp={entry['expected_fingerprint']}")
        agg = entry.get("aggregate")
        if isinstance(agg, dict) and "roi" in agg:
            print(f"      roi={_fmt(agg.get('roi'))} "
                  f"n={agg.get('n_bets_oos')}")

    repro = manifest["reproducibility"]
    print(f"\n  reprodutibilidade: {'SIM' if repro['all_valid'] else 'RUPTURADA'}")
    print(f"  {repro['note']}")

    from betgsn.config import output_root

    out = output_root() / "engineering" / "quant" / "benchmark_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nmanifesto: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
