"""Calibração aplicada exclusivamente após treino e antes do teste."""
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from .base import probabilities
from ..timeutil import utc_key


class TemporalCalibrator:
    def __init__(self, method="platt"):
        if method not in ("platt", "isotonic"):
            raise ValueError("calibração desconhecida")
        self.method = method
        self.version = f"{method.upper()}_V1_EXPERIMENTAL"

    def fit(self, p, y, times, base_trained_until):
        if utc_key(base_trained_until) >= utc_key(min(times)):
            raise ValueError("calibração requer probabilidades OOS")
        p = probabilities(p)
        y = np.asarray(y)
        if len(y) != len(p) or len(y) != len(times):
            raise ValueError("calibração desalinhada")
        self.models = []
        for c in range(p.shape[1]):
            target = (y == c).astype(int)
            if len(set(target)) < 2:
                raise ValueError("calibração precisa exemplos positivos e negativos")
            if self.method == "isotonic":
                model = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1-1e-6)
                model.fit(p[:, c], target)
            else:
                model = LogisticRegression(C=1, max_iter=500)
                model.fit(self._logit(p[:, c]), target)
            self.models.append(model)
        self.trained_until = max(times)
        return self

    @staticmethod
    def _logit(p):
        p = np.clip(p, 1e-6, 1-1e-6)
        return np.log(p/(1-p)).reshape(-1, 1)

    def predict_proba(self, p, prediction_time):
        if utc_key(prediction_time) <= utc_key(self.trained_until):
            raise ValueError("calibração não pode usar o próprio conjunto de teste")
        p = probabilities(p)
        cols = [m.predict(p[:, c]) if self.method == "isotonic" else
                m.predict_proba(self._logit(p[:, c]))[:, 1] for c, m in enumerate(self.models)]
        return probabilities(np.column_stack(cols))
