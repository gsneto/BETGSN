# BETGSN — entrega incremental verificada

## Benchmark out-of-sample

Premier League (E0), teste 2025, 371 partidas. Treino 2015–2021; early stopping 2022; ensemble 2023; calibração 2024.
Probabilidades temporais; métricas financeiras abaixo são cenário exploratório com preços CSV sem timestamp. CLV indisponível.

|Modelo 1X2|Brier|Log Loss|RPS|ECE|ROI/banca|Yield|Drawdown|Apostas|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|BASELINE_V1|0.5766|0.9704|0.2043|0.0186|100.38%|22.46%|13.85%|400|
|BASELINE_V1_platt|0.5802|0.9768|0.2050|0.0151|8.39%|2.10%|17.87%|481|
|BASELINE_V1_isotonic|0.5939|1.0542|0.2092|0.0372|-22.99%|-5.87%|33.83%|505|
|XGBoost|0.5833|0.9816|0.2071|0.0296|16.55%|4.69%|17.11%|363|
|XGBoost_platt|0.5869|0.9889|0.2080|0.0226|16.10%|3.93%|22.81%|446|
|XGBoost_isotonic|0.5949|1.0620|0.2110|0.0478|7.59%|1.73%|18.35%|483|
|LightGBM|0.5865|0.9850|0.2083|0.0270|10.59%|3.12%|23.74%|368|
|LightGBM_platt|0.5896|0.9918|0.2092|0.0215|30.26%|6.90%|23.59%|453|
|LightGBM_isotonic|0.5946|1.0596|0.2106|0.0286|3.66%|0.90%|20.95%|463|
|Elo|0.5778|0.9704|0.2040|0.0129|32.34%|10.44%|15.24%|349|
|Elo_platt|0.5829|0.9790|0.2048|0.0245|-8.96%|-2.67%|28.84%|466|
|Elo_isotonic|0.5920|1.0471|0.2086|0.0315|3.07%|0.86%|32.07%|474|
|Poisson|0.5759|0.9691|0.2042|0.0185|102.20%|23.70%|13.07%|355|
|Poisson_platt|0.5802|0.9768|0.2050|0.0162|7.88%|1.98%|18.01%|480|
|Poisson_isotonic|0.5943|1.0554|0.2095|0.0399|-20.60%|-5.12%|32.09%|508|
|Ensemble|0.5744|0.9649|0.2027|0.0102|55.85%|16.87%|10.54%|337|
|Ensemble_platt|0.5792|0.9726|0.2037|0.0124|-6.53%|-1.99%|30.65%|460|
|Ensemble_isotonic|0.5914|1.0210|0.2082|0.0384|-13.90%|-3.73%|35.96%|493|

Nenhum modelo promovido. Uma liga/ano não comprova vantagem generalizável. Isotonic/Platt não garantem melhora.
O benchmark inclui também Over/Under e BTTS; resultados completos em `output/engineering/benchmark/report.json`.

## Arquivos modificados
- `betgsn/api/backtest_schemas.py`
- `betgsn/api/backtest_service.py`
- `betgsn/api/schemas.py`
- `betgsn/api/server.py`
- `betgsn/api/service.py`
- `betgsn/backtest.py`
- `betgsn/backtest_data.py`
- `betgsn/backtest_engine.py`
- `betgsn/backtest_metrics.py`
- `betgsn/backtest_sources.py`
- `betgsn/backtest_store.py`
- `betgsn/data.py`
- `betgsn/engine.py`
- `betgsn/football_data_uk.py`
- `betgsn/markets.py`
- `betgsn/model.py`
- `betgsn/pipeline.py`
- `betgsn/real_signals.py`
- `tests/test_api.py`
- `tests/test_backtest_api.py`
- `tests/test_backtest_sources.py`
- `tests/test_real_signals.py`
- `web/src/App.tsx`
- `web/src/pages/SignalsPage.tsx`
- `web/src/pages/StatsPage.tsx`
- `web/src/types/api.ts`
- `web/src/utils/format.ts`

