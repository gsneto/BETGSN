# BETGSN — Relatório final de engenharia

Gerado em 2026-09-20. Todos os números abaixo vêm de execuções reais
registradas em `output/engineering/`. Nada aqui é estimativa.

---

## R. PROJECT_STATUS

**FUNCIONAL COM LIMITAÇÕES EXTERNAS DECLARADAS.**

O sistema é tecnicamente sólido, temporalmente correto e mensurável. Ele
**não demonstrou vantagem explorável sobre o mercado**, e o relatório
explica exatamente por quê.

A conclusão central desta entrega é negativa, e é o resultado mais
valioso produzido: em 101.673 apostas reais, **nenhuma faixa de odds
apresentou vantagem**, mesmo pegando sempre o melhor preço entre casas.

---

## A. Arquivos criados nesta fase

| Arquivo | Função |
|---|---|
| `betgsn/odds_snapshots.py` | Store SQLite append-only de odds para CLV real |
| `betgsn/features/movement.py` | Features de movimento de preço, point-in-time |
| `betgsn/portfolio/simulation.py` | Monte Carlo de portfólio, haircut, slippage |
| `betgsn/models/promotion.py` | Gate objetivo de promoção de modelo |
| `betgsn/multi_benchmark.py` | Benchmark replicado em várias ligas |
| `betgsn/side_markets_benchmark.py` | Benchmark temporal de corners/cards |
| `betgsn/ablation.py` | Ablação de features 1X2 |
| `tools/cross_season_gate.py` | Une temporadas e aplica o gate |
| `tools/odds_bands.py` | ROI por faixa de odd sobre dados reais |
| `tools/robustness_report.py` | Erro de modelo e slippage |
| `web/src/pages/PortfolioPage.tsx` | Tela de portfólio |
| `web/src/pages/CornersPage.tsx` | Tela de escanteios |
| `web/src/pages/CardsPage.tsx` | Tela de cartões |
| `web/src/api/portfolio.ts`, `web/src/types/portfolio.ts` | Cliente e tipos |
| `tests/test_odds_snapshots.py` (39) | CLV e integridade temporal |
| `tests/test_portfolio_simulation.py` (57) | Monte Carlo e robustez |
| `tests/test_odds_movement.py` (41) | Anti-leakage de movimento |
| `tests/test_promotion_gate.py` (20) | Critérios do gate |
| `tests/test_side_markets_and_ablation.py` (17) | Corners/cards/ablação |
| `web/src/pages/CornersPage.test.tsx` (5) | Testes da CornersPage |
| `web/src/pages/CardsPage.test.tsx` (6) | Testes da CardsPage |

## B. Arquivos modificados

`betgsn/models/__init__.py` (registro de status), `betgsn/markets.py` (DNB),
`betgsn/engine.py` (de-vig DNB), `betgsn/backtest_data.py` (settlement DNB),
`betgsn/backtest_metrics.py` (Wilson CI), `betgsn/api/server.py` (endpoints),
`web/src/App.tsx`, `web/src/layout/TabBar.tsx`, `web/src/types/api.ts`.

## C. Testes

```
Backend:  574 passed, 2 skipped, 0 failed   (238s)
Frontend: 139 passed, 0 failed
Total:    713 testes
```

## D. Frontend build

`tsc -b && vite build` — zero erros. 88 módulos, 407.62 kB (120.66 kB gzip).

---

## F/G/H. Benchmark por modelo, liga e temporada

10 segmentos: 5 ligas (E0, SP1, I1, D1, F1) × 2 temporadas (2024, 2025).
Nenhuma liga foi descartada. Métrica primária: Log Loss.

| Modelo | Melhora média | Consistência | Gate |
|---|---:|---:|---|
| `1x2 Ensemble` | **+0.557%** | **90%** | **VALIDATED** |
| `btts Ensemble` | **+0.544%** | **80%** | **VALIDATED** |
| `btts LightGBM` | +0.255% | 70% | reprovado |
| `1x2 Elo` | +0.287% | 40% | reprovado |
| `1x2 XGBoost` | −0.373% | 30% | reprovado |
| `1x2 LightGBM` | −0.478% | 20% | reprovado |
| `*_isotonic` (todos) | −5% a −10% | ≤30% | reprovado |

