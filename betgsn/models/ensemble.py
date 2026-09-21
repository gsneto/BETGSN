"""Mistura convexa ajustada em previsões OOS anteriores ao teste."""
import numpy as np
from scipy.optimize import minimize
from .base import probabilities
from ..timeutil import utc_key


class WeightedEnsemble:
    version = "ENSEMBLE_V1_EXPERIMENTAL"

    def fit(self, predictions, y, times, base_trained_until):
        if utc_key(base_trained_until) >= utc_key(min(times)):
            raise ValueError("ensemble exige previsões out-of-sample")
        self.names = tuple(sorted(predictions))
        stack = np.array([probabilities(predictions[n]) for n in self.names])
        n = len(self.names)
        y = np.asarray(y, dtype=int)
        def loss(w):
            p = np.tensordot(w, stack, axes=1)
            return -np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1)).mean() + .001 * np.sum(w*w)
        result = minimize(loss, np.ones(n)/n, bounds=[(0, 1)]*n,
                          constraints={"type": "eq", "fun": lambda w: w.sum()-1}, method="SLSQP")
        if not result.success:
            raise ValueError(f"ajuste ensemble falhou: {result.message}")
        self.weights = result.x / result.x.sum()
        self.trained_until = max(times)
        return self

    def predict_proba(self, predictions, prediction_time):
        if utc_key(prediction_time) <= utc_key(self.trained_until):
            raise ValueError("ensemble não pode prever seu próprio treino")
        return probabilities(sum(w * probabilities(predictions[n]) for n, w in zip(self.names, self.weights)))
