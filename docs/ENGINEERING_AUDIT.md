# Auditoria BETGSN — 2026-09-20

## Estado inicial

Entrypoints: `betgsn.py` (Tkinter, CLI, API, imports, estratégia e staking),
`betgsn/api/server.py` (FastAPI), `web/src/main.tsx` (React).
Núcleo: `model.py` ajusta ratings multiplicativos e Poisson independente com
correção Dixon-Coles (não há componente latente de Poisson bivariado).
`pipeline.analyze_fixture`, `signals.generate_signals` e `engine.py` são
compartilhados. `pipeline.run` é demonstração sintética.

Fontes: CSV football-data.co.uk, JSON de API-Football e snapshots The Odds API.
Persistência: caches em `output/`, SQLite `output/backtests/betgsn_backtest.db`;
resultados/sinais/métricas têm payload JSON. Nenhuma migração destrutiva necessária.
Frontend já consulta HTTP, mas a API serve dados sintéticos em grande parte.

## Defeitos confirmados na leitura

1. `real_signals._fit_on_real_history`: janela ancorada no último ano do corpus,
   sem cutoff. Cache não considera tempo nem todas as mudanças de arquivos.
2. `backtest_engine.run_backtest`: universo de times do futuro afeta normalização;
   `matches_before(match.kickoff)` perde `timezone`. Kickoff anterior não prova
   que o jogo terminou. Corte precisa considerar disponibilidade do resultado.
3. `model.fit_ratings`, `backtest_data.league_average_rating`: gols copiados para
   médias xG. `decay` ignorado; SOT = shots * 0.35; ausências viram constantes.
4. `backtest.py`: split único, média global da liga, chaves sem data,
   banca atualizada entre apostas do mesmo evento. Será adaptador do walk-forward.
5. `CsvRealOddsSource`: timestamp kickoff inventado para abertura/fechamento.
   CSV não comprova timestamp de publicação. Validação estrita deve recusar
   odds sem timestamp; análise de preços CSV deve ser explicitamente exploratória.
6. `apply_statistics`: associa casa/fora por magnitude do ID, não identidade.
7. Fixtures incluem Max/Avg como casas. `evaluate_market` remove vig entre
   múltiplas linhas independentes. Dupla chance exige soma 2, não 1.
8. Métricas probabilísticas no motor principal medem apenas sinais selecionados,
   não todas as previsões. Bootstrap por sinal ignora dependência entre apostas.
9. `value_strategy` tem afirmações de vantagem confirmada com análise do corpus
   inteiro; não constitui validação temporal de seleção de regra.

## Endpoints

| Rota | Origem inicial |
|---|---|
| dashboard, games, odds, odds/comparison, stats, model | sintético via pipeline.run |
| model/performance | backtest legado sobre dataset sintético |
| signals | real por padrão; synthetic explicitamente disponível |
| signals/status | pode fazer frontend trocar automaticamente para synthetic |
| backtest/* | fonte configurável, default sintético |
| health | confunde chave configurada com dado real consumido |

## Inventário funcional

Odds: `engine` (implied_prob/fair_probs/evaluate_market/scan_arbitrage),
`football_data_uk` (parsers/stores), `providers.odds_event_to_internal`,
`backtest_sources.OddsHistoryCache`, `backtest_data.*OddsSource`.
xG/form/ratings: `model.fit_ratings/expected_goals/_blend`,
`backtest_sources.apply_statistics`, `real_signals._fit_on_real_history`.
Backtest/calibração: `backtest`, `backtest_engine`, `backtest_metrics`,
`backtest_store`; `calibrate_ev` é desconto de EV legado, não calibrador probabilístico.
Signals/staking: `signals.generate_signals/build_report/scale_stakes_to_cap`,
`engine.stake_plan`, `staking` (Monte Carlo), `value_strategy` (CLI experimental).
Inventário de símbolos completo: `output/engineering/original-manifest.json`.

## Plano incremental

Modificar núcleo temporal/modelo/importadores/backtests; adicionar `features/`,
`models/`, benchmark e testes. Depois adaptar API/schemas/React e documentação.
Preservar CLI, GUI e baseline; ML/calibração/ensemble são experimentais até
comparação temporal. Dependências ML opcionais: numpy, sklearn, xgboost, lightgbm.
Riscos: mudança de semântica das métricas, contratos nullable, custo de refit,
dados históricos sem hora/publicação/xG, nomes de times sem IDs globais.

## Verificação inicial

Backend: 315 passed, 1 skipped; frontend: 127 passed. Sem repositório Git.
Snapshot de código (sem segredos/datasets): `output/engineering/original-source.zip`.
