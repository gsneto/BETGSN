# BETGSN — Alpha Lab

Laboratório quantitativo do terminal de mercado. Objetivo: descobrir
**honestamente** se um sinal antecipa movimento de preço — não fabricar ROI.

## Fluxo

```
PROVIDERS → CAPTURE → NORMALIZE → MATCH → SNAPSHOT → MOVEMENT
  → MARKET RAW/FAIR → SIGNALS → ALPHA LAB → CLV → EXECUTION GAP
  → OOS → ROBUSTNESS → PROMOTION GATE → NO_BET | PRODUCTION
```

## Princípios não negociáveis

1. **PIT estrito**: o estado do sinal em T usa só quotes `<= T`; o futuro é
   lido apenas para MEDIR (`market_move` em +5/15/30/60min, closing).
2. **Sem lógica paralela**: thresholds vêm de `SignalRules` (produção); as
   métricas de `signal_calibration` são as mesmas dos sinais.
3. **Sem fabricar dado**: ausência de fechamento → CLV BLOCKED; ausência de
   execução → EXECUTION UNKNOWN; amostra curta → INSUFFICIENT_DATA.
4. **Veredito por evidência**: n, IC bootstrap, consistência por dimensão.
   ROI isolado nunca decide.
5. **Nunca promover sem gate**: `PRODUCTION_CANDIDATE` exige OOS + CLV
   n≥200 + execução medida. Hoje: NO_BET.

## Módulos

| Módulo | Papel |
|---|---|
| `betgsn/market_dataset.py` | estado PIT por linha (best/2nd/median/worst/dispersion/book_count) + fingerprint |
| `betgsn/alpha_lab.py` | hipóteses, observação forward, avaliação estatística, ablation |
| `betgsn/alpha_replay.py` | replay PIT do store gerando observações dos sinais |
| `betgsn/market_audit.py` | MARKET_RAW vs MARKET_FAIR por mercado (overround, complete) |
| `betgsn/clv_dataset.py` | progresso CLV (closed/200) + dataset com referências de fechamento |
| `betgsn/quota_scheduler.py` | estados de quota + cooldown + WAITING_FOR_PROVIDER_QUOTA |
| `betgsn/signal_registry.py` | status central por sinal/alpha |
| `tools/alpha_lab_run.py` | executa e grava os artefatos |
| `tools/provider_diagnostics.py` | ProviderStatus estruturado (sem segredos) |
| `tools/research_daily.py` | orquestrador idempotente (capture → sweep → alpha lab) |

## Replay intra-line

O replay reconstrói, por linha (evento + mercado + resultado), a sequência
cronológica de preços e mede o movimento POSTERIOR em horizontes curtos:

```
+1m  +5m  +10m  +15m  +30m  +60m  → closing (quando existir)
```

Para cada sinal: `signal_timestamp`, `price_at_signal`, `future_price`,
`movement`, `movement_direction`, `movement_magnitude`, `closing_price`.
Nunca usa dados anteriores como posteriores.

## Segmentação

Cada alpha é quebrado por: `market`, `league`, `book`, `odds_band`,
`ttk_band` (tempo até o kickoff), `gap_band` (best-vs-median),
`book_band` (número de casas). A consistência é medida por dimensão:
efeito estável exige ≥55% dos subgrupos (n≥10) do mesmo lado do geral.

## CLV dataset e progresso

- `clv_progress`: `closed / 200` com pending/no_close/invalid/mismatch.
  Abaixo do alvo → `CLV_INSUFFICIENT_DATA`.
- `clv_dataset`: referências de fechamento SEPARADAS —
  `bookmaker_close` (Pinnacle), `exchange_close` (Betfair),
  `consensus_close` (mediana). Sem timestamp/close real → CLV `None`.

## Quota-aware capture

Estados: `AVAILABLE` / `DEGRADED` / `RATE_LIMITED` / `EXHAUSTED` /
`AUTH_ERROR` / `DOWN` / `UNKNOWN`, com cooldown por estado (auth 24h,
exhausted 6h, rate-limit 15min). Quando todos os operacionais estão em
cooldown, o sistema entra em `WAITING_FOR_PROVIDER_QUOTA` — não gasta
chamadas e o store acumulado segue analisável.

## Estados do registry

`RESEARCH` · `INSUFFICIENT_DATA` · `EXPERIMENTAL` · `VALIDATED` · `FRAGILE`
· `NO_EVIDENCE` · `REJECTED` · `BLOCKED` · `PRODUCTION_CANDIDATE` ·
`PRODUCTION`

## Resultado da execução atual (2026-09-25)

Fingerprint do dataset: `1b6e922b85ad2c08` · 51.865 observações ·
3 mercados · 437 partidas · 31 casas.

| Sinal | n | Métrica primária | IC95% | Status |
|---|---:|---|---|---|
| BOOKMAKER_OUTLIER | 40.919 | signed_market_move_5m = **0,0005** | [−0,0001, 0,0012] | **NO_EVIDENCE** |
| BEST_PRICE_GAP | 7.155 | converged_rate = 0,3085 | [0,2966, 0,3205] | VALIDATED (observação) |
| DISPERSION_SPIKE | 3.791 | converged_rate = 0,3071 | [0,2911, 0,3238] | VALIDATED (observação) |

**Leitura honesta:**

