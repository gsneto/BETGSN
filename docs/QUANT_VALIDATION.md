# BETGSN — Validação Quantitativa Honesta (Agente 3 — QUANT)

Este documento descreve o que foi fortalecido em modelos, calibração,
ensemble, benchmarks, avaliação, backtest, métricas, EV, portfólio,
staking, robustez, ablação e gates de promoção — e, principalmente, o que
a evidência **não** permite concluir.

Regra que guia tudo aqui: **um resultado inconclusivo é um resultado.**
Não existe "edge comprovado" por conveniência.

---

## 1. Estado honesto da evidência (não esconder)

### 1.1 Aposta real (odds de fecho do football-data.co.uk)

`output/engineering/benchmark/odds_bands.json` — **101.673 apostas** em 5
ligas, preço = melhor odd entre ≥ 3 casas:

| faixa de odd | n | ROI | t | significativo 95% |
|---|---:|---:|---:|---|
| 1.00–1.10 | 27 | −0.5% | −0.10 | não |
| 1.10–1.20 | 390 | +1.7% | 0.93 | não |
| 1.20–1.30 | 1.127 | −1.3% | −0.90 | não |
| 1.30–1.40 | 1.476 | +0.2% | 0.13 | não |
| 1.40–1.60 | 3.999 | −1.1% | −0.95 | não |
| 1.60–2.00 | 9.670 | −0.9% | −1.03 | não |
| 2.00–3.00 | 21.602 | **−2.3%** | −2.79 | **sim (negativo)** |
| 3.00+ | 63.382 | **−6.5%** | −8.71 | **sim (negativo)** |

**Nenhuma faixa tem ROI positivo significativo.** As duas faixas
significativas são significativamente **negativas** — a margem da casa.
O resultado negativo fica preservado: não houve recorte que "salvasse" a
estratégia.

### 1.2 Ensemble (métrica probabilística, out-of-sample)

`output/engineering/quant/quant_validation.json` — 10 segmentos
(5 ligas × 2 temporadas):

| modelo | melhora LogLoss | t entre segmentos | consistência | status |
|---|---:|---:|---:|---|
| `1x2\|Ensemble` | +0.557% | 2.78 | 90% | VALIDATED, **não elegível a produção** |
| `btts\|Ensemble` | +0.544% | 2.44 | 80% | VALIDATED, **não elegível a produção** |

Leitura correta:

- o efeito é **estatisticamente distinguível de zero** (t ≈ 2,8);
- mas é **menor que a margem de erro medida (~5%)**;
- portanto o Ensemble é `VALIDATED` (evidência existe) e
  `PRODUCTION_ELIGIBLE = False` (efeito pequeno demais para dinheiro real).

`VALIDATED` **não** significa edge comprovado nem promoção a produção.

---

## 2. Contratos consumidos (não editados)

O Agente QUANT **não alterou** os contratos centrais proibidos:
`timeutil.py`, `markets.py`, `engine.py`, `signals.py`, `pipeline.py`,
`backtest_data.py`, `backtest_engine.py`. Todos foram lidos e usados como
estão (`HistoricalCorpus`, `settle_outcome`, `kelly_fraction`,
`TemporalBatch`, `walk_forward`).

---

## 3. O que foi adicionado

### 3.1 Comparação honesta — `betgsn/evaluation.py`

- `logloss_terms`, `brier_terms`, `rps_terms` — perda **por observação**.
- `paired_bootstrap(a, b, block_keys=...)` — IC bootstrap da diferença
  **pareada** (mesmas partidas), com reamostragem por **blocos** (mês) para
  não subestimar a dependência temporal.
- `block_bootstrap_ci` — IC de uma série com dependência temporal.
- `compare_models` — compara baseline vs candidato nas mesmas partidas,
  devolve melhora relativa, IC, p-valor e veredito.
- `comparison_verdict` — traduz IC em `melhora_robusta` / `melhora_pequena`
  / `piora_robusta` / `inconclusivo`.
- `min_detectable_effect` — menor efeito detectável dado n e sigma.

Por que importa: uma média agregada positiva pode ser indistinguível de
zero. O teste pareado remove a variância comum das partidas e é o mínimo
para chamar uma diferença de "real".

### 3.2 Validação walk-forward — `betgsn/models/validation.py`

- `build_folds` — janelas expansivas/rolantes via
  `walk_forward.windows`/`indices`, selecionadas por **disponibilidade do
  resultado** (`available_times`), não por data de calendário.
- `leakage_audit` — reprova sobreposição treino/validação/teste, reúso de
  índice de teste entre janelas, e bloco que usa partida cujo resultado só
  existia depois do fim do bloco.
- `walk_forward_validate` — roda o candidato contra o baseline em cada
  janela, agrega no teste e compara com block bootstrap. `tuned_on_test`
  marca o resultado como contaminado em vez de escondê-lo.
- `ensemble_ablation` — contribuição leave-one-out de cada membro.
- `weight_stability` — dispersão dos pesos entre janelas (CV).

