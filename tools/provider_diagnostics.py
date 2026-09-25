"""BETGSN :: provider_diagnostics — diagnóstico estruturado dos providers.

Produz um `ProviderStatus` por provider OPERACIONAL (The Odds API,
ParlayAPI, OddsPapi) combinando:

  - configuração (chave presente? — NUNCA o valor)
  - health persistido no store (sucesso/falha/quota observados)
  - última observação real gravada

Sem inventar: um provider sem observação aparece como `unknown`, e quota
nunca é estimada quando o header não informa.

Uso:
    python tools/provider_diagnostics.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.odds_registry import default_odds_registry  # noqa: E402


@dataclass
class ProviderStatus:
    provider: str
    configured: bool
    operational: bool
    reachable: str            # "yes" | "no" | "unknown"
    authenticated: str        # "yes" | "no" | "unknown"
    quota_status: str         # "ok" | "exhausted" | "unknown"
    quota_remaining: int | None
    latency_ms: float | None
    last_success: str
    last_failure: str
    last_error: str
    available: bool
    timestamp_quality: str    # "quote" | "event" | "capture" | "unknown"

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "operational": self.operational,
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "quota_status": self.quota_status,
            "quota_remaining": self.quota_remaining,
            "latency_ms": self.latency_ms,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "last_error": self.last_error,
            "available": self.available,
            "timestamp_quality": self.timestamp_quality,
        }


_QUOTA_HINTS = (
    "out_of_usage_credits", "usage quota", "credit limit", "no_credits",
    "quota has been reached", "request limit exceeded", "request_limit_exceeded",
)
_INVALID_KEY_HINTS = ("invalid or inactive api key", "valid apikey", "invalid api key")


def _classify(record: dict) -> tuple[str, str, str]:
    """(reachable, authenticated, quota) a partir do health observado.

    401 pode significar chave inválida OU quota esgotada: distinguimos pelo
    corpo do erro. Nunca tratamos "sem quota" como "chave inválida" (e
    vice-versa) — o significado do dado muda.
    """
    state = str(record.get("state") or "").upper()
    kind = str(record.get("last_kind") or "").upper()
    status = record.get("last_status")
    error = str(record.get("last_error") or "").lower()
    if state == "HEALTHY":
        return "yes", "yes", "ok"
    if state in ("DEGRADED", "STALE", "NO_COVERAGE"):
        return "yes", "unknown", "unknown"
    if state == "UNAVAILABLE":
        if any(h in error for h in _QUOTA_HINTS):
            return "yes", "yes", "exhausted"
        if any(h in error for h in _INVALID_KEY_HINTS):
            return "no", "no", "unknown"
        if kind == "AUTH":
            return "no", "no", "unknown"
        if kind in ("FORBIDDEN", "NO_CREDITS"):
            return "yes", "yes", "exhausted"
        if status == 429:
            return "yes", "yes", "rate_limited"
        return "unknown", "unknown", "unknown"
    return "unknown", "unknown", "unknown"


def diagnostics() -> dict:
    from betgsn.odds_snapshots import OddsSnapshotStore

    registry = default_odds_registry()
    operational = {name for name, _p in registry.operational_providers()}
    configured = {name for name, _p in registry.available_providers()}
    legacy = set(registry.legacy_names())

    store = OddsSnapshotStore()
    health = store.load_provider_health()
    credits = store.load_provider_credits()
    # qualidade de timestamp: das observações reais, qual origem domina
    ts_quality: dict[str, str] = {}
    try:
        with store._lock:
            conn = store._conn()
            rows = conn.execute(
                """SELECT provider, timestamp_source, COUNT(*) n
                   FROM odds_observations GROUP BY provider, timestamp_source"""
            ).fetchall()
            conn.close()
        per_provider: dict[str, dict[str, int]] = {}
        for r in rows:
            per_provider.setdefault(r["provider"], {})[r["timestamp_source"] or "?"] = r["n"]
        for provider, counts in per_provider.items():
            best = max(counts.items(), key=lambda kv: kv[1])[0]
            ts_quality[provider] = {
                "QUOTE_TIMESTAMP": "quote",
                "EVENT_TIMESTAMP": "event",
                "CAPTURE_TIMESTAMP": "capture",
            }.get(best, "unknown")
    except Exception:  # noqa: BLE001
        pass

    statuses: dict[str, dict] = {}
    for name in sorted(configured | legacy):
        record = health.get(name, {})
        credit = credits.get(name, {})
        reachable, auth, quota = _classify(record)
        remaining = credit.get("remaining")
        if credit.get("exhausted"):
            quota = "exhausted"
        statuses[name] = ProviderStatus(
            provider=name,
            configured=name in configured,
            operational=name in operational,
            reachable=reachable,
            authenticated=auth,
            quota_status=quota,
            quota_remaining=remaining,
            latency_ms=record.get("latency_ms"),
            last_success=str(record.get("last_success_at") or ""),
            last_failure=str(record.get("last_failure_at") or ""),
            last_error=str(record.get("last_error") or "")[:200],
            available=bool(name in configured and name in operational),
            timestamp_quality=ts_quality.get(name, "unknown"),
        ).to_dict()

    return {
        "kind": "provider_diagnostics",
        "operational": sorted(operational),
        "legacy": sorted(legacy),
        "providers": statuses,
    }


def main() -> int:
    report = diagnostics()
    print("=== PROVIDER DIAGNOSTICS ===")
    print(f"  operacionais: {', '.join(report['operational']) or 'nenhum'}")
    print(f"  legacy      : {', '.join(report['legacy']) or 'nenhum'}")
    print()
    for name, s in report["providers"].items():
        print(f"  {name}")
        print(f"    configured={s['configured']} operational={s['operational']} "
              f"reachable={s['reachable']} auth={s['authenticated']} "
              f"quota={s['quota_status']} remaining={s['quota_remaining']}")
        print(f"    latency_ms={s['latency_ms']} ts_quality={s['timestamp_quality']}")
        if s["last_error"]:
            print(f"    last_error={s['last_error'][:120]}")
    out = ROOT / "output" / "engineering" / "quant" / "provider_diagnostics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nrelatorio: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