- **BOOKMAKER_OUTLIER NÃO antecipa o mercado.** O movimento assinado do
  mercado em +5min é ~0,05% com IC que inclui o nulo → o outlier
  majoritariamente **reverte**, não lidera. Responde à investigação pedida:
  a alta frequência do sinal (62% dos criados) é ruído, não edge.
- **BEST_PRICE_GAP e DISPERSION_SPIKE persistem** (taxa de convergência
  ~31% < 50%): o gap de preço e a dispersão NÃO fecham em 15min. Isso é
  consistente com valor real de line shopping — mas **não** prova edge sem
  CLV/closing, que está BLOCKED (kickoffs futuros).

### Segmentação (por odds band / tempo-até-kickoff / gap / nº de casas)

| Sinal | odds_band | ttk_band | gap_band | book_band |
|---|---|---|---|---|
| BEST_PRICE_GAP | 0,80 | 1,00 | 1,00 | 1,00 |
| BOOKMAKER_OUTLIER | 0,60 | 0,67 | 0,75 | 1,00 |
| DISPERSION_SPIKE | 1,00 | 1,00 | 1,00 | 1,00 |

*(consistência = fração de subgrupos com n≥10 do mesmo lado do efeito geral)*

Destaques da segmentação:

- **BEST_PRICE_GAP** por `book_band`: `books>10` converge 36,8%,
  `books6-10` apenas 6,9% — o efeito depende de profundidade de mercado.
  Por `gap_band`: `gap>=10%` converge 43,5% vs `gap2-5%` 27,2%.
- **BOOKMAKER_OUTLIER** tem `market` consistência 0,50 (1X2 vs demais
  divergem) e `gap_band 2-5%` com move **negativo** (−0,4%) — reforça a
  reversão. `gap<2%` mostra +0,58% (n=808), mas com consistência 0,75 e IC
  amplo **não** é promovido (não se escolhe sub-bucket olhando o resultado).

### Ablation (line shopping separado de edge de modelo)

| Sinal | entry_advantage | best_persistence_5m | median_move_5m |
|---|---:|---:|---:|
| BEST_PRICE_GAP | 7,7% | 94,8% | +1,3% |
| BOOKMAKER_OUTLIER | 9,3% | 97,6% | +1,5% |
| DISPERSION_SPIKE | 9,3% | 100% | +1,9% |

O ganho de pegar a melhor cotação (~8–9%) e a persistência (95–100%) são
**ganho de PREÇO**, não edge de modelo. Registrado explicitamente para não
confundir line shopping com previsão.

### CLV e captura

- CLV: **closed=0 / 200** (pending 885, no_close 9) → `CLV_INSUFFICIENT_DATA`.
- Execução: **UNKNOWN** (0 fills).
- Captura: **WAITING_FOR_PROVIDER_QUOTA** (3 providers em cooldown).
- **VERDICT: BLOCKED_EXTERNAL_PROVIDER_QUOTA** — o bloqueio restante é
  fechamento real + quota de provider, não código.

## Market audit

| Mercado | Linhas | Completas | Overround médio | Casas médias |
|---|---:|---:|---:|---:|
| Resultado Final (1X2) | 431 | 430 (99,8%) | 1,0751 | 12,27 |
| Total de Gols | 416 | 416 (100%) | 1,0590 | 5,77 |
| Ambas Marcam | 33 | 33 (100%) | 1,0720 | **1,61** |

**Ambas Marcam tem 1,61 casas em média — abaixo de `MIN_BOOKS=3`.** O
mercado fica **RESEARCH** por cobertura insuficiente, nunca produção.

## Calibração

`betgsn/realtime/signal_calibration.py` calibra threshold por quantil
histórico, com `method`, `sample_size`, `period` e `fingerprint`. Sem
amostra ≥ `MIN_CALIBRATION_SAMPLE` (200) → `INSUFFICIENT_DATA`. Nenhum
threshold é ajustado olhando o TEST.

## Artefatos

`output/engineering/quant/alpha_lab/`:

- `alpha_registry.json` · `alpha_evaluations.json` · `market_audit.json`
- `clv_evidence.json` · `execution_gap.json` · `signal_registry.json`
- `robustness_report.json` · `promotion_report.json`

Reproduzir: `python tools/alpha_lab_run.py --stride=1800`

## Limitações atuais

- **CLV BLOCKED**: 0 CLOSED (kickoffs no futuro). Sem fechamento real não
  há veredito de edge.
- **EXECUTION UNKNOWN**: nenhuma fill registrada.
- **Providers em quota**: The Odds API/ParlayAPI/OddsPapi com 401/403/429
  externos — captura nova limitada; o laboratório roda sobre o store real
  já acumulado.
- **LEAD/LAG, CONSENSUS, REVERSAL, RAPID_CONVERGENCE, STALE**: replay
  pendente de série intra-linha mais densa; ficam RESEARCH (n=0).
- **MARKET_RESIDUAL e FAVORITE_LONGSHOT**: exigem modelo calibrado por
  janela + resultados → BLOCKED.

## Próximos experimentos

1. Replay dos sinais de movimento (consensus/lead/lag/reversal) com janela
   deslizante intra-linha.
2. Quando houver closing real: recalcular CLV e reavaliar OUTLIER com a
   pergunta "o outlier que reverte perde CLV?".
3. Calibrar thresholds dos 3 sinais snapshot com quantil histórico por
   mercado/faixa de odd.
4. Residual de modelo (`model_prob − market_fair`) quando houver modelo
   calibrado por janela + resultados.