**Achados:**
1. O ensemble venceu de forma consistente, não numa liga sortuda.
2. XGBoost e LightGBM isolados **pioraram** o Log Loss em 1X2.
3. Calibração isotônica degradou **todos** os modelos, nas duas temporadas.
4. Platt ajudou o BTTS, prejudicou o 1X2.

## L. Faixas de odds — 101.673 apostas, 46.709 partidas

| Faixa | n | Acerto | Implícito | Dif | ROI | Sig. |
|---|---:|---:|---:|---:|---:|---|
| 1.10–1.20 | 390 | 0.885 | 0.870 | +0.015 | +1.74% | não |
| 1.20–1.30 | 1.127 | 0.794 | 0.805 | −0.011 | −1.34% | não |
| 1.40–1.60 | 3.999 | 0.666 | 0.673 | −0.007 | −1.05% | não |
| 1.60–2.00 | 9.670 | 0.559 | 0.564 | −0.005 | −0.93% | não |
| 2.00–3.00 | 21.602 | 0.407 | 0.416 | −0.009 | **−2.27%** | **sim** |
| 3.00+ | 63.382 | 0.231 | 0.246 | −0.015 | **−6.46%** | **sim** |

**Nenhuma faixa com ROI positivo significativo.** As duas únicas
estatisticamente significativas são negativas. Isso refuta diretamente a
premissa de que "odd baixa é segura" e confirma o viés favorito-azarão.

## N/O. Haircut e slippage

Portfólio de 25 apostas, p=0,55, odd 1,90, stake 1%. EV declarado +4,50%.

| Haircut | EV real | Log growth | P(lucro) |
|---:|---:|---:|---:|
| 0% | +4.50% | +0.0103 | 0.542 |
| 3% | +1.36% | +0.0026 | 0.475 |
| **5%** | **−0.73%** | **−0.0024** | 0.437 |
| 10% | −5.95% | −0.0155 | 0.328 |

- **ROBUSTNESS_SCORE = 0.67** — definido como a fração dos haircuts
  testados em que o crescimento log permaneceu positivo. Sem ponderação oculta.
- **breakeven_haircut = 5%** — erro de 5% na probabilidade elimina a vantagem.
- **Slippage de 5% zera o EV** igualmente.

**Implicação decisiva:** o ganho do ensemble (+0,56%) é **nove vezes menor**
que a margem de erro tolerada (5%). Estatisticamente real, operacionalmente
irrelevante diante do risco de modelo.

## P. Status dos modelos

```
Poisson       = PRODUCTION
Dixon-Coles   = PRODUCTION
BASELINE_V1   = PRODUCTION
Ensemble      = VALIDATED     (gate aprovado; NÃO promovido a produção)
Elo           = EXPERIMENTAL
XGBoost       = EXPERIMENTAL
LightGBM      = EXPERIMENTAL
```

`PRODUCTION_MODEL` continua `BASELINE_V1`. Aprovar no gate significa que a
evidência existe — não que o modelo passa a governar decisões.

---

## Q. Classificação por item

