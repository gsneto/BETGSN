# BETGSN — Governança de Desenvolvimento Paralelo (4 Agentes)

Documento gerado após auditoria completa do código real. Estado do repositório:
commit `b7d14e3` na branch `main`, working tree limpo, sem remote (GitHub não
autenticado).

---

## 1. Auditoria de dependências (imports reais entre módulos `betgsn/`)

Contagem de importadores por módulo (mais importado = mais sensível a mudança):

| Módulo | Importadores | Natureza |
|---|---:|---|
| `timeutil` | 22 | leaf — parse de kickoff/UTC |
| `model` | 15 | núcleo estatístico (Poisson, ratings, ScoreMatrix, HistoricalMatch) |
| `football_data_uk` | 10 | fonte CSV de resultados+odds |
| `models.base` | 8 | contratos ML (TemporalBatch, probabilities) |
| `backtest_data` | 8 | corpus point-in-time + settlement |
| `markets` | 8 | probabilidades por mercado |
| `data` | 7 | dataset sintético |
| `providers` | 7 | APIs externas (Odds API, API-Football) |
| `signals` | 7 | gerador de sinais |
| `pipeline` | 6 | orquestração |
| `engine` | 6 | matemática de odds/Kelly |
| `temporal` | 5 | política de disponibilidade (48h) |
| `backtest_engine` | 5 | walk-forward |

Módulos criados mas **ainda não integrados** (importadores = 0): `canonical`,
`cache`, `quota`, `data_quality`, `xg_sources`. São o ponto de partida dos
agentes 1 e 2.

---

## 2. Divisão por domínio (4 agentes)

O objetivo é **maximizar trabalho paralelo e minimizar conflito de Git**.
Cada agente tem um domínio com arquivos próprios; os arquivos centrais
acima ficam **proibidos de editar por todos**, exceto sob coordenação.

### AGENTE 1 — DATA / FIXTURES
Domínio: ingesta, future fixtures, resultados, normalização, entity matching,
cache, qualidade de dados.
- **Pode editar**: `canonical.py`, `cache.py`, `quota.py`, `data_quality.py`,
  `football_data_uk.py`, `backtest_sources.py`, `xg_sources.py`,
  `tests/test_canonical.py`, `tests/test_infrastructure.py`,
  `tests/test_football_data_uk.py`, `tests/test_backtest_sources.py`.
- **Proibido**: `model.py`, `markets.py`, `engine.py`, `signals.py`,
  `pipeline.py`, `providers.py` (compartilhado com Agente 2 — ver contratos).

### AGENTE 2 — ODDS / BOOKMAKERS
Domínio: providers de odds, bookmakers, snapshots, timestamps, movement,
no-vig, fair odds, CLV prospectivo.
- **Pode editar**: `odds_snapshots.py`, `features/movement.py`,
  `features/odds.py`, `providers.py` (contrato de odds), `value_strategy.py`,
  `tests/test_odds_snapshots.py`, `tests/test_odds_movement.py`.
- **Proibido**: `engine.py` (usa `consensus_fair_probs`/`evaluate_market` sem
  alterá-los), `signals.py`, `backtest_engine.py`.

### AGENTE 3 — QUANT / MODELS / SIGNALS
Domínio: modelos, ensemble, mercados, EV, edge, sinais, backtest, validação,
portfolio, staking, robustness.
- **Pode editar**: `betgsn/models/**` (exceto contratos `models/base.py`),
  `portfolio/**`, `benchmark.py`, `multi_benchmark.py`, `side_markets_benchmark.py`,
  `ablation.py`, `evaluation.py`, `backtest_metrics.py`, `backtest.py`,
  `backtest_compat.py`, `backtest_store.py`, `staking.py`,
  `tests/test_models_evaluation.py`, `tests/test_benchmark.py`,
  `tests/test_portfolio*.py`, `tests/test_side_markets_and_ablation.py`,
  `tests/test_promotion_gate.py`, `tests/test_staking.py`.
- **Proibido**: `model.py`, `markets.py`, `engine.py`, `signals.py`,
  `pipeline.py`, `backtest_data.py`, `backtest_engine.py` (leitura apenas).

### AGENTE 4 — FRONTEND / API
Domínio: React, páginas, dashboards, integração, visualização, UX, contratos
de API.
- **Pode editar**: `web/src/**`, `betgsn/api/schemas.py`,
  `betgsn/api/service.py`, `betgsn/api/server.py`, `betgsn/api/prediction_schemas.py`,
  `betgsn/api/backtest_schemas.py`, `betgsn/api/backtest_service.py`,
  `web/src/**/*.test.*`, `tests/test_api.py`, `tests/test_backtest_api.py`.
- **Proibido**: todo `betgsn/` fora de `api/` (a API é só apresentação).

---

## 3. Contratos estáveis (não editáveis sem coordenação)

| Contrato | Dono | Consumidores | Risco de breaking change |
|---|---|---|---|
| `HistoricalMatch`, `Fixture`, `TeamRating`, `ScoreMatrix` (model.py) | coordenador | todos | ALTO |
| `settle_outcome`, `HistoricalCorpus` (backtest_data.py) | coordenador | 1, 3 | ALTO |
| `market_1x2/ou/btts/...` (markets.py) | coordenador | 3, 4 | ALTO |
| `evaluate_market`, `consensus_fair_probs`, `implied_prob` (engine.py) | coordenador | 2, 3 | ALTO |
| `Signal`, `generate_signals`, `build_report` (signals.py) | coordenador | 3, 4 | ALTO |
| `analyze_fixture` (pipeline.py) | coordenador | 1, 3 | ALTO |
| Schemas Pydantic (api/schemas.py) | Agente 4 | todos | MÉDIO |

Regra: mudanças em contratos exigem abrir **PR separado**, aprovado antes de
qualquer branch de agente que dependa dela.

---

## 4. Branches

```
main            (produção estável)
├── agent/data
├── agent/odds
├── agent/quant
└── agent/frontend
```

- Cada agente trabalha em sua branch, faz commits frequentes e claros.
- Merge: `main` <- branch do agente, um por vez, na ordem de dependência:
  1. contratos (se houver) → 2. data → 3. odds → 4. quant → 5. frontend.
- Rebase local antes de abrir PR para `main` (evita merges sujos).
- Nenhum merge direto entre branches de agentes (só via `main`).

---

## 5. Ordem de execução

- **ETAPA 0** — Git pronto, contratos congelados, branches criadas.
- **ETAPA 1** — Trabalho paralelo dos 4 agentes (domínios disjuntos).
- **ETAPA 2** — Primeira integração (data + odds primeiro).
- **ETAPA 3** — Novo ciclo paralelo (quant consome data/odds; frontend consome API).
- **ETAPA 4** — Integração final.
- **ETAPA 5** — Suíte completa (backend + frontend + build).
- **ETAPA 6** — Release (tag + documentação).
