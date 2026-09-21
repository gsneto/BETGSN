import numpy as np
import pytest
from betgsn.models.base import TemporalBatch
from betgsn.models.calibration import TemporalCalibrator
from betgsn.models.ensemble import WeightedEnsemble
from betgsn.evaluation import rps, calibration_metrics, clv


def test_rps_and_ece_known_values():
    assert rps([1,0,0],0) == 0
    assert rps([1,0,0],2) == 1
    assert rps([1/3]*3,1) == pytest.approx(1/9)
    m = calibration_metrics([(.5,0),(.5,1),(1,1)])
    assert m["ece"] == 0
    assert m["mce"] == 0


def test_clv_missing_and_timestamp():
    assert clv(2,None,"2025-01-01","2025-01-02",None)["percentage"] is None
    assert clv(2.2,2,"2025-01-01","2025-01-02","2025-01-03")["percentage"] == pytest.approx(.1)
    with pytest.raises(ValueError):
        clv(2,2,"2025-01-03","2025-01-02","2025-01-01")


@pytest.mark.parametrize("method", ["platt", "isotonic"])
def test_applied_calibration_oos(method):
    p = np.array([[.9,.05,.05],[.05,.9,.05],[.05,.05,.9]]*20)
    y = np.array([0,1,2,1,2,0]*10)
    c = TemporalCalibrator(method).fit(p,y,["2024-01-01"]*60,"2023-01-01")
    out = c.predict_proba(p,"2025-01-01")
    assert np.allclose(out.sum(axis=1),1)
    assert not np.allclose(out,p)
    with pytest.raises(ValueError):
        c.predict_proba(p,"2024-01-01")
    with pytest.raises(ValueError):
        c.fit(p,y,["2024-01-01"]*60,"2024-01-01")


def test_ensemble_learns_and_enforces_oos():
    good=np.array([[.8,.1,.1],[.1,.8,.1],[.1,.1,.8]]*10)
    bad=np.full((30,3),1/3)
    e=WeightedEnsemble().fit({"good":good,"bad":bad},[0,1,2]*10,["2024-01-01"]*30,"2023-01-01")
    assert e.weights[e.names.index("good")] > .9
    assert np.allclose(e.predict_proba({"good":good,"bad":bad},"2025-01-01").sum(axis=1),1)
    with pytest.raises(ValueError):
        e.predict_proba({"good":good,"bad":bad},"2024-01-01")


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_boosters_fit_temporal_and_binary(backend):
    if backend == "xgboost":
        from betgsn.models.xgboost_model import XGBoostModel as Model
    else:
        from betgsn.models.lightgbm_model import LightGBMModel as Model
    x=np.arange(240).reshape(120,2)%7
    for classes in (2,3):
        train=TemporalBatch(x,np.arange(120)%classes,tuple(["2023-01-01"]*120))
        val=TemporalBatch(x[:30],np.arange(30)%classes,tuple(["2024-01-01"]*30))
        model=Model().fit(train,val)
        assert model.predict_proba(x[:5]).shape == (5,classes)
        with pytest.raises(ValueError):
            Model().fit(val,train)