### 3.3 Robustez por segmento — `betgsn/models/robustness.py`

- `segment_verdicts` — veredito por liga, temporada, mercado, faixa de
  odd, período e bookmaker.
- Regra: veredito forte exige **amostra suficiente E IC que exclui zero**.
  Segmento pequeno é sempre `amostra_insuficiente`.
- `robustness_summary` e `sample_size_summary` — quantos segmentos
  melhoram/pioram/são inconclusivos e por faixa de amostra.

### 3.4 Gate de promoção v2 — `betgsn/models/promotion.py`

Critérios **bloqueantes** novos:

| critério | regra |
|---|---|
| `amostra_total_minima` | ≥ 400 partidas úteis |
| `efeito_acima_do_ruido` | t entre segmentos ≥ 2 **ou** IC pareado com limite inferior > 0 |
| `sem_tuning_no_teste` | nenhum ajuste declarado no teste |
| `evidencia_oos_janelas` | ≥ 2 janelas walk-forward (quando informadas) |
| `clv_nao_negativo` | CLV médio > 0 e IC low > 0 (quando informado) |
| `drawdown_aceitavel` | drawdown ≤ 50% (quando informado) |

Critério **não bloqueante** (mede relevância econômica):

| critério | regra |
|---|---|
| `efeito_acima_da_margem` | melhora média ≥ margem de erro medida (5%) → `production_eligible` |

Duas camadas explícitas:

- `recommended_status = VALIDATED` → a evidência estatística existe;
- `production_eligible` → o efeito excede a margem de erro. O gate
  **nunca** concede `PRODUCTION`; isso continua decisão humana.

### 3.5 Staking — `betgsn/staking.py`

- `conservative_roi(roi, se)` — limite inferior (ROI − z·se).
- `decide_bet(...)` — decisão `BET` / `NO_BET` com verificações explícitas:
  evidência confiável, limite inferior positivo, amostra mínima, ruína
  tolerável. Dimensiona pelo **limite inferior**, não pela estimativa
  pontual.
- `PLANS["no_bet"]` — fração zero, para que "não apostar" seja uma opção
  de primeira classe.

### 3.6 Política de portfólio — `betgsn/portfolio/policy.py`

- `decide_portfolio(...)` — `BET` / `NO_BET` no nível do portfólio:
  evidência, EV conservador (com haircut), crescimento log esperado,
  ruína, drawdown e limites de exposição. NO_BET registra o motivo.

---

## 4. Testes

Novos arquivos:

- `tests/test_evaluation_statistics.py` — perdas por observação, bootstrap
  pareado e por bloco, vereditos, efeito real porém pequeno.
- `tests/test_quant_leakage.py` — resultado futuro, odds/feature futura,
  calibração/ensemble no próprio teste, janelas sobrepostas, embargo de
  48h, partidas sem kickoff.
- `tests/test_validation_walk_forward.py` — folds disjuntos, auditoria de
  leakage, walk-forward, tuning no teste, ablação de ensemble, estabilidade
  de pesos.
- `tests/test_robustness.py` — vereditos por segmento e por tamanho de
  amostra.
- `tests/test_portfolio_policy.py` — NO BET por evidência exploratória, EV
  negativo, exposição e ruína.
- `tests/test_promotion_gate.py` — critérios de ruído, margem, OOS,
  tuning, CLV, drawdown e elegibilidade a produção.
- `tests/test_staking.py` — NO BET e plano de fração zero.

Rodar:

```powershell
& "C:\Users\anton\Desktop\BETGSN\.venv\Scripts\python.exe" -m pytest -q
```

---

## 5. Como reproduzir a evidência

```powershell
# gate fortalecido sobre a evidência já persistida (sem rede, sem CSV)
python tools/quant_validation_report.py
```

Gera `output/engineering/quant/quant_validation.json` e imprime a tabela.
Também disponível `output/engineering/quant/promotion_gate_v2.json`.

Os benchmarks pesados (`benchmark.py`, `multi_benchmark.py`,
`side_markets_benchmark.py`, `ablation.py`) exigem os CSVs do
football-data.co.uk e por isso não rodam neste worktree; o gate foi
reavaliado sobre os relatórios já persistidos em `output/engineering/`.

---

## 6. Limitações declaradas

- Odds dos CSVs **não têm timestamp de publicação**: ROI é cenário
  exploratório, não evidência de execução. CLV é `null` por isso.
- Ligas não são independentes (mesmo período, mesmo mercado global): o
  desvio entre ligas **subestima** a incerteza real.
- O teste t entre segmentos trata segmentos como observações; por isso o
  critério preferencial é o IC pareado, quando disponível.
- Nenhum modelo foi promovido a produção. `PRODUCTION_MODEL` continua
  `BASELINE_V1`.

---

## 7. Conclusão

- Nenhum modelo é elegível a produção: os efeitos ficam abaixo da margem
  de erro medida (~5%).
- A evidência de aposta real não mostra vantagem positiva significativa em
  nenhuma faixa de odd.