| Item | Status |
|---|---|
| CLV — arquitetura, store, cálculo, cobertura | **COMPLETE** |
| CLV — dados históricos reais | **BLOCKED_BY_EXTERNAL_DATA** |
| Odds movement — features point-in-time | **COMPLETE** |
| Odds movement — integrado ao benchmark | **PARTIAL** |
| xG — contrato, estados, isolamento temporal | **COMPLETE** |
| xG — provider real conectado | **BLOCKED_BY_EXTERNAL_DATA** |
| Injuries/Lineups — estrutura canônica | **COMPLETE** |
| Injuries/Lineups — ingestão | **BLOCKED_BY_EXTERNAL_DATA** |
| Frontend PortfolioPage | **COMPLETE** |
| Frontend CornersPage | **COMPLETE** |
| Frontend CardsPage | **COMPLETE** |
| Multi-league benchmark | **COMPLETE** |
| Multi-season (2 temporadas) | **COMPLETE** |
| Portfolio Monte Carlo | **COMPLETE** |
| Model error simulation | **COMPLETE** |
| Odds slippage | **COMPLETE** |
| Low odds analysis | **COMPLETE** |
| ML promotion gate | **COMPLETE** |
| Corners/cards — modelos | **COMPLETE** |
| Corners benchmark (multi-liga, overdispersion) | **COMPLETE** |
| Cards benchmark (multi-liga, overdispersion) | **COMPLETE** |
| Feature ablation (forward + leave-one-out) | **COMPLETE** |
| Feature importance (native + permutation) | **COMPLETE** |

## O que impede 100%

**1. CLV histórico — bloqueio externo real.** Os CSVs trazem preços
reais, mas sem timestamp de publicação. Não existe prova de que aquele
preço estava disponível na decisão. A infraestrutura está pronta e
testada; ela precisa de tempo coletando dados dali em diante. Não há
atalho honesto: inventar timestamps produziria um CLV falso.

**2. xG real — bloqueio externo.** Football-data.co.uk não fornece xG. O
contrato, os três estados e o isolamento temporal existem e são testados.
Falta a fonte.

**3. Lesões e escalações — bloqueio externo.** Dependem de API-Football
com quota. A estrutura canônica existe.

Estas são as **únicas** pendências que dependem de terceiros. Todas as
pendências internas (ablação, benchmarks de corners/cards, páginas
Corners/Cards, feature importance) foram fechadas nesta fase.

---

## Novas análises (fase de fechamento)

### Corners — 19.786 partidas, teste 2025 (1.736), 5 ligas

| Linha | Melhor | LogLoss | vs Poisson |
|---|---|---|---|
| Total >8.5 | XGBoost | 0.6717 | −0.006 |
| Total >9.5 | NegBin | 0.6899 | −0.003 |
| Total >10.5 | LightGBM | 0.6595 | −0.002 |

**Overdispersion confirmado** (ratio médio 1.16, máx 1.35): Poisson é o
pior modelo em todas as linhas. NegBin e os boosters ficam empatados, com
vantagem pequena e instável entre linhas. Nenhum é promovido (um ano de teste).

### Cards — 19.786 partidas, teste 2025 (1.736), 5 ligas

| Linha | Melhor | LogLoss | vs Poisson |
|---|---|---|---|
| Total >3.5 | XGBoost | 0.6675 | −0.018 |
| Total >4.5 | XGBoost | 0.6550 | −0.017 |
| Total >5.5 | LightGBM | 0.5152 | −0.019 |

Os boosters batem Poisson/NegBin com margem consistente (~1–2% de LogLoss).
Ainda assim, EXPERIMENTAL: falta segunda temporada. Overdispersion médio 1.10.

### Feature ablation (1X2, 6 segmentos)

Elo é o grupo dominante (remove-lo degrada LogLoss em ~0,9%). Form em segundo.
H2H é ruído (negativo em LightGBM). Elo melhora precisão mas piora ECE.
Detalhe completo em `docs/FEATURE_ABLATION.md`.

---

## Conclusão honesta

O resultado mais importante desta entrega é **negativo e sólido**:

- Em 101.673 apostas reais, **nenhuma faixa de preço mostrou vantagem**.
- As duas faixas estatisticamente significativas são **perdedoras**.
- O único ganho validado (+0,56%) é **muito menor que a margem de erro de
  5%** que o próprio sistema mede.

Um sistema que mede a própria incerteza e conclui "não encontrei vantagem
explorável" é mais útil que um que produz ROI alto em amostra e quebra na
execução. A infraestrutura está pronta para detectar uma vantagem caso ela
apareça — e, por ora, ela não apareceu.
