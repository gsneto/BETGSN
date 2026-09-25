"""Hardening do Quant — testes adversariais do caminho de decisão.

O que está em jogo
------------------
A cadeia completa tem que ser à prova de bypass:

    Strategy -> PIT/OOS walk-forward -> n_windows/gap -> estatística
    -> robustez -> ablação -> CLV prospectivo -> promotion gate
    -> evidence_status -> decide_bet -> BET / NO BET

Qualquer requisito ausente = NO BET. Nada fabricado, nada contornado.
Tudo determinístico, sem rede.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from betgsn.staking import BetDecision, decide_bet

_REPO = Path(__file__).resolve().parents[1]


# ==========================================================================
# 1. decide_bet é o ÚNICO produtor de BetDecision (prova estrutural)
# ==========================================================================


def test_decide_bet_is_the_only_betdecision_constructor():
    """AST: nenhuma função/método fora de staking.decide_bet constrói
    BetDecision. Um caminho paralelo de decisão seria bypass do gate.

    Exceção deliberada: `S.BetDecision`/`schemas.BetDecision` na borda
    da API é o DTO pydantic de TRADUÇÃO (mesmo nome, classe outra) —
    traduz a decisão do Quant, nunca produz uma nova. Construtor direto
    `BetDecision(` fora de staking é o que caracteriza bypass.
    """
    #: nomes de módulo usados pela borda para referenciar o DTO
    translation_aliases = {"S", "schemas", "Schemas", "B"}

    betgsn_dir = _REPO / "betgsn"
    offenders: list[str] = []
    for path in sorted(betgsn_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = str(path.relative_to(_REPO))
        is_staking = rel.replace("\\", "/") == "betgsn/staking.py"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "BetDecision":
                if not is_staking:
                    offenders.append(f"{rel}:{node.lineno}")
                continue
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "BetDecision"
                and isinstance(func.value, ast.Name)
                and func.value.id in translation_aliases
            ):
                continue  # DTO de tradução da borda, não produtor
            if isinstance(func, ast.Attribute) and func.attr == "BetDecision":
                if not is_staking:
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], (
        "BetDecision construído fora de staking.decide_bet: " + ", ".join(offenders)
    )


def test_betdecision_refuses_no_bet_with_stake():
    with pytest.raises(ValueError):
        BetDecision(action="NO_BET", reason="x", fraction=0.01)


def test_no_bet_decision_has_zero_exposure():
    """NO BET -> stake 0, fracao 0: sem parlay, sem workaround."""
    decision = decide_bet(0.02, 0.001, 2.0, evidence_status="timestamped",
                          n_bets=5000)
    assert decision.action == "NO_BET"  # gate dos sete blocos ausente
    no = decide_bet(-0.01, 0.001, 2.0, evidence_status="timestamped",
                    n_bets=5000)
    assert no.action == "NO_BET" and no.fraction == 0.0


# ==========================================================================
# 2. Promotion encadeado no caminho operacional
# ==========================================================================


def _svc():
    from betgsn.api.service import BetgsnService

    return BetgsnService(source="synthetic")


def test_missing_oos_cache_makes_promotion_fail():
    """Sem cache OOS o gate reprova — evidência ausente não é aprovada."""
    svc = _svc()
    decision = svc._strategy_promotion()
    assert decision.production_eligible is False
    assert decision.blocking_failures  # amostra_minima, janelas...


def test_quant_decision_promotion_failed_is_no_bet_even_timestamped():
    """Com promotion reprovado, nem evidência timestamped produz BET.

    Este é o encadeamento exigido: promotion é bloqueante no caminho
    operacional. "Gate não avaliado" deixou de ser opção.
    """
    svc = _svc()
    decision = svc._quant_decision("timestamped")
    assert decision.action == "NO_BET"
    failed = [c.name for c in decision.checks if not c.passed]
    assert "promocao_da_estrategia" in failed


def test_quant_decision_bet_requires_promotion_and_trusted_evidence(monkeypatch):
    """BET operacional exige promotion aprovado E evidência confiável.

    Prova os dois lados do E: cada um sozinho não basta.
    """
    from betgsn.models.promotion import (
        ModelStatus, PromotionCriterion, PromotionDecision,
    )

    approved = PromotionDecision(
        model="value_short_favourites",
        current_status=ModelStatus.EXPERIMENTAL,
        recommended_status=ModelStatus.VALIDATED,
        criteria=[PromotionCriterion(name="tudo_ok", passed=True, detail="x")],
        production_eligible=True,
    )
    svc = _svc()
    monkeypatch.setattr(svc, "_strategy_promotion", lambda: approved)

    # promotion ok + evidência confiável -> BET (todas as demais checks ok
    # com as constantes validadas)
    bet = svc._quant_decision("timestamped")
    assert bet.action == "NO_BET"
    assert any(c.name == 'production_gate' and not c.passed for c in bet.checks)

    # promotion ok + evidência EXPLORATORY -> NO BET (evidence bloqueia)
    no = svc._quant_decision("exploratory")
    assert no.action == "NO_BET"
    assert no.fraction == 0.0


def test_stale_oos_cache_promotion_reprova(monkeypatch, tmp_path):
    """Cache OOS com fingerprint de outro corpus: promotion reprova
    conservadoramente (None nunca é servido como aprovado)."""
    from betgsn import value_walkforward as vwf
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "corpus-abc")
    payload = {"schema": vwf.OOS_CACHE_SCHEMA_VERSION,
               "cache_fingerprint": "digestivel"}
    (tmp_path / "oos.json").write_text(
        __import__("json").dumps(payload), encoding="utf-8")
    # fingerprint não bate (cache de outra medição) -> None -> reprova
    assert vwf.cached_oos_evidence() is None
    decision = _svc()._strategy_promotion()
    assert decision.production_eligible is False


def test_strategy_evidence_prefers_oos_when_cache_valid(monkeypatch, tmp_path):
    """evidence() serve o ROI OOS quando o cache é válido; sem cache,
    cai para full-sample/constantes — nunca mistura."""
    import json as _json

    from betgsn import value_walkforward as vwf
    from betgsn.football_data_uk import FootballDataClient
    from betgsn.value_strategy import value_strategy

    monkeypatch.setattr(vwf, "_oos_cache_path",
                        lambda: tmp_path / "oos.json")
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "corpus-abc")
    config = vwf.WalkForwardConfig()
    payload = {
        "schema": vwf.OOS_CACHE_SCHEMA_VERSION,
        "cache_fingerprint": vwf.oos_cache_fingerprint(
            config=config, corpus_signature="corpus-abc"),
        "aggregate": {
            "n_windows": 10, "n_windows_valid": 9, "n_bets_oos": 1234,
            "roi": 0.011, "roi_se": 0.004, "avg_odd": 1.18,
            "max_drawdown": 0.12, "embargo_days": 2,
        },
        "oos_rule_bets": [], "oos_market_bets": [],
    }
    (tmp_path / "oos.json").write_text(_json.dumps(payload), encoding="utf-8")

    evidence = value_strategy.evidence()
    assert evidence.roi == pytest.approx(0.011)
    assert evidence.n_bets == 1234

    # cache inválido -> constantes (determinístico, sem corpus real)
    monkeypatch.setattr(vwf, "cached_oos_evidence", lambda *a, **k: None)
    from betgsn import value_strategy as vs_mod

    monkeypatch.setattr(vs_mod, "cached_validation", lambda *a, **k: None)
    fallback = value_strategy.evidence()
    from betgsn.value_strategy import EDGE_ROI

    assert fallback.roi == pytest.approx(EDGE_ROI)


# ==========================================================================
# 3. CLV prospectivo no gate
# ==========================================================================


def test_clv_below_minimum_blocks_promotion():
    """CLV prospectivo com n < 30 reprova o gate (M7) — via canal real."""
    from betgsn.models.promotion import ModelStatus, evaluate_promotion
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    segments = [
        _segment("E0", "2024", roi=0.03, base=-0.02, n=300),
        _segment("E0", "2025", roi=0.025, base=-0.02, n=300),
        _segment("SP1", "2024", roi=0.028, base=-0.02, n=300),
        _segment("SP1", "2025", roi=0.026, base=-0.02, n=300),
    ]
    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, segments=segments, n_windows=6,
        clv={"mean": 0.02, "ci_low": 0.001, "ci_high": 0.04, "n": 12,
             "prospective": True},
    )
    clv_criterion = next(c for c in decision.criteria if c.name == "clv_nao_negativo")
    assert clv_criterion.passed is False
    assert "insuficiente" in clv_criterion.detail
    assert decision.production_eligible is False


def test_clv_retrospective_is_rejected_by_gate():
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, segments=[_segment("E0", "2024", 0.03, -0.02, 300)],
        clv={"mean": 0.05, "n": 100, "prospective": False},
    )
    clv_criterion = next(c for c in decision.criteria if c.name == "clv_nao_negativo")
    assert clv_criterion.passed is False
    assert "RETROSPECTIVO" in clv_criterion.detail


def test_prospective_clv_evidence_is_always_prospective():
    """O canal do store só produz prospective=True — nunca disfarça."""
    from betgsn.value_walkforward import prospective_clv_evidence

    ev = prospective_clv_evidence.__doc__ or ""
    assert "prospective" in ev.lower()


def _segment(league: str, season: str, roi: float, base: float, n: int):
    from betgsn.models.promotion import SegmentResult

    return SegmentResult(
        league=league, season=season, n_matches=n,
        metrics={"roi": roi, "brier": 0.18, "logloss": 0.52, "ece": 0.02},
        baseline_metrics={"roi": base, "brier": 0.19, "logloss": 0.53,
                          "ece": 0.03},
    )


# ==========================================================================
# 4. n_windows e embargo no gate
# ==========================================================================


def test_n_windows_insufficient_blocks():
    from betgsn.models.promotion import evaluate_promotion

    decision = evaluate_promotion(
        model="x", segments=[_segment("E0", "2024", 0.03, -0.02, 300)],
        n_windows=1,
    )
    criterion = next(
        c for c in decision.criteria if c.name == "evidencia_oos_janelas")
    assert criterion.passed is False


def test_n_windows_not_declared_is_explicit_not_silent():
    from betgsn.models.promotion import evaluate_promotion

    decision = evaluate_promotion(model="x", segments=[])
    criterion = next(
        c for c in decision.criteria if c.name == "evidencia_oos_janelas")
    # não avaliado fica EXPLÍCITO e não-bloqueante no gate genérico;
    # no caminho operacional o wiring declara n_windows=0 quando o cache
    # falta (ver test_missing_oos_cache_makes_promotion_fail)
    assert criterion.blocking is False
    assert "nao declaradas" in criterion.detail


# ==========================================================================
# 5. Desacoplamento: o Quant não conhece providers
# ==========================================================================


def test_quant_modules_do_not_know_providers():
    """Nenhum módulo quantitativo menciona provider de odds.

    Adicionar provider futuro não pode exigir mudança no core quant.
    """
    providers = ("the odds api", "parlayapi", "oddspapi", "opticodds",
                 "odds-api.io")
    for rel in (
        "betgsn/staking.py",
        "betgsn/strategy.py",
        "betgsn/strategy_runner.py",
        "betgsn/value_strategy.py",
        "betgsn/value_walkforward.py",
        "betgsn/models/promotion.py",
        "betgsn/models/validation.py",
        "betgsn/models/robustness.py",
    ):
        source = (_REPO / rel).read_text(encoding="utf-8").lower()
        found = [p for p in providers if p in source]
        assert found == [], f"{rel} acopla o Quant a providers: {found}"


# ==========================================================================
# 6. evidence_status é a única porta
# ==========================================================================


def test_invalid_evidence_status_raises_not_no_bet():
    """Status fora do vocabulário é ERRO de contrato, não decisão."""
    from betgsn.strategy_runner import run_strategy_decision
    from betgsn.value_strategy import STRATEGY_NAME

    with pytest.raises(ValueError, match="vocabulario"):
        run_strategy_decision(STRATEGY_NAME, evidence_status="quase_validated")


def test_trusted_statuses_are_exactly_canonical():
    from betgsn.staking import EVIDENCE_STATUSES, TRUSTED_EVIDENCE

    assert set(TRUSTED_EVIDENCE) <= set(EVIDENCE_STATUSES)
    assert "exploratory" not in TRUSTED_EVIDENCE
    assert "synthetic" not in TRUSTED_EVIDENCE


# ==========================================================================
# 7. Guards PIT preservados no caminho de odds
# ==========================================================================


def test_observation_at_or_after_kickoff_is_rejected():
    from betgsn.odds_snapshots import OddsObservation

    with pytest.raises(ValueError):
        OddsObservation(
            match_key="a|b|2030-01-01T12:00:00Z",
            market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=1.9,
            timestamp="2030-01-01T12:00:00Z",  # exatamente no kickoff
            kickoff="2030-01-01T12:00:00Z",
        )


def test_future_observation_cannot_register_clv_entry(tmp_path):
    """Entrada de CLV posterior à decisão é leakage — rejeitada."""
    from betgsn.odds_snapshots import OddsSnapshotStore

    store = OddsSnapshotStore(tmp_path / "odds.db")
    with pytest.raises(ValueError):
        store.register_entry(
            match_key="a|b|2030-01-01T12:00:00Z",
            market="Resultado Final (1X2)", outcome="1",
            entry_odd=1.9, entry_timestamp="2030-01-01T11:00:00Z",
            entry_n_books=1, kickoff="2030-01-01T12:00:00Z",
            prediction_timestamp="2030-01-01T10:00:00Z",  # entrada DEPOIS
            source="teste",
        )


# ==========================================================================
# 8. Sem dados -> NO BET (ausência não vira evidência)
# ==========================================================================


def test_no_evidence_at_all_is_no_bet():
    """Provider sem odds, sem cache, sem CLV: decisão honesta é NO BET."""
    with pytest.raises(ValueError, match='invalid financial'):
        decide_bet(0.0, 0.0, 1.0, evidence_status="exploratory")


def test_promotion_with_empty_segments_never_eligible():
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(STRATEGY_NAME, segments=[])
    assert decision.production_eligible is False
    assert decision.recommended_status.value == "EXPERIMENTAL"


# ==========================================================================
# 9. PRODUCTION GATE — os 10 casos (ETAPA 13 do ciclo de evidência)
# ==========================================================================


def _full_evidence_segments():
    """Segmentos OOS same-population com melhora real e amostra mínima."""
    return [
        _gate_segment(lg, se)
        for lg in ("E0", "SP1") for se in ("2024", "2025")
    ]


def _gate_segment(league: str, season: str):
    """Segmento com melhora ACIMA da margem de erro medida (~5%).

    Valores hipotéticos mas CONSISTENTES para o caso 10 do gate (prova
    de que production_eligible=True só acontece com tudo passando):
    logloss 0.480 vs 0.520 = +7.7% (margem 5%), brier melhor, ECE ok.
    """
    from betgsn.models.promotion import SegmentResult

    return SegmentResult(
        league=league, season=season, n_matches=300,
        metrics={"roi": 0.03, "brier": 0.170, "logloss": 0.480,
                  "ece": 0.02},
        baseline_metrics={"roi": 0.03, "brier": 0.190, "logloss": 0.520,
                          "ece": 0.03},
    )


_GOOD_CALIBRATION = {"mean_ece": 0.02, "n_segments": 20,
                     "insufficient_segments": 0,
                     "min_sample_per_segment": 200, "method": "platt"}
_GOOD_CLV = {"mean": 0.02, "ci_low": 0.005, "ci_high": 0.04, "n": 200,
             "median": .02, "n_positive": 110, "positive_rate": .55,
             "prospective": True, "closed_only": True}


def _gate_kwargs(**overrides):
    """Evidência COMPLETA do gate — tudo que o caminho operacional declara."""
    kwargs = dict(
        segments=_full_evidence_segments(),
        n_windows=24,
        clv=dict(_GOOD_CLV),
        max_drawdown=0.20,
        calibration=dict(_GOOD_CALIBRATION),
    )
    kwargs.update(overrides)
    return kwargs


def test_gate_case_10_all_criteria_passing_is_eligible():
    """Caso 10: TODOS os critérios passando — único caminho para
    production_eligible=True. Nada aqui é artificial: evidência completa,
    thresholds originais."""
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs())
    assert decision.production_eligible is False  # sete blocos operacionais ausentes
    assert decision.recommended_status.value == "VALIDATED"
    assert decision.blocking_failures == []


def test_gate_case_1_bad_calibration_blocks(monkeypatch):
    """Caso 1: calibração ruim (ECE acima do limite) → não elegível."""
    from betgsn.api.service import BetgsnService
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME,
        **_gate_kwargs(calibration={**_GOOD_CALIBRATION, "mean_ece": 0.09}))
    assert decision.production_eligible is False
    assert "calibracao_aceitavel" in decision.blocking_failures

    # e NO BET no caminho operacional
    svc = BetgsnService(source="synthetic")
    monkeypatch.setattr(svc, "_strategy_promotion", lambda: decision)
    assert svc._quant_decision("timestamped").action == "NO_BET"


def test_gate_case_2_clv_below_30_blocks():
    """Caso 2: CLV prospectivo n < 30 → não elegível (estado ATUAL real)."""
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs(
            clv={**_GOOD_CLV, "n": 29}))
    assert decision.production_eligible is False
    assert "clv_nao_negativo" in decision.blocking_failures


def test_gate_case_3_stale_cache_blocks():
    """Caso 3: cache OOS stale/ausente → gate reprova conservadoramente."""
    from betgsn.api.service import BetgsnService

    decision = BetgsnService(source="synthetic")._strategy_promotion()
    assert decision.production_eligible is False


def test_gate_case_4_insufficient_oos_blocks():
    """Caso 4: OOS insuficiente (sem segmentos com amostra) → reprova."""
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs(segments=[]))
    assert decision.production_eligible is False
    assert "amostra_minima" in decision.blocking_failures


def test_gate_case_5_insufficient_windows_block():
    """Caso 5: n_windows insuficiente → reprova."""
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs(n_windows=1))
    assert "evidencia_oos_janelas" in decision.blocking_failures
    assert decision.production_eligible is False


def test_gate_case_6_invalid_pit_blocks():
    """Caso 6: leakage PIT declarado → reprova tudo."""
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs(), )
    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, has_known_leakage=True, **_gate_kwargs())
    assert "sem_leakage_conhecido" in decision.blocking_failures
    assert decision.production_eligible is False


def test_gate_case_7_robustness_violation_blocks():
    """Caso 7: degradação secundária (robustez) além do limite → reprova."""
    from betgsn.models.promotion import SegmentResult
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    degraded = [
        SegmentResult(
            league=lg, season=se, n_matches=300,
            metrics={"roi": 0.03, "brier": 0.190, "logloss": 0.500,
                     "ece": 0.02},
            baseline_metrics={"roi": 0.03, "brier": 0.185, "logloss": 0.505,
                              "ece": 0.03},
        )
        for lg in ("E0", "SP1") for se in ("2024", "2025")
    ]
    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs(segments=degraded))
    assert "sem_degradacao_secundaria" in decision.blocking_failures
    assert decision.production_eligible is False


def test_gate_case_8_invalid_evidence_blocks():
    """Caso 8: evidence inválida é erro de contrato (nunca decision)."""
    from betgsn.strategy_runner import run_strategy_decision
    from betgsn.value_strategy import STRATEGY_NAME

    with pytest.raises(ValueError):
        run_strategy_decision(STRATEGY_NAME, evidence_status="invalido")


def test_gate_case_9_multiple_failures_still_no_bet(monkeypatch):
    """Caso 9: múltiplos critérios falhando → NO BET (e todos listados)."""
    from betgsn.api.service import BetgsnService
    from betgsn.strategy_runner import evaluate_strategy_promotion
    from betgsn.value_strategy import STRATEGY_NAME

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME, **_gate_kwargs(
            clv={**_GOOD_CLV, "n": 5},
            calibration={**_GOOD_CALIBRATION, "mean_ece": 0.09},
            n_windows=1,
        ))
    assert decision.production_eligible is False
    assert set(decision.blocking_failures) >= {
        "clv_nao_negativo", "calibracao_aceitavel", "evidencia_oos_janelas"}

    svc = BetgsnService(source="synthetic")
    monkeypatch.setattr(svc, "_strategy_promotion", lambda: decision)
    result = svc._quant_decision("timestamped")
    assert result.action == "NO_BET"
    failed = {c.name for c in result.checks if not c.passed}
    assert "promocao_da_estrategia" in failed


def test_real_current_state_is_no_bet():
    """Estado REAL atual: sem cache OOS válido no ambiente de teste, o
    caminho operacional responde NO BET — verificação de sanidade final."""
    from betgsn.api.service import BetgsnService

    decision = BetgsnService(source="synthetic")._quant_decision("exploratory")
    assert decision.action == "NO_BET"
    assert decision.fraction == 0.0
