"""Calibradores pós-modelo ajustados em previsões internas OOS, nunca odds."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from .production_policy import finite_number
from .timeutil import utc_key

METHODS = ('raw', 'platt', 'temperature', 'isotonic')
TEMPERATURES = (.5, .75, 1., 1.25, 1.5, 2., 3.)


@dataclass(frozen=True)
class CalibrationRow:
    p_model: float
    y: int
    prediction_time: str
    label_available_at: str
    model_trained_until: str
    market: str
    sample_weight: float = 1.0


def _logit(ps):
    p = np.clip(np.asarray(ps, dtype=float), 1e-6, 1-1e-6)
    return np.log(p/(1-p))


def apply_parameters(ps, method, params):
    p = np.asarray(ps, dtype=float)
    if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError('invalid model probabilities')
    if method == 'raw':
        return p.tolist()
    if method == 'isotonic':
        out = np.interp(p, params['x'], params['y'])
    elif method in ('platt', 'temperature'):
        z = (_logit(p)*params['coef']+params['intercept'] if method == 'platt'
             else _logit(p)/params['temperature'])
        out = 1/(1+np.exp(-np.clip(z, -700, 700)))
    else:
        raise ValueError('unknown calibration method')
    return np.clip(out, 1e-6, 1-1e-6).tolist()


def _loss(rows, ps):
    y = np.asarray([r.y for r in rows])
    w = np.asarray([r.sample_weight for r in rows])
    p = np.clip(ps, 1e-6, 1-1e-6)
    return (float(np.average((p-y)**2, weights=w)),
            float(np.average(-y*np.log(p)-(1-y)*np.log(1-p), weights=w)))


def fit_parameters(rows: Sequence[CalibrationRow], method: str) -> dict:
    p = [r.p_model for r in rows]
    y = [r.y for r in rows]
    w = [r.sample_weight for r in rows]
    if method == 'raw':
        return {}
    if method == 'temperature':
        t = min(TEMPERATURES, key=lambda t: _loss(
            rows, apply_parameters(p, method, {'temperature':t}))[1])
        return {'temperature':t}
    if method == 'platt':
        m = LogisticRegression(C=1, max_iter=500, random_state=0)
        m.fit(_logit(p).reshape(-1,1), y, sample_weight=w)
        return {'coef':float(m.coef_[0,0]), 'intercept':float(m.intercept_[0])}
    if method == 'isotonic':
        m = IsotonicRegression(out_of_bounds='clip', y_min=1e-6, y_max=1-1e-6)
        m.fit(p, y, sample_weight=w)
        return {'x':m.X_thresholds_.tolist(), 'y':m.y_thresholds_.tolist()}
    raise ValueError('unknown calibration method')


@dataclass(frozen=True)
class CalibrationBundle:
    method: str
    parameters_json: str
    n: int
    train_end: str
    fingerprint: str
    audit_json: str

    @property
    def audit(self):
        return json.loads(self.audit_json)

    @property
    def parameters(self):
        return json.loads(self.parameters_json)

    def predict(self, ps, prediction_time):
        if utc_key(prediction_time) <= utc_key(self.train_end):
            raise ValueError('calibration prediction must follow TRAIN')
        return apply_parameters(ps, self.method, self.parameters)


def fit_model_calibrator(rows: Sequence[CalibrationRow], *, train_end: str,
                         market: str) -> CalibrationBundle:
    eligible = []
    for r in rows:
        if r.market != market or utc_key(r.label_available_at) >= utc_key(train_end):
            continue
        if utc_key(r.model_trained_until) >= utc_key(r.prediction_time):
            raise ValueError('model_trained_until must precede prediction_time')
        if utc_key(r.label_available_at) <= utc_key(r.prediction_time):
            raise ValueError('label must follow prediction')
        if (not finite_number(r.p_model) or not 0 <= r.p_model <= 1
                or r.y not in (0,1) or not finite_number(r.sample_weight)
                or r.sample_weight < 0):
            raise ValueError('invalid calibration row')
        if r.sample_weight > 0:
            eligible.append(r)
    eligible.sort(key=lambda r:(utc_key(r.prediction_time), r.p_model, r.y))
    days = sorted({utc_key(r.prediction_time) for r in eligible})
    split = days[max(0, int(len(days)*.75)-1)] if days else ''
    valid = [r for r in eligible if utc_key(r.prediction_time) > split]
    valid_start = min((utc_key(r.prediction_time) for r in valid), default=train_end)
    fit = [r for r in eligible if utc_key(r.prediction_time) <= split
           and utc_key(r.label_available_at) < valid_start]
    method = 'raw'
    candidates = {}
    reason = 'INSUFFICIENT_INTERNAL_OOS'
    if len(fit) >= 50 and len(valid) >= 50 and len({r.y for r in fit}) == 2:
        raw_loss = _loss(valid, [r.p_model for r in valid])
        best = sum(raw_loss)
        for candidate in METHODS:
            params = fit_parameters(fit, candidate)
            losses = _loss(valid, apply_parameters([r.p_model for r in valid], candidate, params))
            candidates[candidate] = {'brier':losses[0], 'logloss':losses[1]}
            if losses[0] <= raw_loss[0] and losses[1] <= raw_loss[1] and sum(losses) < best:
                method, best = candidate, sum(losses)
        reason = 'INTERNAL_VALIDATION_ONLY'
    params = fit_parameters(eligible, method)
    audit = dict(source='p_model', market=market, train_end=train_end,
                 n=len(eligible), n_fit=len(fit), n_validation=len(valid),
                 validation_start=valid_start, candidates=candidates, reason=reason,
                 methods=METHODS, temperatures=TEMPERATURES)
    identity = dict(audit= audit, method=method, params=params,
                    rows=[asdict(r) for r in eligible])
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return CalibrationBundle(method, json.dumps(params), len(eligible), train_end,
                             fingerprint, json.dumps(audit))
