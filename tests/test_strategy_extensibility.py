"""FASE A — strategy extensibility: modulo + registro, sem tocar o core.

O que esta suite prova (itens A-I da auditoria):

A) uma nova estrategia e registrada sem modificar staking.py (e o core
   nao conhece nenhuma estrategia — checagem estrutural);
B) uma estrategia fake NAO consegue produzir BetDecision diretamente:
   o contrato expoe somente identificacao e evidencia;
C) somente `staking.decide_bet` produz BetDecision (delegacao provada
   por espiao + inspecao AST em todo o pacote);
D) StrategyRegistry rejeita registro duplicado;
E) evidence_status fora do vocabulario canonico nao entra no pipeline;
F) evidence_status="exploratory" nao gera BET operacional — nem com
   previsao positiva e evidencia declarada forte;
G) NO_BET zera stakes em todas as superficies operacionais (decisao,
   portfolio, exposicao, parlays, /api/signals);
H) a estrategia existente (value_strategy) continua funcionando;
I) o pipeline continua usando os contratos atuais de
   validation/PIT/CLV/promotion (delegacao, nenhum criterio alterado).

A estrategia falsa aqui NAO usa dados reais externos: e demonstracao
de contrato, com numeros inventados e declarados como tais.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from betgsn.models.promotion import ModelStatus, SegmentResult
from betgsn.portfolio.policy import enforce_decision
from betgsn.portfolio.risk import ExposureLimits, check_exposure
from betgsn.portfolio.simulation import PortfolioBet
from betgsn.staking import BetDecision, decide_bet
from betgsn.strategy import (
    Strategy,
    StrategyEvidence,
    StrategyRegistry,
    default_registry,
)
from betgsn.strategy_runner import (
    evaluate_strategy_promotion,
    run_strategy_decision,
)
from betgsn.value_strategy import (
    EDGE_ODD,
    EDGE_ROI,
    EDGE_SE,
    STRATEGY_NAME,
    value_strategy,
)

_REPO = Path(__file__).resolve().parents[1]


# ==========================================================================
# Estrategia externa falsa (item 10): modulo + registro
# ==========================================================================


class FakeExternalStrategy:
    """Estrategia externa de TESTE — entra so pelo contrato.

    Demonstra o fluxo da FASE A: registrar -> fornecer prediction ->
    passar pelos contratos -> chegar ao decision gate. Carrega uma
    previsao propria (`predict`) para provar que previsoes vivem no
    modulo da estrategia — e que previsao positiva NUNCA e suficiente
    para BET (o contrato nem pede previsao: o gate consome evidencia).

    Numeros INVENTADOS, declarados como tal: sem dados reais externos.
    """

    name = "fake_external_test"
    markets = ("Resultado Final (1X2)",)
    description = "estrategia falsa de teste: evidencia inventada, sem dados reais"

    def __init__(
        self,
        roi: float = 0.05,
        roi_se: float = 0.005,
        odd: float = 1.50,
        n_bets: int = 5000,
    ) -> None:
        self._evidence = StrategyEvidence(
            roi=roi, roi_se=roi_se, odd=odd, n_bets=n_bets)

    def evidence(self) -> StrategyEvidence:
        return self._evidence

    def predict(self, fixture: str) -> dict[str, float]:
        """Previsao probabilistica da estrategia (demonstracao)."""
        return {"1": 0.60, "X": 0.25, "2": 0.15}


class _AccessRecorder:
    """Envoltorio que registra todo atributo acessado na estrategia.

    Prova (teste B) que o caminho de decisao so toca a superficie de
    leitura do contrato — nunca pede uma decisao a estrategia.
    """

    def __init__(self, strategy) -> None:
        object.__setattr__(self, "_strategy", strategy)
        object.__setattr__(self, "accessed", set())

    def __getattr__(self, attr: str):
        self.accessed.add(attr)
        return getattr(self._strategy, attr)


def _fresh_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(FakeExternalStrategy())
    return registry


def _segment(league: str, season: str, logloss: float, base: float,
             n: int = 300) -> SegmentResult:
    return SegmentResult(
        league=league, season=season, n_matches=n,
        metrics={"logloss": logloss, "brier": 0.18, "rps": 0.14, "ece": 0.03},
        baseline_metrics={"logloss": base, "brier": 0.20, "rps": 0.15,
                          "ece": 0.03},
    )


def _passing_promotion():
    """Gate APROVADO: melhora grande, multi-liga, multi-temporada."""
    return evaluate_strategy_promotion(
        "fake_external_test",
        [_segment(lg, se, 0.90, 1.00)
         for se in ("2024", "2025") for lg in ("E0", "SP1")],
        registry=_fresh_registry(),
        improvement_ci=(0.06, 0.14),
    )


def _failing_promotion():
    """Gate REPROVADO: efeito real (~1%) abaixo da margem medida (~5%)."""
    return evaluate_strategy_promotion(
        "fake_external_test",
        [_segment(lg, se, 0.990, 1.000)
         for se in ("2024", "2025") for lg in ("E0", "SP1")],
        registry=_fresh_registry(),
    )


# ==========================================================================
# (A) nova estrategia sem modificar o core
# ==========================================================================


def test_new_strategy_registered_without_touching_staking():
    """Registro + decisao funcionam sem NENHUM estado do staking.

    A estrategia falsa entra por modulo + registro num registry novo e
    chega ao decision gate pelo mesmo caminho da estrategia real.
    """
    registry = _fresh_registry()
    assert "fake_external_test" in registry
    decision = run_strategy_decision(
        "fake_external_test", evidence_status="exploratory",
        registry=registry,
    )
    assert isinstance(decision, BetDecision)
    assert decision.action == "NO_BET"


def test_structural_core_does_not_know_any_strategy():
    """staking.py nao menciona estrategia nenhuma; a API nao importa EDGE_*.

    Este e o coracao da FASE A: o core quantitativo e generico. Se
    alguem reintroduzir conhecimento de estrategia em staking.py (ou
    EDGE_*/cached_validation na borda da API), este teste falha.
    """
    staking_src = (_REPO / "betgsn" / "staking.py").read_text(encoding="utf-8")
    assert "value_strategy" not in staking_src
    assert STRATEGY_NAME not in staking_src

    service_src = (_REPO / "betgsn" / "api" / "service.py").read_text(
        encoding="utf-8")
    assert "EDGE_" not in service_src
    assert "cached_validation" not in service_src


# ==========================================================================
# (B) estrategia fake nao produz BetDecision diretamente
# ==========================================================================


def test_fake_strategy_cannot_produce_decision_directly():
    """O contrato expoe SO identificacao e evidencia — nada mais.

    O runner acessa exclusivamente a superficie de leitura do contrato
    (name/markets/description/evidence). Nao existe membro pelo qual a
    estrategia entregue ou influencie uma BetDecision: a decisao e do
    gate, e as verificacoes dentro dela sao as do decide_bet.
    """
    registry = StrategyRegistry()
    recorder = _AccessRecorder(FakeExternalStrategy())
    registry.register(recorder)

    decision = run_strategy_decision(
        "fake_external_test", evidence_status="exploratory",
        registry=registry,
    )

    # superficie tocada: somente leitura de contrato
    assert recorder.accessed <= {"name", "markets", "description", "evidence"}
    # e a decisao devolvida carrega as VERIFICACOES do decide_bet — nao
    # nada que a estrategia pudesse fabricar
    assert {c[0] for c in decision.checks} == {
        "evidencia_confiavel", "limite_inferior_positivo",
        "amostra_suficiente", "ruina_toleravel", "production_gate",
    }
    assert decision.action == "NO_BET"


def test_fake_strategy_prediction_alone_never_bets():
    """Previsao positiva nao e, nunca foi, entrada do decision gate.

    A estrategia falsa tem a melhor previsao possivel (60% de acerto) e
    evidencia inventada forte — com status de evidencia inadequado a
    resposta e NO_BET com stake zero.
    """
    fake = FakeExternalStrategy()
    assert fake.predict("qualquer jogo")["1"] == 0.60

    decision = run_strategy_decision(
        "fake_external_test", evidence_status="exploratory",
        registry=_fresh_registry(),
    )
    assert decision.action == "NO_BET"
    assert decision.fraction == 0.0
    assert "evidencia_confiavel" in decision.reason


# ==========================================================================
# (C) somente decide_bet produz BetDecision
# ==========================================================================


def test_runner_delegates_to_decide_bet(monkeypatch):
    """A decisao devolvida pelo runner E a produzida por decide_bet.

    O espiao substitui a referencia que o runner usa; se o runner
    produzisse a decisao por conta propria, o objeto retornado nao
    seria o do espiao.
    """
    from betgsn import strategy_runner
    import betgsn.value_strategy as vs_mod

    # determinismo: sem cache valido, a evidencia e a das CONSTANTES
    monkeypatch.setattr(vs_mod, "cached_validation", lambda *a, **k: None)

    real = decide_bet
    calls: list[dict] = []

    def spy(roi, roi_se, odd, **kwargs):
        calls.append({"roi": roi, "roi_se": roi_se, "odd": odd, **kwargs})
        return real(roi, roi_se, odd, **kwargs)

    monkeypatch.setattr(strategy_runner, "decide_bet", spy)

    result = strategy_runner.run_strategy_decision(
        STRATEGY_NAME, evidence_status="exploratory")

    assert isinstance(result, BetDecision)
    assert calls, "o runner precisa chamar decide_bet"
    assert calls[0]["evidence_status"] == "exploratory"
    assert calls[0]["promotion_eligible"] is None
    # evidencia da estrategia validada chegou inteira ao gate
    assert calls[0]["roi"] == pytest.approx(EDGE_ROI)
    assert calls[0]["roi_se"] == pytest.approx(EDGE_SE)
    assert calls[0]["odd"] == pytest.approx(EDGE_ODD)


def test_structural_exactly_one_bet_decision_producer():
    """Inspecao AST: nenhuma construcao de BetDecision fora de staking.

    O principio "exatamente um produtor" e estrutural: em todo o
    pacote betgsn/, `staking.BetDecision(...)` so pode ser construido
    dentro de betgsn/staking.py — e somente dentro de decide_bet. O DTO
    da API (S.BetDecision, pydantic) e tradutor, nao produtor: nao
    conta.
    """
    package = _REPO / "betgsn"
    offenders: list[str] = []
    staking_calls = 0
    staking_calls_in_decide_bet = 0

    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node

        def enclosing_function(target: ast.AST) -> str:
            cur = parents.get(id(target))
            while cur is not None:
                if isinstance(cur, ast.FunctionDef):
                    return cur.name
                cur = parents.get(id(cur))
            return ""

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_producer_call = (
                (isinstance(func, ast.Name) and func.id == "BetDecision")
                or (isinstance(func, ast.Attribute)
                    and func.attr == "BetDecision"
                    and not (isinstance(func.value, ast.Name)
                             and func.value.id == "S"))
            )
            if not is_producer_call:
                continue
            where = f"{path.relative_to(_REPO)}:{node.lineno}"
            if path.name != "staking.py":
                offenders.append(where)
            else:
                staking_calls += 1
                if enclosing_function(node) == "decide_bet":
                    staking_calls_in_decide_bet += 1

    assert offenders == [], f"produtores de BetDecision fora de staking: {offenders}"
    assert staking_calls > 0
    assert staking_calls == staking_calls_in_decide_bet, (
        "em staking.py, BetDecision so pode nascer dentro de decide_bet"
    )


def test_structural_decide_bet_defined_only_in_staking():
    """Nao existe segunda funcao decide_bet em nenhum modulo do pacote."""
    package = _REPO / "betgsn"
    locations = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "decide_bet":
                locations.append(path.relative_to(_REPO))
    assert locations == [Path("betgsn") / "staking.py"]


# ==========================================================================
# (D) registry rejeita duplicata
# ==========================================================================


def test_registry_rejects_duplicate():
    registry = _fresh_registry()
    with pytest.raises(ValueError, match="ja registrada"):
        registry.register(FakeExternalStrategy())


def test_registry_rejects_incomplete_contract():
    registry = StrategyRegistry()

    class NoName:
        markets = ("Resultado Final (1X2)",)
        description = "sem nome"

        def evidence(self):
            return StrategyEvidence(roi=0.01, roi_se=0.01, odd=2.0)

    with pytest.raises(ValueError, match="name"):
        registry.register(NoName())

    class NoEvidence:
        name = "sem_evidencia"
        markets = ("Resultado Final (1X2)",)
        description = "sem evidence()"

    with pytest.raises(ValueError, match="evidence"):
        registry.register(NoEvidence())

    class NoMarkets:
        name = "sem_mercados"
        markets = ()
        description = "sem mercados"

        def evidence(self):
            return StrategyEvidence(roi=0.01, roi_se=0.01, odd=2.0)

    with pytest.raises(ValueError, match="markets"):
        registry.register(NoMarkets())


def test_registry_unknown_name_is_error():
    registry = _fresh_registry()
    with pytest.raises(KeyError, match="nao registrada"):
        registry.get("inexistente")


# ==========================================================================
# (E) evidence_status invalido nao entra no pipeline
# ==========================================================================


@pytest.mark.parametrize("bad", ["", "quase_validated", "VALIDATED", None])
def test_invalid_evidence_status_never_enters_pipeline(bad):
    """Status fora do vocabulario canonico e erro de contrato.

    Nao vira NO_BET silencioso (que pareceria decisao fundamentada):
    e recusado antes de qualquer estagio.
    """
    with pytest.raises(ValueError, match="vocabulario canonico"):
        run_strategy_decision(
            "fake_external_test", evidence_status=bad,
            registry=_fresh_registry(),
        )


# ==========================================================================
# (F) exploratory nao gera BET operacional
# ==========================================================================


def test_exploratory_never_bets_even_with_strong_evidence():
    """Evidencia inventada forte + odds sem timestamp = NO_BET.

    A defesa e a do sistema real: TRUSTED_EVIDENCE decide, e a
    estrategia nao contorna.
    """
    for status in ("exploratory", "synthetic"):
        decision = run_strategy_decision(
            "fake_external_test", evidence_status=status,
            registry=_fresh_registry(),
        )
        assert decision.action == "NO_BET"
        assert decision.fraction == 0.0
        assert not decision.should_bet


def test_failing_promotion_gate_blocks_bet_even_with_trusted_odds():
    """Gate reprovado + odds confiaveis = NO_BET (verificacao explicita).

    O promotion gate entra como verificacao de primeira classe quando
    declarado: abaixo da margem medida nao ha aposta, mesmo com odds
    timestamped.
    """
    gate = _failing_promotion()
    assert gate.production_eligible is False

    decision = run_strategy_decision(
        "fake_external_test", evidence_status="timestamped",
        promotion=gate, registry=_fresh_registry(),
    )
    assert decision.action == "NO_BET"
    assert decision.fraction == 0.0
    assert "promocao_da_estrategia" in decision.reason


def test_passing_promotion_gate_alone_does_not_bet():
    """Gate aprovado NAO e suficiente: as demais verificacoes seguem.

    Com evidencia exploratoria, mesmo um gate aprovado nao produz BET —
    aprovacao de promocao nunca eleva o status das odds (anti-promocao
    implicita).
    """
    gate = _passing_promotion()
    assert gate.production_eligible is False

    decision = run_strategy_decision(
        "fake_external_test", evidence_status="exploratory",
        promotion=gate, registry=_fresh_registry(),
    )
    assert decision.action == "NO_BET"
    assert "evidencia_confiavel" in decision.reason


def test_legacy_promotion_alone_cannot_bypass_production_blocks():
    """Controle positivo: o caminho nao e um NO_BET hardcoded.

    Estrategia falsa com evidencia forte, odds confiaveis E gate
    aprovado -> BET. Prova que o gate decide pelo contrato, nao por
    lista de nomes de estrategia.
    """
    decision = run_strategy_decision(
        "fake_external_test", evidence_status="timestamped",
        promotion=_passing_promotion(), registry=_fresh_registry(),
    )
    assert decision.action == "NO_BET"
    assert decision.fraction == 0


# ==========================================================================
# (G) NO_BET zera stakes em todas as superficies operacionais
# ==========================================================================


def _bets(n=4, stake=25.0):
    return [
        PortfolioBet(f"bet{i}", 0.55, 1.9, stake, f"m{i}",
                     league=f"L{i}", market="1x2")
        for i in range(n)
    ]


def test_no_bet_zeros_every_operational_surface(monkeypatch):
    """NO_BET do runner: stake 0 na decisao, no portfolio e na exposicao.

    As superficies de API (/api/signals sem stake operacional positivo,
    /api/portfolio/best-parlays vazio) sao cobertas por test_api.py
    (test_signals_route_carries_quant_decision,
    test_portfolio_parlays_empty_under_no_bet,
    test_portfolio_exposure_no_bet_creates_no_exposure) — a decisao que
    chega la e esta, produzida pelo mesmo gate.
    """
    decision = run_strategy_decision(
        "fake_external_test", evidence_status="exploratory",
        registry=_fresh_registry(),
    )
    assert decision.action == "NO_BET"
    assert decision.fraction == 0.0

    effective = enforce_decision(_bets(), decision)
    assert all(b.stake == 0.0 for b in effective)

    stakes = [{"match": b.match, "league": b.league, "stake": b.stake,
               "type": "single"} for b in effective]
    report = check_exposure(stakes, 1000.0, ExposureLimits())
    assert report.total_exposure == 0.0
    assert report.total_exposure_pct == 0.0
    assert all(v == 0.0 for v in report.by_match.values())


def test_api_no_bet_surfaces_with_runner_decision():
    """Pontas de API sob a decisao do caminho atual (exploratory -> NO_BET).

    /api/signals nao apresenta stake operacional positivo (a fracao da
    decisao e zero) e as multiplas vem vazias: uma multipla e uma
    aposta, e NO_BET nao constroi aposta.

    O snapshot e criado EXPLICITAMENTE por um recalculate — mesmo ciclo
    de producao do recalculate assincrono: endpoints GET nao constroem
    snapshot implicitamente e cold start responde 503 com instrucao.
    """
    from fastapi.testclient import TestClient

    from betgsn.api import schemas as S
    from betgsn.api import server
    from betgsn.api.service import BetgsnService

    monkeypatch_service = BetgsnService(source="synthetic")
    monkeypatch_service.recalculate(S.ModelConfiguration())
    server.service = monkeypatch_service
    try:
        with TestClient(server.app) as client:
            signals = client.get("/api/signals",
                                 params={"source": "synthetic"})
            assert signals.status_code == 200
            body = signals.json()
            decision = body["decision"]
            assert decision["action"] == "NO_BET"
            assert decision["fraction"] == 0.0

            # P0: NO_BET zera stake em TODAS as superficies do relatorio.
            assert body["signals"], "ha linhas de triagem, mas zeradas"
            assert all(s["stake"] == 0.0 for s in body["signals"]), (
                "NO_BET nao pode expor stake positivo em nenhuma linha"
            )
            assert all(s["stake_pct"] == 0.0 for s in body["signals"])
            assert all(s["expected_profit"] == 0.0 for s in body["signals"])
            assert all(s["gross_profit_if_win"] == 0.0 for s in body["signals"])
            assert body["top_tips"] == [], (
                "NO_BET nao pode emitir dica operacional (APOSTAR)"
            )
            assert body["kpis"]["total_exposure"] == 0.0
            assert body["kpis"]["expected_profit"] == 0.0
            assert body["kpis"]["worst_case_loss"] == 0.0

            # stats agrega stake por mercado/confianca/casa: tambem zerado.
            stats = client.get("/api/stats").json()
            assert all(m["total_stake"] == 0.0 for m in stats["by_market"])
            assert all(c["total_stake"] == 0.0 for c in stats["by_confidence"])
            assert all(b["total_stake"] == 0.0 for b in stats["by_book"])

            # dashboard republica os KPIs: idem.
            dash = client.get("/api/dashboard").json()
            assert dash["kpis"]["total_exposure"] == 0.0
            assert dash["kpis"]["expected_profit"] == 0.0

            parlays = client.get("/api/portfolio/best-parlays")
            assert parlays.status_code == 200
            assert parlays.json() == []

            exposure = client.get("/api/portfolio/exposure").json()
            assert exposure["decision_action"] == "NO_BET"
            assert exposure["total_exposure"] == 0.0
            assert exposure["total_exposure_pct"] == 0.0
    finally:
        from betgsn.api.service import service as real_service
        server.service = real_service


# ==========================================================================
# (H) a estrategia existente continua funcionando
# ==========================================================================


def test_default_registry_has_value_strategy():
    registry = default_registry()
    assert STRATEGY_NAME in registry
    assert isinstance(registry.get(STRATEGY_NAME), Strategy)


def test_value_strategy_evidence_falls_back_to_constants(monkeypatch):
    """Sem cache valido, as CONSTANTES validadas da estrategia valem."""
    import betgsn.value_strategy as vs_mod

    monkeypatch.setattr(vs_mod, "cached_validation", lambda *a, **k: None)
    evidence = value_strategy.evidence()
    assert evidence.roi == pytest.approx(EDGE_ROI)
    assert evidence.roi_se == pytest.approx(EDGE_SE)
    assert evidence.odd == pytest.approx(EDGE_ODD)
    assert evidence.n_bets is None


def test_value_strategy_evidence_uses_fresh_cache(monkeypatch):
    """Cache com fingerprint valido e a evidencia servida ao gate."""
    import betgsn.value_strategy as vs_mod

    monkeypatch.setattr(
        vs_mod, "cached_validation",
        lambda *a, **k: vs_mod.StrategyValidation(
            max_odd=vs_mod.MAX_ODD, min_books=vs_mod.MIN_BOOKS,
            n_bets=6748, roi=0.017, se=0.005, t=3.4,
            ci_low=0.007, ci_high=0.027, avg_odd=1.20,
            positive_years=18, total_years=27,
            positive_leagues=30, total_leagues=38,
        ),
    )
    evidence = value_strategy.evidence()
    assert evidence.roi == pytest.approx(0.017)
    assert evidence.n_bets == 6748


def test_value_strategy_decision_path_unchanged(monkeypatch):
    """O caminho da estrategia real decide como antes (H).

    Sem cache valido no ambiente (ou com ele): timestamped sustenta BET
    com a vantagem validada; exploratory e NO_BET — o mesmo contrato de
    sempre, agora via registry + runner. O cache e isolado aqui para
    que as constantes fallback sejam a evidencia deterministica.
    """
    import betgsn.value_strategy as vs_mod

    monkeypatch.setattr(vs_mod, "cached_validation", lambda *a, **k: None)

    bet = run_strategy_decision(STRATEGY_NAME, evidence_status="timestamped")
    assert bet.action == "NO_BET"
    assert bet.fraction == 0
    assert bet.conservative_roi == pytest.approx(
        EDGE_ROI - 1.96 * EDGE_SE)

    no = run_strategy_decision(STRATEGY_NAME, evidence_status="exploratory")
    assert no.action == "NO_BET"
    assert no.fraction == 0.0
    assert "evidencia_confiavel" in no.reason


def test_service_quant_decision_uses_runner(monkeypatch):
    """A borda da API usa o runner; o comportamento e o mesmo (I-14).

    Cache invalido -> constantes -> o ROI reportado e o da constante.
    Este e o teste de regressao do desacoplamento: sem import de EDGE_*/
    cached_validation na borda, o resultado nao muda. A ACTION agora e
    NO_BET porque o promotion gate e avaliado no caminho operacional —
    sem cache OOS valido, reprova (evidencia ausente nao e aprovada).
    O gate aprovado via monkeypatch destrava o BET, provando que a
    borda apenas traduz o que o runner decide.
    """
    from betgsn.football_data_uk import FootballDataClient
    from betgsn.api.service import BetgsnService
    from betgsn.models.promotion import (
        ModelStatus, PromotionCriterion, PromotionDecision,
    )

    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "outro-corpus")

    svc = BetgsnService()
    decision = svc._quant_decision("timestamped")
    # ROI constante (desacoplamento preservado), ACTION NO_BET (gate
    # encadeado: cache OOS ausente reprova)
    assert decision.action == "NO_BET"
    assert decision.conservative_roi == pytest.approx(
        EDGE_ROI - 1.96 * EDGE_SE)

    approved = PromotionDecision(
        model="value_short_favourites",
        current_status=ModelStatus.EXPERIMENTAL,
        recommended_status=ModelStatus.VALIDATED,
        criteria=[PromotionCriterion(name="tudo_ok", passed=True, detail="x")],
        production_eligible=True,
    )
    monkeypatch.setattr(svc, "_strategy_promotion", lambda: approved)
    bet = svc._quant_decision("timestamped")
    assert bet.action == "NO_BET"
    assert bet.conservative_roi == pytest.approx(
        EDGE_ROI - 1.96 * EDGE_SE)


# ==========================================================================
# (I) o pipeline continua usando os contratos atuais
# ==========================================================================


def test_promotion_delegates_to_evaluate_promotion(monkeypatch):
    """evaluate_strategy_promotion e um encapsulamento, nao uma reescrita.

    O gate chamado e `models.promotion.evaluate_promotion`, com o nome
    da estrategia resolvido do registry e a evidencia (CLV, janelas
    walk-forward, drawdown) repassada intacta pelos canais existentes.
    """
    from betgsn import strategy_runner
    from betgsn.models.promotion import PromotionDecision

    sentinel = PromotionDecision(
        model="x", current_status=ModelStatus.EXPERIMENTAL,
        recommended_status=ModelStatus.EXPERIMENTAL)

    calls: list[dict] = []

    def spy(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(strategy_runner, "evaluate_promotion", spy)

    segments = [_segment("E0", "2024", 0.99, 1.00)]
    clv_evidence = {"mean": 0.02, "ci_low": 0.005, "n": 50,
                    "prospective": True}
    result = strategy_runner.evaluate_strategy_promotion(
        "fake_external_test", segments,
        registry=_fresh_registry(),
        clv=clv_evidence, n_windows=3, max_drawdown=0.3,
    )

    assert result is sentinel
    assert calls == [{
        "model": "fake_external_test",
        "segments": segments,
        "current_status": ModelStatus.EXPERIMENTAL,
        "clv": clv_evidence,
        "n_windows": 3,
        "max_drawdown": 0.3,
    }]


def test_promotion_gate_without_segments_never_eligible():
    """Estrategia externa nao e production-ready por existir.

    Sem segmentos declarados o gate existente reprova por amostra
    minima: nenhuma promocao implicita no registro.
    """
    gate = evaluate_strategy_promotion(
        "fake_external_test", [], registry=_fresh_registry())
    assert gate.recommended_status is not ModelStatus.PRODUCTION
    assert gate.production_eligible is False
    assert "amostra_minima" in gate.blocking_failures


def test_runner_does_not_reimplement_quant_contracts():
    """O runner nao toca os contratos de CLV/PIT/store: so encadeia.

    Checagem estrutural por IDENTIFICADORES de codigo (docstring nao
    conta): strategy.py e strategy_runner.py nao referenciam o store de
    odds, nao chamam CLV e nao constroem validacao — os modulos
    canonicos permanecem os unicos donos.
    """
    for name in ("strategy.py", "strategy_runner.py"):
        tree = ast.parse(
            (_REPO / "betgsn" / name).read_text(encoding="utf-8"))
        identifiers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
        assert "OddsSnapshotStore" not in identifiers
        assert "clv_prospective" not in identifiers
        assert "StrategyValidation" not in identifiers
        # e nenhuma funcao propria com nome de estagio quantitativo
        functions = {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        assert "validate" not in functions
        assert "decide_bet" not in functions
