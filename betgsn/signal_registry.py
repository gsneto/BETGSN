"""BETGSN :: signal_registry — registro central de sinais e alphas.

Consolida, num único lugar auditável, o estado de cada sinal/alpha:

    RESEARCH            existe e é informativo; sem evidência de edge
    INSUFFICIENT_DATA   amostra insuficiente para decidir
    EXPERIMENTAL        implementado, aguardando OOS
    VALIDATED           sobreviveu ao OOS com efeito consistente
    FRAGILE             efeito existe mas não é estável entre dimensões
    NO_EVIDENCE         efeito indistinguível do nulo
    REJECTED            testado e rejeitado
    BLOCKED             falta dado (closing/resultado/in-play)
    PRODUCTION_CANDIDATE  passou todos os gates; ainda NO_BET
    PRODUCTION          elegível a produção (nunca alcançado sem gate)

O registry NÃO promove por declaração: `PRODUCTION` exige evidência
completa (OOS + CLV n>=200 + execução medida + robustez). Enquanto isso,
o produto segue NO_BET.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

STATUS_ORDER = (
    "REJECTED", "BLOCKED", "NO_EVIDENCE", "RESEARCH", "INSUFFICIENT_DATA",
    "EXPERIMENTAL", "FRAGILE", "VALIDATED", "PRODUCTION_CANDIDATE", "PRODUCTION",
)

#: Status que exigem evidência completa antes de existir.
PRODUCTION_STATUSES = frozenset({"PRODUCTION_CANDIDATE", "PRODUCTION"})


@dataclass
class SignalRegistryEntry:
    signal_id: str
    version: str
    status: str
    market: tuple[str, ...]
    required_books: int
    freshness: str
    threshold: str
    calibration: str
    sample_size: int = 0
    clv_status: str = "BLOCKED"
    oos_status: str = "NOT_RUN"
    execution_status: str = "UNKNOWN"
    robustness: str = "NOT_RUN"
    fingerprint: str = ""
    last_updated: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "version": self.version,
            "status": self.status,
            "market": list(self.market),
            "required_books": self.required_books,
            "freshness": self.freshness,
            "threshold": self.threshold,
            "calibration": self.calibration,
            "sample_size": self.sample_size,
            "clv_status": self.clv_status,
            "oos_status": self.oos_status,
            "execution_status": self.execution_status,
            "robustness": self.robustness,
            "fingerprint": self.fingerprint,
            "last_updated": self.last_updated,
            "notes": list(self.notes),
        }


def build_registry(
    *,
    alpha_evaluations: dict | None = None,
    clv_evidence: dict | None = None,
    execution_gap: dict | None = None,
    fingerprint: str = "",
    now: str = "",
) -> dict[str, SignalRegistryEntry]:
    """Monta o registry a partir das evidências REAIS disponíveis.

    `alpha_evaluations` mapeia signal_type -> AlphaEvaluation.to_dict().
    `clv_evidence` é o resumo prospectivo do store (n/mean/median/beat-close).
    `execution_gap` é o resumo de execution (n/ratio/status).
    """
    from .alpha_lab import default_alpha_registry

    evals = alpha_evaluations or {}
    clv = clv_evidence or {}
    exec_gap = execution_gap or {}
    n_closed = int(clv.get("n") or 0)
    clv_ok = (
        n_closed >= 200
        and (clv.get("mean") or 0) > 0
        and (clv.get("median") or 0) > 0
        and (clv.get("positive_rate") or 0) >= 0.55
    )
    clv_status = "GREEN" if clv_ok else ("RED" if n_closed == 0 else "PENDING")
    exec_status = exec_gap.get("status", "UNKNOWN")

    registry: dict[str, SignalRegistryEntry] = {}
    for alpha_id, spec in default_alpha_registry().items():
        for signal_type in spec.signal_types or (alpha_id,):
            ev = evals.get(signal_type, {})
            n = int(ev.get("n") or 0)
            ev_status = ev.get("status")
            # status do sinal: o veredito da avaliação quando existe; senão
            # o status DECLARADO de suporte de dados da spec.
            if ev_status:
                status = ev_status
            elif spec.status == "BLOCKED":
                status = "BLOCKED"
            else:
                status = "RESEARCH"

            # PRODUCTION_CANDIDATE exige evidência completa — nunca por
            # declaração. Hoje nenhum sinal satisfaz (CLV n=0).
            if status == "VALIDATED" and clv_ok and exec_status == "MEASURED":
                status = "PRODUCTION_CANDIDATE"

            registry[signal_type] = SignalRegistryEntry(
                signal_id=signal_type,
                version=spec.version,
                status=status,
                market=tuple(spec.markets),
                required_books=spec.required_books,
                freshness=spec.freshness_requirement,
                threshold=(
                    "SignalRules defaults (não calibrado)"
                    if not ev else "SignalRules defaults (não calibrado)"
                ),
                calibration="INSUFFICIENT_DATA",
                sample_size=n,
                clv_status=clv_status,
                oos_status="NOT_RUN",
                execution_status=exec_status,
                robustness=(
                    "CONSISTENT" if ev.get("status") == "VALIDATED"
                    else "NOT_RUN"
                ),
                fingerprint=fingerprint,
                last_updated=now,
                notes=tuple(ev.get("limitations") or (spec.hypothesis,)),
            )
    return registry


def registry_to_dict(registry: dict[str, SignalRegistryEntry]) -> dict:
    return {sid: entry.to_dict() for sid, entry in sorted(registry.items())}


def save_registry(registry: dict[str, SignalRegistryEntry], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "kind": "signal_registry",
                "statuses": list(STATUS_ORDER),
                "signals": registry_to_dict(registry),
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


__all__ = [
    "SignalRegistryEntry",
    "build_registry",
    "registry_to_dict",
    "save_registry",
    "STATUS_ORDER",
    "PRODUCTION_STATUSES",
]
