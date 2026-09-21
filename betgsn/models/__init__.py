"""Registro de status dos modelos.

PRODUCTION continua BASELINE_V1. O ensemble passou no gate de promocao e
esta VALIDATED, que NAO e a mesma coisa: validado significa que a
evidencia estatistica existe, nao que o modelo ja governa as decisoes.

Evidencia do ensemble (tools/cross_season_gate.py, 2026-09-20):
  1x2  -> melhora media de logloss +0.56%, consistencia 90%
  btts -> melhora media de logloss +0.54%, consistencia 80%
  10 segmentos: 5 ligas (E0, SP1, I1, D1, F1) x 2 temporadas (2024, 2025)

A melhora e pequena. Ela e levada a serio porque e CONSISTENTE: apareceu
em 9 dos 10 segmentos em 1x2, nao numa liga sortuda. XGBoost e LightGBM
isolados falharam o gate — varios com melhora media NEGATIVA. Calibracao
isotonica degradou todos os modelos em ambas as temporadas.

Limite honesto dessa evidencia (revisao quantitativa)
-----------------------------------------------------
O efeito (+0,56%) e MENOR que a margem de erro medida do experimento
(~5%). Estatisticamente ele nao e indistinguivel de zero (t ~2,8 entre os
10 segmentos), mas tambem NAO e grande o bastante para sustentar dinheiro
real. Por isso o Ensemble continua VALIDATED e continua PRODUCTION_ELIGIBLE
= False: VALIDATED aqui significa "a evidencia estatistica existe", nunca
"edge comprovado" nem "pode ir para producao".

A evidencia de aposta real (101.673 apostas em 5 ligas) nao mostra vantagem
positiva significativa em NENHUMA faixa de odd; as faixas 2.00-3.00 e 3.00+
sao significativamente NEGATIVAS. NO BET e um resultado valido.

Trocar o modelo de producao e decisao explicita de quem opera o sistema,
nunca um efeito colateral de um benchmark passar.
"""
BASELINE_VERSION = "BASELINE_V1"
PRODUCTION_MODEL = BASELINE_VERSION

#: Status por modelo. Fonte unica de verdade para UI, API e relatorios.
MODEL_STATUS: dict[str, str] = {
    "BASELINE_V1": "PRODUCTION",
    "Poisson": "PRODUCTION",
    "DixonColes": "PRODUCTION",
    "Ensemble": "VALIDATED",
    "Elo": "EXPERIMENTAL",
    "XGBoost": "EXPERIMENTAL",
    "LightGBM": "EXPERIMENTAL",
}

#: Onde a evidencia de cada promocao esta registrada (gate original).
PROMOTION_EVIDENCE = "output/engineering/benchmark/cross_season_gate.json"

#: Evidencia do gate FORTALECIDO (ruido + margem + OOS + tuning), gerada
#: por `tools/quant_validation_report.py`. Inclui `production_eligible` e a
#: leitura honesta da evidencia financeira (nenhuma faixa de odd com ROI
#: positivo significativo em 101.673 apostas).
QUANT_EVIDENCE = "output/engineering/quant/quant_validation.json"

#: Margem de erro medida do experimento (melhora relativa de LogLoss).
#: Efeito abaixo disso e real na direcao, mas pequeno demais para dinheiro
#: real. Ver `models.promotion.MIN_MEANINGFUL_IMPROVEMENT`.
MEASURED_ERROR_MARGIN = 0.05

#: Elegibilidade a producao. Diferente de MODEL_STATUS: mede se o efeito
#: excede a margem de erro medida. O Ensemble e VALIDATED (evidencia
#: estatistica) mas NAO elegivel (efeito 0,56% < 5%). Decisao de producao
#: continua humana e explicita.
PRODUCTION_ELIGIBLE: dict[str, bool] = {
    "BASELINE_V1": True,
    "Poisson": False,
    "DixonColes": False,
    "Ensemble": False,
    "Elo": False,
    "XGBoost": False,
    "LightGBM": False,
}

#: Status dos modelos de mercados secundários. Avaliados em UM ano de teste
#: (2025) — insuficiente para VALIDATED; permanecem EXPERIMENTAL até
#: validação multi-temporada. NegBin supera Poisson em corners (overdispersion
#: confirmado); XGBoost/LightGBM superam os paramétricos em cards.
SIDE_MARKET_STATUS: dict[str, str] = {
    "CORNERS_POISSON": "EXPERIMENTAL",
    "CORNERS_NEGBIN": "EXPERIMENTAL",
    "CORNERS_XGBOOST": "EXPERIMENTAL",
    "CORNERS_LIGHTGBM": "EXPERIMENTAL",
    "CARDS_POISSON": "EXPERIMENTAL",
    "CARDS_NEGBIN": "EXPERIMENTAL",
    "CARDS_XGBOOST": "EXPERIMENTAL",
    "CARDS_LIGHTGBM": "EXPERIMENTAL",
}
