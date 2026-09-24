"""Validate experimental evidence against corpus and implementation identity."""
import hashlib
import json
from pathlib import Path


def fingerprint():
    from .football_data_uk import FootballDataClient

    root = Path(__file__).resolve().parent
    paths = [root / name for name in (
        "ml_walkforward.py", "model_walkforward.py", "value_walkforward.py",
        "temporal.py", "models/elo.py", "models/xgboost_model.py",
        "models/lightgbm_model.py")]
    paths.extend(sorted((root / "features").glob("*.py")))
    digest = hashlib.sha256()
    digest.update(FootballDataClient().corpus_signature().encode())
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def load_validated():
    from .config import output_root

    path = output_root() / "engineering/quant/ensemble_oos_validation.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"status": "INVALID", "reason": "unreadable ensemble artifact"}
    if payload.get("implementation_fingerprint") != fingerprint():
        return {"status": "STALE", "reason": "corpus/code fingerprint mismatch"}
    return payload.get("ensemble")
