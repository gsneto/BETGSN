import json

from betgsn import ensemble_artifact as artifacts


def test_artifact_requires_matching_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setattr("betgsn.config.output_root", lambda: tmp_path)
    monkeypatch.setattr(artifacts, "fingerprint", lambda: "current")
    assert artifacts.load_validated() is None
    target = tmp_path / "engineering/quant/ensemble_oos_validation.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"ensemble": {"status": "OK"},
                                  "implementation_fingerprint": "old"}))
    assert artifacts.load_validated()["status"] == "STALE"
    target.write_text(json.dumps({"ensemble": {"status": "OK"},
                                  "implementation_fingerprint": "current"}))
    assert artifacts.load_validated()["status"] == "OK"
