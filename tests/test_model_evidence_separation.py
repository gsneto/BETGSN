"""ETAPA 16-17 do ciclo de modelo — bypass e separação de evidência.

Garante que a avaliação de MODELO não criou caminho paralelo de decisão
nem contaminou a evidência da estratégia:

- model_walkforward não produz decisão (só evidência);
- o promotion gate da estratégia NÃO consome performance de modelo como
  se fosse da estratégia (e vice-versa);
- ML models dos benchmarks são declarados com janelas PRÓPRIAS — nunca
  misturados com as 24 janelas;
- market baseline nunca é rotulado como model performance em nenhum
  artefato do ciclo.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


# ==========================================================================
# 1. Nenhum bypass: harness de modelo não decide
# ==========================================================================


def test_model_harness_imports_no_decision_path():
    """model_walkforward não importa staking/runner: evidência only."""
    source = (_REPO / "betgsn" / "model_walkforward.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(m.startswith("betgsn.staking") for m in imported)
    assert not any(m.startswith("betgsn.strategy_runner") for m in imported)
    assert not any(m.startswith("betgsn.api") for m in imported)


def test_decide_bet_still_only_betdecision_producer():
    """Regressão do AST check: nada novo constrói BetDecision."""
    translation_aliases = {"S", "schemas", "Schemas", "B"}
    offenders = []
    for path in sorted((_REPO / "betgsn").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(_REPO))
        is_staking = rel.replace("\\", "/") == "betgsn/staking.py"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "BetDecision":
                if not is_staking:
                    offenders.append(f"{rel}:{node.lineno}")
            elif (isinstance(func, ast.Attribute)
                  and func.attr == "BetDecision"
                  and not (isinstance(func.value, ast.Name)
                           and func.value.id in translation_aliases)):
                if not is_staking:
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


# ==========================================================================
# 2. Separação de evidência nos artefatos
# ==========================================================================


def test_artifacts_label_sources_when_present():
    """Se os artefatos do ciclo de modelo existem, os rótulos de origem
    estão presentes — nada de market baseline apresentado como modelo."""
    checks = {
        "output/model_validation_oos.json": [
            "market_raw", "market_fair", "model_raw", "model_calibrated",
            "strategy_model",
        ],
        "output/engineering/quant/model_comparison_oos.json": [
            "market_raw", "model_raw", "paired_model_vs_raw",
        ],
        "output/engineering/quant/strategy_validation_oos.json": [
            "strategy_model", "strategy_market",
        ],
    }
    for rel, keys in checks.items():
        path = _REPO / rel
        if not path.exists():
            continue  # artefato ainda não gerado: sem nada para validar
        import json

        body = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(body)
        for key in keys:
            assert key in text, f"{rel}: rótulo {key} ausente"


def test_strategy_promotion_does_not_read_model_cache():
    """O promotion gate da ESTRATÉGIA não consome o cache de modelo:
    são evidências separadas (a estratégia é market-based; o modelo é
    avaliado no relatório próprio). Prova: service._strategy_promotion
    só lê value_walkforward/odds_snapshots."""
    source = (_REPO / "betgsn" / "api" / "service.py").read_text(
        encoding="utf-8")
    # dentro do método _strategy_promotion
    start = source.index("def _strategy_promotion")
    end = source.index("\n    def ", start + 10)
    body = source[start:end]
    assert "model_walkforward" not in body
    assert "cached_oos_evidence" in body  # evidência da ESTRATÉGIA


def test_ml_benchmark_models_declared_separately():
    """Os relatórios de benchmark ML têm janelas PRÓPRIAS declaradas —
    nunca apresentadas como as 24 janelas da estratégia."""
    path = (_REPO / "output" / "engineering" / "benchmark"
            / "multi_league_2025.json" / "report.json")
    if not path.exists():
        path = (_REPO / "output" / "engineering" / "benchmark"
                / "multi_league_2024" / "report.json")
    if not path.exists():
        import pytest

        pytest.skip("sem relatórios de benchmark ML neste ambiente")
    import json

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body.get("kind") == "multi_league_benchmark"
    # janelas POR LIGA (bloco próprio de 5 papéis), não as 24 da estratégia
    for league in body.get("leagues", []):
        windows = league.get("windows") or []
        assert 0 < len(windows) <= 6
        for w in windows:
            assert "n" in w and "start" in w and "end" in w
    # e o total de janelas por liga é bem menor que 24
    assert all(
        len(league.get("windows") or []) <= 6
        for league in body.get("leagues", [])
    )


def test_model_evidence_sections_note_exists():
    """O payload de modelo declara que ML models vivem em benchmarks
    próprios — sem mistura silenciosa."""
    path = _REPO / "output" / "model_validation_oos.json"
    if not path.exists():
        import pytest

        pytest.skip("validação de modelo ainda não executada")
    import json

    body = json.loads(path.read_text(encoding="utf-8"))
    assert "sections_note" in body
    assert "ML models" in body["sections_note"]


# ==========================================================================
# 3. CLV prospectivo: gate continua íntegro (regressão do ciclo anterior)
# ==========================================================================


def test_clv_monitor_tool_exists_and_blocks():
    """O monitor de CLV existe e continua BLOCKED < 30 (não alterado)."""
    source = (_REPO / "tools" / "clv_report.py").read_text(encoding="utf-8")
    assert "MIN_CLV_SAMPLE = 30" in source
    assert "BLOCKED" in source
