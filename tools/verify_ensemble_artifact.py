"""Offline verification of a completed run, without fitting or changing metrics."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from betgsn.config import output_root
from betgsn.ensemble_artifact import fingerprint
from betgsn.timeutil import utc_key


def main():
    path = output_root() / "engineering/quant/ensemble_oos_validation.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    ensemble = payload["ensemble"]
    assert ensemble["n_windows"] == ensemble["n_windows_valid"] == 24
    audits = ensemble["stacking_audits"]
    assert len(audits) == 24
    assert len(ensemble["windows"]) == 24
    used = skipped = 0
    for window, audit in zip(ensemble["windows"], audits):
        assert window["gap_days"] == 2
        assert window["train_end"] == audit["train_end"]
        assert audit["meta"]["last_time"] < audit["train_end"]
        assert audit["final_bases"]["last_time"] < audit["train_end"]
        assert window["calibration_method"] == "raw"
        assert audit["meta"]["n_rows"] == sum(f["n_rows"] for f in audit["folds"] if not f["skipped"])
        for fold in audit["folds"]:
            if fold["skipped"]:
                skipped += 1
                continue
            used += 1
            assert fold["n_rows"] >= 1000
            assert fold["embargo_days"] == 2
            assert utc_key(fold["base_train_end"]) < fold["base_cutoff"]
            assert fold["labels_available_until"] < utc_key(fold["first_prediction"])
            assert fold["last_prediction"] < audit["train_end"]
    assert sum(w["n_test_bets"] for w in ensemble["windows"]) == ensemble["n_bets_oos"]
    assert sum(b["n"] for b in ensemble["odds_bands"]) == ensemble["n_bets_oos"]
    assert payload["implementation_fingerprint"] == fingerprint(), "stale artifact"
    verification = {"windows": 24, "folds_used": used,
                               "folds_skipped_insufficient_early_stop": skipped,
                               "temporal_audits_passed": True}
    print(json.dumps({"verified": verification,
                      "ensemble": ensemble["model_raw"],
                      "market_raw": ensemble["market_raw"],
                      "market_fair": ensemble["market_fair"]}))


if __name__ == "__main__":
    main()