- **NO BET** é o resultado honesto enquanto não houver evidência
  timestamped de CLV positivo e efeito acima do ruído.

O resultado negativo está preservado, versionado e auditável.

---

## 8. Etapa market/CLV validation (branch agent/market-clv-validation)

### 8.1 Auditoria do line-shopping (o efeito de ~+6,4pp)

`tools/line_shopping_audit.py` decompõe o efeito reportado entre
strategy_model COM e SEM line-shopping em duas partes, sobre o corpus
real (587.015 linhas apostáveis):

- **POPULAÇÃO CONSTANTE** (mesmas apostas da regra, preço variando):
  n=6.751; ROI ao melhor preço +1,61% [bootstrap +0,56%..+2,68%], à
  segunda melhor +0,71%, à mediana -0,36%, à pior -2,52%. Delta puro de
  preço **+1,97pp**, positivo em **24/24 janelas**, em todas as odd
  bands e todos os buckets de bookmakers.
- **POPULAÇÃO VARIÁVEL** (semântica da ablação `use_median`):
  reproduz o número reportado e mostra quanto dele é população
  diferente.
- **strategy_model (EV>0, n=181.172)**: as MESMAS apostas liquidadas ao
  melhor preço (-4,89%) e à mediana (-11,13%) → delta puro de preço
  **+6,24pp** do +6,42pp reportado (~0,18pp é efeito de população).

Leitura honesta: o efeito é essencialmente de PREÇO (best vs median na
mesma população), estável temporalmente e não depende de poucos books,
poucas janelas ou bandas específicas. LIMITAÇÕES: as odds do corpus não
têm timestamp de publicação — quote age e time-to-kickoff históricos
são indemonstráveis (perguntas B/C/E); o caminho operacional continua
PIT via `OddsSnapshotStore.line_at` (testado). Auditoria, não tuning:
nenhum parâmetro foi ajustado.

### 8.2 CLV prospectivo — ciclo de vida e proveniência

Store schema v4: entradas de CLV carregam home/away/league/casa
representativa/execution_status (migração ALTER TABLE, append-only).
Estados explícitos do ciclo de vida:

| estado | significado |
|---|---|
| PENDING | kickoff no futuro — fechamento ainda pode chegar |
| NO_CLOSE | kickoff passou sem fechamento válido (nunca CLV=0) |
| CLOSED | entry < closing < kickoff — CLV calculado |
| INVALID | dado inconsistente (fechamento antes da entrada etc.) |
| MISMATCH | entrada sem observação correspondente no store |

`clv_lifecycle_sweep` é a passada operacional (leitura, idempotente).
Executabilidade: `execution_status=UNKNOWN` — preço observado na decisão
nunca é presumido igual ao preço executado.

### 8.3 Modelos experimentais nas MESMAS 24 janelas

`tools/ml_oos_validation.py` avalia Elo/XGBoost/LightGBM no MESMO
protocolo da Etapa 19 (`ml_walkforward.MLWindowAdapter` plugado ao
`run_model_walkforward` via `model_fn`): features point-in-time,
treino apenas com partidas anteriores a train_end, early stopping em
split temporal interno do TRAIN, hiperparâmetros default (sem tuning
OOS). Ensemble: PENDENTE (exige stacking OOS por janela). Sem ranking,
sem vencedor, sem promoção.

Resultado real (546.533 linhas OOS, 24/24 janelas válidas, corpus
2026-09-23; cada confronto resolvido pelo DIA da partida — providers de
features nunca respondem pelo jogo errado):

| modelo | LogLoss (raw) | delta vs market_raw | veredito pareado |
|---|---:|---:|---|
| MARKET_RAW | 0,5875 | — | — |
| MARKET_FAIR | 0,5876 | — | — |
| Elo | 0,5968 | +0,0093 | piora_robusta |
| XGBoost | 0,5944 | +0,0069 | piora_robusta |
| LightGBM | 0,5944 | +0,0068 | piora_robusta |

**Nenhum modelo experimental adiciona informação além do mercado** —
todos pioram de forma robusta contra market_raw E market_fair
(block bootstrap por mês). Os boosters superam o BASELINE_V1
(0,6070), mas continuam abaixo do mercado. Nenhum ranking, nenhum
vencedor, nenhuma promoção.

### 8.4 Observabilidade

`/api/quant/*` (benchmarks, model-vs-market, line-shopping, ml,
clv/status): leitura pura, fingerprint validado, estados explícitos
MISSING/STALE/NO_VALID_CACHE. `tools/benchmark_manifest.py` congela a
referência da Etapa 19 (corpus, protocolo, fingerprints, versões) com
estado de reprodutibilidade por cache. Frontend: aba Quant.

### 8.5 Conclusão da fase

- O ganho do line-shopping é real COMO PREÇO, mas não muda o veredito:
  strategy_model segue negativa em qualquer preço.
- CLV prospectivo: 480 entradas PENDING (kickoffs futuros), n=0 com CLV
  válido — gate segue bloqueado, **NO BET** permanece.
- production_eligible = false inalterado; promotion gate intocado.
