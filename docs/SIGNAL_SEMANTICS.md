# BETGSN — Semântica dos sinais realtime

Documento de contrato dos 9 sinais emitidos por `betgsn/realtime/signals.py`.
Nenhum sinal é recomendação de aposta: todos nascem com
`production = NO_BET` e `evidence_status = OBSERVED`. Este documento existe
para que cada sinal seja auditável (fórmula, inputs, threshold, janela,
expiração) e para separar **INFORMATIONAL SIGNAL** de **PRODUCTION ACTION**.

## Regras comuns

- **Fonte de verdade dos inputs:** `MarketState.latest` (última cotação por
  casa/linha), construída por `realtime/views.py` a partir de quotes aceitas.
- **Timestamp:** cada sinal carrega `observed_at` do fato que o originou.
  Quote com carimbo **futuro** (`> now`) é recusada (`FUTURE_TIMESTAMP`) e
  não entra em nenhum sinal (sem tolerância de skew).
- **Livros mínimos:** sinais de dispersão/outlier exigem `n_books >= 3`.
- **Expiração:** todo sinal tem TTL (`SignalRules.ttl_seconds`, default
  1800s); `with_status` marca ACTIVE/STALE/EXPIRED.
- **Evidência:** `evidence_status=OBSERVED`; `production=NO_BET` sempre.
- **Thresholds:** constantes de engenharia em `SignalRules`. Não há
  calibração estatística aplicada por padrão — a infraestrutura de
  calibração vive em `betgsn/realtime/signal_calibration.py` e produz
  `INSUFFICIENT_DATA` quando não há amostra. Enquanto um threshold não for
  calibrado, o sinal correspondente é **RESEARCH**, nunca produção.

## Tabela por sinal

| Sinal | Fórmula | Inputs | Threshold | Janela | Mín. books | Risco de FP |
|---|---|---|---|---|---|---|
| `STALE_PRICE` | 1 sinal por casa com `age > stale_seconds` quando existe ao menos 1 casa com `age <= recent_seconds` | idade por casa (`age_seconds`, `timestamp`) | `stale_seconds=3600`, `recent_seconds=900` | idade | 2 | médio |
| `BOOKMAKER_OUTLIER` | `\|price − mediana(demais)\| / mediana(demais) >= outlier_min_deviation`, emitido **por casa** | preços das casas | `outlier_min_deviation=0.05` | frescor da quote | 3 | **alto** (ver nota) |
| `DISPERSION_SPIKE` | `pstdev(preços) / mediana(preços) >= dispersion_spike_ratio` | preços, mediana | `dispersion_spike_ratio=0.04` | frescor | 3 | médio-alto |
| `BEST_PRICE_GAP` | `(melhor − mediana) / mediana >= best_gap_min` | melhor preço, mediana | `best_gap_min=0.04` | frescor | 3 | alto (estrutural: `melhor >= mediana`) |
| `CONSENSUS_MOVE` | N casas movendo na mesma direção na janela | movimentos (`LineMove`) | `consensus_min_books=3` | `move_window_seconds` (0 ≤ idade ≤ janela) | 3 | baixo-médio |
| `BOOKMAKER_LEAD` | primeira casa a mover numa direção com ≥1 seguidora | movimentos + timestamps | `len(recent) >= 2` | janela | 2 | médio |
| `BOOKMAKER_LAG` | 1 sinal por seguidora do líder | movimento seguidor + timestamp líder | — | janela | 2 | médio-alto |
| `PRICE_REVERSAL` | ≥2 movimentos da mesma casa com direção invertida | movimentos | — | janela (0 ≤ idade ≤ janela) | 1 casa | médio-alto |
| `RAPID_CONVERGENCE` | `(max − min)/mediana <= convergence_max_spread` | preços atuais, mediana | `convergence_max_spread=0.01` | janela | 2 | baixo (raro) |

## Nota sobre `BOOKMAKER_OUTLIER`

Na operação observada (`output/logs/realtime.jsonl`, ~45k eventos) o OUTLIER
respondeu por **62,3%** dos sinais criados. Causas estruturais:

1. **emissão por casa** — uma seleção pode gerar até `n_books` sinais de
   uma vez;
2. **banda relativa fixa de 5%** sem escalar com `n_books`/mercado;
3. **sem filtro de frescor/PIT** no caminho informativo — quote velha vira
   "outlier" mecanicamente.

Enquanto não houver threshold calibrado (via
`signal_calibration.calibrate_threshold` sobre `outlier_deviations`
históricos), o OUTLIER é tratado como **RESEARCH**. A calibração separa
*dispersão ordinária* de *dispersão estatisticamente incomum* usando o
quantil histórico (ex.: q=0.99), com `n`, período e fingerprint auditáveis.

## Separação INFORMATIONAL vs PRODUCTION

- `/api/realtime/*` → sinais informativos (`production=NO_BET`, sem stake).
- `/api/signals` → triagem analítica com decisão global do Quant; sob
  NO_BET, `stake=0`, `top_tips=[]`, exposição zero.
- Qualquer ação operacional futura exige o `ProductionGate` de 7 blocos
  GREEN (`betgsn/live_gate.py`) — nunca um sinal isolado.
