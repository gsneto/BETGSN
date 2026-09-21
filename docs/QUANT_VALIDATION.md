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
