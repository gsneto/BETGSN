"""BETGSN :: research_daily — orquestrador idempotente de pesquisa.

Executa, em ordem, os passos que constroem evidência prospectiva:

    1. capture (quota-aware)      — pula se providers em cooldown
    2. clv lifecycle sweep        — tools/clv_report.py
    3. alpha lab run              — tools/alpha_lab_run.py

Todos os passos são idempotentes e NUNCA fabricam dado. Se a captura
estiver bloqueada por quota, os passos locais (sweep + alpha lab) seguem
com o store acumulado. Exit code:

    0 = tudo o que era possível rodou
    2 = falha real (exceção) em algum passo local

Uso:
    python tools/research_daily.py [--skip-capture] [--stride=1800]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _run(label: str, args: list[str]) -> tuple[int, float]:
    print(f"\n=== {label} ===")
    started = time.time()
    proc = subprocess.run(
        [PY, *args], cwd=str(ROOT), text=True, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - started
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # imprime só o essencial (o full fica nos artefatos)
    tail = "\n".join(out.splitlines()[-20:])
    if tail:
        print(tail)
    if err:
        print(err[-800:], file=sys.stderr)
    print(f"  exit={proc.returncode} ({elapsed:.0f}s)")
    return proc.returncode, elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--stride", type=int, default=1800)
    parser.add_argument("--markets", default="1x2,ou,btts")
    args = parser.parse_args(argv)

    failures: list[str] = []

    if not args.skip_capture:
        rc, _ = _run("CAPTURE (quota-aware)",
                     ["betgsn.py", "--capture-odds"])
        # rc=0 inclui WAITING_FOR_PROVIDER_QUOTA (não é falha).
        if rc not in (0, 1, 2):
            failures.append(f"capture exit={rc}")

    rc, _ = _run("CLV LIFECYCLE SWEEP", ["tools/clv_report.py"])
    if rc != 0:
        failures.append(f"clv_report exit={rc}")

    rc, _ = _run("ALPHA LAB RUN",
                 ["tools/alpha_lab_run.py", f"--stride={args.stride}",
                  f"--markets={args.markets}"])
    if rc != 0:
        failures.append(f"alpha_lab_run exit={rc}")

    if failures:
        print("\nFALHAS:", "; ".join(failures))
        return 2
    print("\nRESEARCH DAILY: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
