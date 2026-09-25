"""BETGSN :: live_gate — ProductionGate de 7 blocos para o caminho LIVE.

Uma UNICA decisao de elegibilidade a producao, montada a partir da
evidencia REAL disponivel agora:

  DATA -> VALIDATION -> EVIDENCE -> PROMOTION -> PRODUCTION POLICY
       -> FINAL DECISION -> NO_BET | acao permitida

Nao cria uma segunda decisao paralela: a API operacional passa este gate
para `staking.decide_bet`, exatamente como `tools/quant_oos_validation.py`
faz offline. Ausencia de evidencia NAO aprova — o bloco fica RED/PENDING.

Blocos e como saem do estado atual:
  - MODEL       PENDING  modelo calibrado por janela ainda nao pareado ao live
  - CLV         GREEN/RED conforme evidencia prospectiva CLOSED
  - MARKET      PENDING  EVgap so e pareado por-sinal no live
  - EXECUTION   PENDING  execucao medida ainda nao coletada
  - ROBUSTNESS  GREEN/RED conforme cenarios OOS
  - PROVENANCE  PENDING  odds sem selo de execucao/publicacao
  - TEMPORAL    GREEN/RED conforme janelas OOS validas

`production_eligible` exige TODOS os sete GREEN com o fingerprint atual.
Enquanto qualquer bloco nao estiver GREEN, a decisao final e NO_BET.
"""
from __future__ import annotations

from .config import production_policy_fingerprint, production_thresholds
from .production_policy import GateBlock, ProductionGate


def _clv_block(limits: dict) -> GateBlock:
    from .value_walkforward import prospective_clv_evidence

    clv = prospective_clv_evidence() or {}
    n = int(clv.get("n") or 0)
    mean = clv.get("mean")
    median = clv.get("median")
    rate = clv.get("positive_rate")
    ok = (
        n >= int(limits["min_clv_sample"])
        and mean is not None and mean > 0
        and median is not None and median > 0
        and rate is not None and rate >= float(limits["min_beat_close"])
    )
    fmt = lambda v: "n/d" if v is None else f"{v:+.4%}"
    return GateBlock(
        "GREEN" if ok else "RED",
        (f"n={n}", f"mean={fmt(mean)}", f"median={fmt(median)}",
         f"beat_close={fmt(rate)}"),
    )


def _robustness_and_temporal(limits: dict) -> tuple[GateBlock, GateBlock]:
    from .value_walkforward import cached_oos_evidence

    oos = cached_oos_evidence()
    if oos is None:
        pending = GateBlock("PENDING", ("cache OOS ausente ou stale",))
        return pending, pending

    n_windows = int(getattr(oos, "n_windows_valid", 0) or 0)
    temporal = GateBlock(
        "GREEN" if n_windows >= int(limits["min_windows"]) else "RED",
        (f"{n_windows} janelas OOS validas",),
    )

    scenarios = getattr(oos, "robustness", None) or []
    def _rob_ok(sc: dict) -> bool:
        deg = sc.get("degradation_vs_baseline")
        n_w = int(sc.get("n_windows_valid") or 0)
        return (
            deg is not None
            and deg > -float(limits["max_execution_erosion"])
            and n_w >= int(limits["min_windows"])
        )
    ok_rows = [s for s in scenarios if _rob_ok(s)]
    robustness = GateBlock(
        "GREEN" if ok_rows else ("PENDING" if not scenarios else "RED"),
        (f"{len(ok_rows)}/{len(scenarios)} cenarios dentro do orcamento",),
    )
    return robustness, temporal


def build_live_gate() -> ProductionGate:
    """Gate operacional montado a partir da evidencia REAL do processo.

    Toda leitura e defensiva: evidencia ausente vira PENDING/RED, nunca
    GREEN. O gate nunca e None no caminho operacional — a decisao sempre
    o consulta.
    """
    limits = production_thresholds()
    blocks: dict[str, GateBlock] = {}

    blocks["MODEL"] = GateBlock(
        "PENDING",
        ("modelo calibrado por janela ainda nao pareado ao live",),
    )
    blocks["CLV"] = _clv_block(limits)
    blocks["MARKET"] = GateBlock(
        "PENDING", ("EVgap pareado por-sinal no live (fair_override)",),
    )
    blocks["EXECUTION"] = GateBlock(
        "PENDING", ("execucao medida ainda nao coletada (executed_price=null)",),
    )
    robustness, temporal = _robustness_and_temporal(limits)
    blocks["ROBUSTNESS"] = robustness
    blocks["TEMPORAL"] = temporal
    blocks["PROVENANCE"] = GateBlock(
        "PENDING", ("odds live sem selo de publicacao/execucao",),
    )
    return ProductionGate(blocks, production_policy_fingerprint())


__all__ = ["build_live_gate"]
