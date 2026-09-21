"""Elo probabilístico independente: logits aprendidos no treino temporal."""
from sklearn.linear_model import LogisticRegression
from .base import probabilities


class EloModel:
    version = "ELO_V1_EXPERIMENTAL"

    def fit(self, train, validation=None):
        self.model = LogisticRegression(C=1, max_iter=500)
        self.model.fit(train.x, train.y)
        self.trained_until = train.times[-1]
        return self

    def predict_proba(self, x):
        return probabilities(self.model.predict_proba(x))
