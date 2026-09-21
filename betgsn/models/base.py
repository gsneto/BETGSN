"""Contratos e validação de cortes de treino/validação/teste."""
from dataclasses import dataclass
import numpy as np
from ..timeutil import utc_key

OUTCOMES = {"1x2": ("1", "X", "2"), "ou": ("Under 2.5", "Over 2.5"),
            "btts": ("BTTS Nao", "BTTS Sim")}
MARKETS = {"1x2": "Resultado Final (1X2)", "ou": "Total de Gols", "btts": "Ambas Marcam"}


@dataclass(frozen=True)
class TemporalBatch:
    x: object
    y: object
    times: tuple[str, ...]

    def __post_init__(self):
        if not len(self.times) or len(self.x) != len(self.y) or len(self.x) != len(self.times):
            raise ValueError("batch vazio ou desalinhado")
        if list(self.times) != sorted(self.times):
            raise ValueError("batch deve ser temporalmente ordenado")


def separated(train, validation):
    if utc_key(train.times[-1]) >= utc_key(validation.times[0]):
        raise ValueError("treino/validação devem ser temporalmente disjuntos")


def probabilities(p):
    p = np.asarray(p, dtype=float)
    if p.ndim != 2 or not np.isfinite(p).all() or (p < 0).any() or (p.sum(axis=1) <= 0).any():
        raise ValueError("probabilidades inválidas")
    return p / p.sum(axis=1, keepdims=True)


def matrix(snapshots, columns=None):
    columns = columns or sorted(snapshots[0].values)
    return np.array([[s.values.get(c) if s.values.get(c) is not None else np.nan for c in columns]
                     for s in snapshots]), columns
