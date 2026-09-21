# Feature Ablation — 1X2

Estudo de impacto marginal de cada grupo de features, out-of-sample, sobre o
modelo de 1X2 (XGBoost e LightGBM).

## Escopo e método

- **Escopo:** 3 ligas (E0, SP1, I1) × 2 temporadas de teste (2024, 2025) = **6 segmentos**.
  D1 e F1 ficaram fora por travamento do fit de ML no ambiente Windows
  (oversubscription de threads OpenMP/joblib), não por falta de dado.
- **Baseline de referência:** Poisson/Dixon-Coles de produção (não usa features).
- **Disciplina temporal:** cada partida só usa histórico com resultado
  disponível ANTES do kickoff (embargo de 48h via `result_time`). Split
  treino → validação (early stopping) → teste.
- **Métrica primária:** Log Loss (probabilística). Brier, RPS e ECE como
  secundárias. **Nada de ROI** — métrica de aposta é consequência do
  pricing, não evidência de qualidade de probabilidade.
- **Grupos testados** (identificados por prefixo de coluna do FeatureBuilder):
  Elo, Form, OpponentStrength, Rest, H2H.
- **Grupos indisponíveis** (sem dado point-in-time no CSV), declarados e não
  testados: xG, Injuries, Lineups, OddsMovement.

## Duas análises

### A. Forward addition (adiciona grupos em ordem fixa)

Mede o ganho ao acrescentar o grupo sobre o conjunto anterior.

### B. Leave-one-group-out (remove um grupo do modelo completo)

Mede a perda ao retirar o grupo. É a medida mais direta de contribuição:
Δ positivo = remover o grupo PIORA o modelo (grupo útil); Δ negativo =
remover MELHORA (grupo redundante/prejudicial).

## Resultados (Δ vs modelo completo; negativo = melhor Log Loss)

### XGBoost

| Config | Δ LogLoss | Δ Brier | Δ ECE |
|---|---:|---:|---:|
| −Elo | **+0.00985** | +0.00733 | −0.00522 |
| −Form | +0.00372 | +0.00188 | +0.00057 |
| −OpponentStrength | +0.00120 | +0.00072 | +0.00388 |
| −Rest | +0.00082 | +0.00048 | +0.00080 |
| −H2H | +0.00037 | +0.00027 | +0.00415 |

### LightGBM

| Config | Δ LogLoss | Δ Brier | Δ ECE |
|---|---:|---:|---:|
| −Elo | **+0.00902** | +0.00695 | −0.00563 |
| −Form | +0.00172 | +0.00060 | −0.00252 |
| −OpponentStrength | +0.00092 | +0.00088 | +0.00037 |
| −Rest | +0.00073 | +0.00062 | −0.00285 |
| −H2H | −0.00132 | −0.00067 | −0.00027 |

## Conclusões

1. **Elo é o grupo dominante.** Remover Elo degrada o Log Loss em ~0,9% em
   ambos os modelos — maior que todos os outros grupos somados. A vantagem
   do ML sobre o baseline de produção vem principalmente do Elo (e do Form
   em segundo lugar).

2. **H2H é ruído.** Em XGBoost contribui ~zero; em LightGBM é ligeiramente
   negativo (−0,13%). Não há evidência de que o confronto direto adicione
   informação além do que Elo/Form já capturam com a amostra atual.

3. **Elo melhora precisão mas piora calibração.** Remover Elo reduz o ECE
   (Δ negativo) ao mesmo tempo que piora o Log Loss. Ou seja: Elo adiciona
   informação real, mas o ML fica um pouco superconfiante com ele incluído.
   Isso reforça a necessidade de calibração pós-treino.

4. **Forward addition é monotônico por construção** (Elo primeiro), por isso
   a leitura confiável é o leave-one-out. A forward addition mostra o mesmo
   padrão qualitativo: Elo e Form são os grupos que importam.

5. **Rest e OpponentStrength ajudam marginalmente**, com ganhos pequenos mas
   consistentes de sinal positivo no leave-one-out.

## Não feito

- Grupos sem dado point-in-time (xG, injuries, lineups, odds movement) não
  foram testados — dependem de fonte externa.
- 5 ligas completas (D1 e F1 pendentes) por limitação de execução no
  ambiente, não por bloqueio externo.

Relatório estruturado: `output/engineering/feature_ablation/ablation.json`.
