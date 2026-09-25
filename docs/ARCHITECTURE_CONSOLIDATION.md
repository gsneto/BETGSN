# BETGSN — Plano de consolidação de arquitetura (P2)

Classificação das duplicações e módulos encontrados na auditoria. **Nada é
removido nesta fase** — o objetivo é decidir com evidência o que é
USED / UNUSED / LEGACY / DUPLICATE e a ordem segura de consolidação.

## Fonte de verdade (produção)

O runtime operacional usa os módulos legados, não `betgsn/datalayer/*`:

| Concern | Fonte de verdade (produção) | Evidência |
|---|---|---|
| Data/CSV | `football_data_uk.py` + `backtest_data.HistoricalCorpus` | importados por `pipeline`, `real_signals`, `backtest_engine` |
| Odds | `providers.py` + `odds_normalize.py` + `odds_snapshots.py` | capture/engine/API |
| PIT | `temporal.result_time` + `available_before` | `backtest_data`, `model_walkforward`, `features/builder` |
| Health/quota | `odds_health.py` (`HealthTracker`/`CreditController`/`CircuitBreaker`) | capture + API |
| Gate | `production_policy.py` (7 blocos) + `live_gate.build_live_gate` | `staking.decide_bet` |

## Classificação

| Módulo / duplicação | Classe | Ação proposta |
|---|---|---|
| `betgsn/datalayer/*` (14 arquivos) | **UNUSED** | Nenhum import de produção (só `tests/test_data_layer.py`). Manter como referência ou remover em PR dedicado, após o teste ser removido/arquivado. |
| `betgsn/data.py` | **LEGACY** (demo sintético) | Usado por `pipeline` (dataset sintético) e testes. Manter. |
| `betgsn/canonical.py`, `cache.py`, `quota.py`, `data_quality.py`, `xg_sources.py` | **UNUSED** (fora do datalayer) | Só alcançáveis via datalayer. Candidatos à remoção junto do datalayer. |
| `model_calibration.py` vs `models/calibration.py` vs `real_signals.MODEL_CALIBRATION` | **DUPLICATE** (3) | Manter as duas primeiras (contextos diferentes: OOS vs strategy harness). A constante de `real_signals` é a calibração LIVE exibida — documentar como provisória até parear o calibrador por janela. |
| `models/ensemble.py` vs `ml_walkforward.EnsembleWindowAdapter` | **DUPLICATE** (2) | Ensemble ponderado (benchmark) vs stacking por janela (OOS). Consolidar só após unificar o contrato de avaliação. |
| `models/promotion.py` vs `production_policy.py` | **COMPOSED** (não duplicado) | `models/promotion` embute `ProductionGate`. Manter; documentar que `production_policy` é a fonte de elegibilidade LIVE. |
| `odds_normalize.event_key` vs `football_data_uk.CsvMatch.match_key` vs `canonical.CanonicalMatch.match_key` | **DUPLICATE** (3 formatos) | Odds usam `event_key`; corpus usa `match_key`. Adapter explícito já existe (`FixtureMatchIndex`). Não unificar sem PR dedicado. |
| `signals.py` vs `realtime/signals.py` vs `real_signals.py` | **DISTINTOS** | `signals.py` = triagem EV/stake (SINAIS); `realtime/signals.py` = 9 fatos de mercado (LIVE); `real_signals.py` = adapter de fonte. Manter. |
| `probability_contract.py` | **UNUSED** | Só testes. Candidato a integrar ou remover. |
| `model_training.py` | **UNUSED** | Só testes. Candidato a remover. |
| `models/dixon_coles.py` | **UNUSED** | Ninguém importa (nem testes). Remover ou integrar. |
| `models/validation.py`, `walk_forward.py`, `robustness.py`, `ablation.py`, `models/referee.py` | **UNUSED em produção** (test-only) | Ferramentas de pesquisa. Manter como biblioteca documentada ou mover para `tools/`. |
| `gui.py`, `widgets.py`, `theme.py` | **LEGACY** (GUI Tkinter) | Só via CLI sem argumento. Manter até o terminal web substituir. |
| `backtest_compat.py` | **LEGACY shim** | `backtest.py` reexporta `run_legacy`. Manter. |
| `multi_benchmark.py`, `side_markets_benchmark.py` | **EXPERIMENTAL** | Harnesses offline sem teste. Documentar como pesquisa. |

## Ordem segura de consolidação (quando aprovada)

1. Remover `betgsn/datalayer/*` + `tests/test_data_layer.py` e os módulos
   órfãos (`canonical`, `cache`, `quota`, `data_quality`, `xg_sources`) —
   **somente** depois de confirmar zero imports de produção (já confirmado).
2. Remover `probability_contract.py`, `model_training.py`,
   `models/dixon_coles.py` (sem consumidores).
3. Documentar os 3 calibradores e os 2 ensembles; unificar contratos.
4. Mover validadores test-only para `tools/research/` se virarem
   executáveis.

## Não fazer

- Não deletar módulos com testes verdes sem remover/arquivar o teste no
  mesmo PR.
- Não fundir `event_key` e `match_key` sem um adapter e testes de
  equivalência — a separação é intencional (odds vs corpus).
