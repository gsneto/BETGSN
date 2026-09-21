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

#: Onde a evidencia de cada promocao esta registrada.
PROMOTION_EVIDENCE = "output/engineering/benchmark/cross_season_gate.json"

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
