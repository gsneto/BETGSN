"""BETGSN :: data_quality — Comparação e validação entre fontes de dados.

Detecta conflitos, registra proveniência e nunca corrige silenciosamente.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass(frozen=True)
class FieldComparison:
    """Comparison of a single field across providers."""
    field: str
    values: dict[str, Any]     # {provider: value}
    consensus: Optional[Any] = None
    status: str = "UNKNOWN"    # CONSISTENT, CONFLICT, SINGLE_SOURCE, MISSING
    
    @staticmethod
    def compare(field_name: str, provider_values: dict[str, Any]) -> "FieldComparison":
        non_null = {k: v for k, v in provider_values.items() if v is not None}
        if not non_null:
            return FieldComparison(field_name, provider_values, status="MISSING")
        unique_values = set(str(v) for v in non_null.values())
        if len(non_null) == 1:
            val = next(iter(non_null.values()))
            return FieldComparison(field_name, provider_values, consensus=val, status="SINGLE_SOURCE")
        if len(unique_values) == 1:
            val = next(iter(non_null.values()))
            return FieldComparison(field_name, provider_values, consensus=val, status="CONSISTENT")
        return FieldComparison(field_name, provider_values, status="CONFLICT")

@dataclass
class MatchQualityReport:
    """Quality report for one match comparing data across providers."""
    match_key: str
    providers: list[str] = field(default_factory=list)
    comparisons: list[FieldComparison] = field(default_factory=list)
    
    @property
    def has_conflicts(self) -> bool:
        return any(c.status == "CONFLICT" for c in self.comparisons)
    
    @property
    def conflict_fields(self) -> list[str]:
        return [c.field for c in self.comparisons if c.status == "CONFLICT"]
    
    @property
    def coverage(self) -> dict[str, int]:
        """How many fields each provider contributed."""
        result: dict[str, int] = {p: 0 for p in self.providers}
        for c in self.comparisons:
            for p in c.values:
                if c.values[p] is not None:
                    result[p] = result.get(p, 0) + 1
        return result
    
    def summary(self) -> dict:
        return {
            "match_key": self.match_key,
            "n_providers": len(self.providers),
            "n_fields": len(self.comparisons),
            "consistent": sum(1 for c in self.comparisons if c.status == "CONSISTENT"),
            "conflicts": sum(1 for c in self.comparisons if c.status == "CONFLICT"),
            "single_source": sum(1 for c in self.comparisons if c.status == "SINGLE_SOURCE"),
            "missing": sum(1 for c in self.comparisons if c.status == "MISSING"),
        }

def compare_match_data(match_key: str, 
                       provider_data: dict[str, dict[str, Any]]) -> MatchQualityReport:
    """Compare match data across multiple providers.
    
    provider_data: {provider_name: {field_name: value}}
    """
    providers = sorted(provider_data.keys())
    all_fields = sorted({f for data in provider_data.values() for f in data})
    
    comparisons = []
    for field_name in all_fields:
        values = {p: provider_data[p].get(field_name) for p in providers}
        comparisons.append(FieldComparison.compare(field_name, values))
    
    return MatchQualityReport(
        match_key=match_key,
        providers=providers,
        comparisons=comparisons,
    )

@dataclass(frozen=True)
class ProviderHealth:
    """Health status of a data provider."""
    name: str
    status: str = "unknown"     # ok, degraded, error, disabled
    last_success: str = ""
    last_error: str = ""
    error_message: str = ""
    requests_today: int = 0
    requests_remaining: int = 0
    coverage: dict[str, bool] = field(default_factory=dict)  # {feature: available}
    features: tuple[str, ...] = ()
    reliability_pct: float = 100.0
