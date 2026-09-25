"""BETGSN :: strategy_runner — o caminho unico da estrategia ao decision gate.

O que este modulo e
-------------------
O encadeador dos estagios que JA existem, na ordem da auditoria de
extensibilidade (FASE A):

    strategy (StrategyRegistry)
      -> validacao (evidence da estrategia: cache com fingerprint I-14
         ou constantes validadas da propria estrategia)
      -> promotion gate (`models.promotion.evaluate_promotion`, quando
         a evidencia de promocao e declarada)
      -> staking.decide_bet  (UNICO produtor de BetDecision)
      -> BET / NO_BET

O runner NAO reimplenta nenhum estagio. PIT, robustness e CLV nao sao
reescritos aqui: continuam exatamente onde estao — PIT no corte temporal
do corpus e do store (`datalayer.pointintime`, `HistoricalCorpus`),
robustness nas verificacoes por segmento (`models.robustness`) e CLV no
`OddsSnapshotStore` (entradas prospectivas registradas pelo caminho
operacional da API). Os resultados desses estagios CHEGAM ao gate pelos
canais que `evaluate_promotion` ja define (`clv`, `n_windows`,
`max_drawdown`, `improvement_ci`), e o gate decide com os criterios
existentes — nenhum criterio foi alterado.

Regras inviolaveis
------------------
- `staking.decide_bet` e o UNICO produtor de `BetDecision`. Este modulo
  nunca construi uma decisao: so a solicita com a evidencia da
  estrategia registrada.
- Uma estrategia com previsao positiva NAO produz BET por isso: sem
  evidencia confiavel (e, quando declarado, sem promotion gate
  aprovado), a resposta e NO_BET.
- Nenhuma promocao e implicita: `promotion=None` significa "gate nao
  avaliado" (comportamento anterior preservado), nunca "aprovado".
"""

from __future__ import annotations

from typing import Sequence

from .models.promotion import (
    ModelStatus,
    PromotionDecision,
    SegmentResult,
    evaluate_promotion,
)
from .staking import EVIDENCE_STATUSES, BetDecision, decide_bet
from .strategy import StrategyEvidence, StrategyRegistry, default_registry
from .production_policy import ProductionGate

__all__ = [
    "evaluate_strategy_promotion",
    "run_strategy_decision",
]


def evaluate_strategy_promotion(
    strategy_name: str,
    segments: Sequence[SegmentResult],
    *,
    current_status: ModelStatus = ModelStatus.EXPERIMENTAL,
    registry: StrategyRegistry | None = None,
    **gate_kwargs,
) -> PromotionDecision:
    """Promotion gate explicito para uma estrategia REGISTRADA.

    Encapsula `models.promotion.evaluate_promotion` com a estrategia
    resolvida do registry (o `model` da decisao e o `name` registrado).
    Nenhum criterio e alterado e NENHUMA promocao e implicita:

    - sem `segments` declarados, o gate reprova por amostra minima —
      uma estrategia externa nao entra como elegivel a producao so
      porque existe;
    - `current_status` comeca em EXPERIMENTAL: promover e decisao de
      evidencia (e humana), nao de registro.

    Evidencia opcional flui pelos canais EXISTENTES de
    `evaluate_promotion`: `clv` (CLV prospectivo do OddsSnapshotStore),
    `n_windows` (janelas walk-forward/PIT), `max_drawdown`,
    `improvement_ci`, `tuned_on_test`, entre outros.
    """
    strategy = (registry or default_registry()).get(strategy_name)
    return evaluate_promotion(
        model=strategy.name,
        segments=segments,
        current_status=current_status,
        **gate_kwargs,
    )


def run_strategy_decision(
    strategy_name: str,
    *,
    evidence_status: str,
    promotion: PromotionDecision | None = None,
    registry: StrategyRegistry | None = None,
    production_gate: ProductionGate | None = None,
) -> BetDecision:
    """Leva a estrategia registrada ao decision gate e devolve a decisao.

    Caminho: registry -> evidencia validada da estrategia -> (promotion
    gate, quando declarado) -> `staking.decide_bet`.

    - `evidence_status` precisa estar no vocabulario canonico
      (`staking.EVIDENCE_STATUSES`): status fora do vocabulario e erro
      de contrato, nao decisao — nao entra no pipeline.
    - `promotion` e o veredicto de `evaluate_strategy_promotion` (ou de
      `evaluate_promotion` direto). `None` = gate nao avaliado
      (comportamento anterior). Um gate reprovado forca NO_BET mesmo
      com odds confiaveis; um gate aprovado NAO basta sozinho — as
      verificacoes do decide_bet continuam todas valendo.

    A decisao devolvida e produzida por `staking.decide_bet`: este
    modulo nao construi nem altera `BetDecision`.
    """
    strategy = (registry or default_registry()).get(strategy_name)
    if evidence_status not in EVIDENCE_STATUSES:
        raise ValueError(
            f"evidence_status fora do vocabulario canonico: "
            f"{evidence_status!r} (validos: {', '.join(EVIDENCE_STATUSES)})"
        )
    evidence: StrategyEvidence = strategy.evidence()
    promotion_eligible = (
        None if promotion is None else bool(promotion.production_eligible)
    )
    return decide_bet(
        evidence.roi,
        evidence.roi_se,
        evidence.odd,
        evidence_status=evidence_status,
        n_bets=evidence.n_bets,
        promotion_eligible=promotion_eligible,
        production_gate=production_gate,
    )