## Arquivos criados
- `betgsn/api/prediction_schemas.py`
- `betgsn/backtest_compat.py`
- `betgsn/benchmark.py`
- `betgsn/cache.py`
- `betgsn/canonical.py`
- `betgsn/config.py`
- `betgsn/data_quality.py`
- `betgsn/evaluation.py`
- `betgsn/features/__init__.py`
- `betgsn/features/builder.py`
- `betgsn/features/elo.py`
- `betgsn/features/form.py`
- `betgsn/features/h2h.py`
- `betgsn/features/odds.py`
- `betgsn/features/rest.py`
- `betgsn/features/strength.py`
- `betgsn/features/xg.py`
- `betgsn/models/__init__.py`
- `betgsn/models/base.py`
- `betgsn/models/calibration.py`
- `betgsn/models/cards.py`
- `betgsn/models/corners.py`
- `betgsn/models/dixon_coles.py`
- `betgsn/models/elo.py`
- `betgsn/models/ensemble.py`
- `betgsn/models/lightgbm_model.py`
- `betgsn/models/poisson.py`
- `betgsn/models/referee.py`
- `betgsn/models/walk_forward.py`
- `betgsn/models/xgboost_model.py`
- `betgsn/portfolio/__init__.py`
- `betgsn/portfolio/correlation.py`
- `betgsn/portfolio/parlay.py`
- `betgsn/portfolio/risk.py`
- `betgsn/quota.py`
- `betgsn/temporal.py`
- `betgsn/xg_sources.py`
- `docs/ENGINEERING_AUDIT.md`
- `docs/ENGINEERING_DELIVERY.md`
- `tests/test_benchmark.py`
- `tests/test_canonical.py`
- `tests/test_corners_cards.py`
- `tests/test_features.py`
- `tests/test_infrastructure.py`
- `tests/test_market_groups.py`
- `tests/test_models_evaluation.py`
- `tests/test_portfolio.py`
- `tests/test_real_contracts.py`
- `tests/test_temporal_foundation.py`
- `tests/test_walk_forward_windows.py`
- `tests/test_xg_foundation.py`
- `tools/audit_snapshot.py`
- `tools/engineering_report.py`
- `web/src/components/DataProvenance.test.tsx`
- `web/src/components/DataProvenance.tsx`
- `requirements-ml.txt`

## Dependências opcionais instaladas
numpy 2.4.6; scikit-learn 1.9.1; xgboost 3.2.0; lightgbm 4.7.0 (mais dependências transitivas).

## Verificação em 2026-09-20
- `python -m pytest tests -q --tb=short`: 342 passaram, 2 ignorados e 4 avisos de depreciação; código de saída 0.
- Teste determinístico confirma uso das cotações fornecidas e retorno vazio quando nenhuma oportunidade passa pelo filtro de EV.

## Limitações e próximos passos
- A entrega não satisfaz ainda todos os critérios do pedido original; módulos experimentais exigem integração adicional.
- Odds CSV sem timestamp não são evidência point-in-time de execução. Motor estrito recusa; CLV exige snapshots de entrada/fechamento.
- xG externo requer fonte e timestamp. Football-data.co.uk não fornece xG. Nenhum conector de xG novo foi ativado.
- Features novas alimentam benchmark; produção continua no baseline. Calibradores/ensemble não foram promovidos.
- Drift PSI é infraestrutura; falta integração operacional para performance/calibração/CLV e thresholds configurados por série.
- Benchmark ainda não executa múltiplos folds rolling/expanding para ML; só split temporal em cinco blocos.
- Elo/contexto de força adversária, identidade global de times e fuso exato das fontes requerem validação adicional.
- Backtest guarda métricas de todas as previsões; UI ainda não apresenta todos os novos campos.
- Código split legado aposentado permanece privado para referência; API pública delega ao motor principal.
- Estatísticas secundárias legadas ainda têm imputações constantes; não foram remodeladas nesta entrega.
- De-vig agora separa linhas, exige grupos conhecidos completos e trata dupla chance com soma 2. EV/Kelly de handicaps com devolução ainda requer tratamento específico.
- Janelas rolling/expanding e filtro de disponibilidade implementados; falta conectá-los à execução multifold do benchmark.
- Snapshot real conferido em 2026-09-20: 17 jogos futuros, 35.843 partidas históricas e contagem do histórico consistente.
- Os relatórios históricos antigos não foram reclassificados nem apagados.
