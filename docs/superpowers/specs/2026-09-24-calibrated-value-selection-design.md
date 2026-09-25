# Qualidade econômica OOS e seleção calibrada

## Objetivo e aprovação

Melhorar qualidade econômica fora da amostra respeitando a ordem MODELO >
PREÇO > SELEÇÃO > CLV > EXECUÇÃO > ROBUSTEZ. Qualidade supera quantidade.
ROI isolado não aprova modelo ou produção. Resultado negativo é publicável.

O usuário aprovou a evolução do pipeline existente e estas definições:

- `edge = model_prob - fair_prob`; limiar de produção `edge >= 0.08`
  significa oito pontos percentuais, não EV de 8%.
- `spread` mantém a definição de `engine.market_consensus`: desvio-padrão
  populacional das odds decimais da mesma seleção; teto absoluto `0.12`.
- EV financeiro permanece campo separado, calculado com o preço correspondente
  e liquidação correta do mercado.

### Adendo aprovado pelo usuário após revisão da especificação

A especificação foi aprovada com cinco correções obrigatórias: calibrador do
modelo usa `p_model` train-only, nunca `1/odd`; stake é dimensionada pela
`lower_95`; FORTE exige simultaneamente `edge >= 0.08`, `EV >= 0.08` e
`spread <= 0.12`; gate CLV exige n>=200, média/mediana positivas e
beat-close>=55%, com todo RED bloqueante; AH aceita linha negativa legítima.
`edge+EV>=0.08` é tratado como duas condições individuais, não soma das duas
grandezas. Este adendo prevalece sobre qualquer descrição menos restritiva.

Trabalho diretamente em `main`, sem branch, worktree ou merge. Commit final
`Improve calibrated value signal selection`, seguido de push `origin main`,
somente após revisão e verificação. Não publicar segredos ou bancos locais.

## Auditoria de base

Git inicial: `9985b45`, `main`, após fetch `HEAD...origin/main = 0/0`.
Arquivo previamente não rastreado:
`output/engineering/quant/ensemble_oos_validation.json`. É evidência existente;
preservar e não tratá-lo como resultado desta implementação.

### Mapa do código existente

| Responsabilidade | Local e estado observado |
|---|---|
| Ratings e decay | `betgsn/model.py:fit_ratings`; decay exponencial existente, default zero; sem parâmetro explícito de shrinkage |
| Home advantage | `model.py`, `config.py`, `model_walkforward.py`, `real_signals.py`; fator global fixo, normalmente 1.18 |
| Attack blend | `model.py:expected_goals/_blend`; gols/xG e rating com pesos fixos; forma em `features/form.py` |
| Pipeline real | `real_signals.py` usa `pipeline.analyze_fixture`; `pipeline.run` é demonstração sintética e não fonte de evidência |
| PIT | `backtest_data.HistoricalCorpus`, `temporal.py`, `features/builder.py`, `datalayer/pointintime.py` |
| Walk-forward | `model_walkforward.py`, `value_walkforward.py`; protocolo externo 730d TRAIN, 2d gap, 365d TEST, 24 janelas históricas |
| Calibração | `models/calibration.py`, `value_walkforward.py`; Platt e isotonic; temperature ausente |
| EV/edge/spread | `engine.py`; EV = p*odd-1 no caso binário sem push; edge = diferença de probabilidades; spread = pstdev(odds) |
| Seleção | `signals.py`; EV_FORTE=0.08, MIN_BOOKS=3, MAX_SPREAD=0.25; classificação não usa edge probabilístico |
| Estratégia existente | `value_strategy.py`; favoritos curtos 1X2; não é modelo de futebol; não confundir sua evidência com a de AH/OU |
| Mercado fair/preço | `odds_math.py`, `line_shopping.py`, `realtime/views.py`; consenso, de-vig, timestamp e best/median existentes |
| Sharps | nomes mapeados em `football_data_uk.py`; falta contrato unificado separando referência sharp e tipos de close |
| CLV | `odds_snapshots.py`; FIRST_WINS, lifecycle CLOSED/PENDING/NO_CLOSE/INVALID/MISMATCH e estatísticas nulas-seguras existentes |
| Gate | `models/promotion.py`; MIN_CLV_SAMPLE=30, MIN_WINDOWS=2; campos ausentes podem ser não bloqueantes |
| Staking | `staking.decide_bet`, `strategy_runner.py`; limite inferior é verificado, mas Kelly final usa ROI pontual |
| Realtime | `realtime/engine.py`, `state.py`, `signals.py`; atualização por eventos afetados, movimento, TTL, SSE e NO_BET existentes |
| Terminal | `web/src/pages/LivePage.tsx`, componentes `live`, tipos/API/hooks realtime |
| Robustez/ablação | `models/robustness.py`, `ablation.py`, `line_shopping_audit.py`, harnesses OOS existentes |

### Defeitos e lacunas constatados

1. `model_walkforward._rows_with_model_probs` prepara `p_model`, mas
   `_select_calibration_method` chama `_raw_prob_pairs`, que lê `1/odd`.
   O calibrador ajustado nessa fonte é aplicado às probabilidades do modelo.
   Uma sonda em memória confirmou entrada 0.5 para odd 2.0 e p_model 0.8.
2. As probabilidades TRAIN desse caminho usam o modelo ajustado até o fim
   do próprio TRAIN: não são previsões rolling-origin internas. O guard do
   calibrador recebe uma fronteira artificial anterior às linhas; não comprova
   a data real do treinamento da fonte.
3. `fit_model_on_train` filtra kickoff, sem consultar disponibilidade explícita
   de resultado/xG; o corpus PIT já possui essa responsabilidade reutilizável.
4. A máscara `_fair_flags` devolve todos os índices como verdadeiros, não a
   posição real das linhas com fair. A população pareada deve ser explícita.
5. Kelly usa ponto após verificar limite inferior. Com os parâmetros históricos
   existentes: fração pontual 0.0190476 versus fração lower-bound 0.00847356.
   Essa sonda não constitui evidência econômica nova.
6. `classify(0.08, 3, 0.20)` retorna FORTE, incompatível com a nova política.
7. `realtime.state` rejeita genericamente linha negativa; a regra precisa
   distinguir handicap asiático legítimo de linhas inválidas de totais.
8. Sinais realtime são fatos de microestrutura com NO_BET, não PricedSignals
   de valor calibrado. Não converter gap best/median em alpha do modelo.
9. Calibração específica AH/OU, execução medida, erosão e gate conjuntivo dos
   sete blocos ainda precisam ser integrados.

Verificação de base executada: 139 testes passaram em 25.84s, cobrindo os
arquivos de testes de model walkforward, leakage, promotion, staking, CLV
lifecycle e realtime state/views. Isso não substitui suíte completa ou smoke.

### Evidência histórica lida, não recalculada

`output/engineering/quant/model_comparison_oos.json`, 24 janelas, n=490736:

| Fonte | Brier | LogLoss | ECE |
|---|---:|---:|---:|
| MARKET_RAW | 0.200495 | 0.586882 | 0.011435 |
| MARKET_FAIR | 0.200506 | 0.586979 | 0.006437 |
| MODEL_RAW | 0.209143 | 0.607036 | 0.002688 |
| MODEL_CALIBRATED legado | 0.209479 | 0.608331 | 0.014730 |

O rótulo calibrado legado fica sob ressalva pelo defeito de fonte descrito
acima. Preservar resultados antigos, sem reinterpretá-los como prova corrigida.

Artifact Ensemble existente: n=546533, Brier 0.203639, LogLoss 0.594372;
MARKET_RAW pareado 0.200774/0.587537, MARKET_FAIR 0.200780/0.587617.
População diferente do Poisson: agregados entre modelos não são comparação
pareada. Nenhum desses resultados demonstra superioridade ao mercado.

`clv_monitor.json`: 480 entradas, 477 PENDING, 3 NO_CLOSE, CLOSED=0,
média/mediana null. É fotografia existente, não sweep atualizado desta tarefa.

## Arquitetura escolhida

Evoluir os módulos existentes, com objetos congelados por janela e uma política
central de produção. Isso evita dois motores divergentes para pesquisa e live.
Uma substituição completa de algoritmo adicionaria confundidores sem corrigir
as falhas de fonte/tempo. Um filtro apenas na UI deixaria gates e staking
inconsistentes. A abordagem aprovada corrige primeiro contratos e modelagem.

### 1. Modelo congelado por janela

- Reutilizar `fit_ratings`, `HistoricalCorpus.available_before`, matriz de
  placares e features PIT existentes.
- Adicionar shrinkage para ataque/defesa e estatísticas usadas no blend.
  A força do prior é ajustada somente no TRAIN; times com pouca evidência
  aproximam-se da média. Registrar contagem bruta e tamanho efetivo ponderado.
- Reutilizar decay exponencial por idade em dias; candidatos predefinidos
  antes da execução TEST. Comparar candidatos em validação rolling-origin
  interna com Brier e LogLoss, nunca ROI.
- Estimar fator casa global no TRAIN e fator por liga com shrinkage ao global;
  mínimo de amostra e força do prior explícitos. Liga insuficiente usa global
  com motivo e n registrados. Identidade dos ratings inclui liga e time para
  impedir colisões entre nomes de equipes de competições distintas.
- Blend aprendido no TRAIN entre fontes reais disponíveis: rating, gols,
  forma e xG. Ausência de xG é máscara de disponibilidade; não preencher xG
  com gols ou estimativas sem proveniência. Registrar pesos efetivos/fallback.
- Hiperparâmetros são selecionados por regra predefinida usando ambas as
  perdas probabilísticas; desempate determinístico por menor complexidade.
  Nenhum resultado externo escolhe grid, variante ou hiperparâmetro.
- Congelar modelo e calibradores antes da primeira linha TEST. Registrar
  limites TRAIN/validação/TEST, maior disponibilidade de cada fonte, corpus,
  versão de código, parâmetros, amostras e fingerprint.

### 2. Calibração após matriz e por mercado

- Probabilidades para ajuste são previsões rolling-origin OOS DENTRO do
  TRAIN. Cada linha leva a fronteira real do modelo que a produziu.
- Separar bloco de ajuste dos calibradores e bloco posterior de seleção;
  reajustar método selecionado somente em previsões internas elegíveis.
- Comparar raw, Platt, isotonic e temperature; registrar parâmetros
  serializáveis, n, perdas internas e motivo de fallback.
- AH e OU têm calibração própria; 1X2 permanece benchmark de controle.
- AH/OU com push ou meia vitória/meia derrota exigem distribuição de
  liquidação compatível com a matriz; não aplicar p*odd-1 ao caso errado.
  Métricas binárias condicionais, quando usadas, devem declarar população
  sem pushes. EV usa probabilidades e retornos de todas as liquidações.
- EVgap = média do EV previsto menos retorno realizado na MESMA população
  e com o MESMO preço; gate usa valor absoluto <0.03 em múltiplas janelas.

### 3. Política congelada e gate de produção

Configuração única, versionada e fingerprintada:

| Regra | Valor |
|---|---:|
| Edge mínimo | 0.08 |
| EV mínimo | 0.08 |
| Spread máximo absoluto | 0.12 |
| Books distintos válidos mínimos | 3 |
| EVgap absoluto máximo, exclusivo | 0.03 |
| CLV CLOSED mínimo | 200 |
| Beat-close mínimo | 0.55 |
| Janelas mínimas com Brier e LogLoss melhores | 3 |
| Erosão de CLV máxima | 0.50 |

Preservar critérios existentes mais fortes, incluindo margem de relevância,
ruído, drawdown e robustez. A política de produção é conjuntiva: MODEL, CLV,
MARKET, EXECUTION, ROBUSTNESS, PROVENANCE e TEMPORAL. Qualquer RED implica
NO_BET; UNKNOWN/insuficiência também impede liberação. Status de pesquisa
VALIDATED não equivale a production_eligible.

FORTE operacional requer edge>=0.08 E EV>=0.08, spread<=0.12, books, quote válida auditável,
timestamp <= decisão, freshness admissível, proveniência, mercado habilitado,
ausência de leakage e todos os gates aprovados. Fora disso, RESEARCH ou
LIMITED_EVIDENCE/UNKNOWN, com motivo. FRACA permanece acessível no laboratório.

Mercados habilitáveis: AH e OU. BTTS, cantos e cartões ficam pausados para
produção. 1X2 permanece pesquisa/controle; evidência de favoritos 1X2 não
autoriza produção AH/OU.

### 4. PricedSignal e preços com proveniência

Contrato obrigatório: signal_id, alpha_id, event_id, market, selection, line,
decision_timestamp, observed_price, selected_price, executed_price,
closing_price, book, model_prob, market_prob, fair_prob, edge, EV, spread,
books_count, execution_status, freshness, evidence_status e provenance.

- Observado e timestamp auditáveis são obrigatórios; ausentes geram DROP
  com motivo. Observado representa quote real identificada; mediana do
  consenso permanece referência própria, sem casa fictícia representativa.
- Selecionado aponta a quote escolhida e sua casa/instante. Não presumir
  igualdade de preços do ciclo.
- Execução sem registro medido é null/UNKNOWN. Não criar aposta automática.
- Sharp reference usa Pinnacle/Betfair/Bet365 apenas com quote válida PIT;
  ausências são enumeradas. Bookmaker_close, exchange_close e consensus_close
  são referências distintas, cada qual com identidade e timestamp.
- Preservar FIRST_WINS e dados históricos; campos novos desconhecidos
  permanecem null. Não reescrever entradas antigas para parecerem completas.
- Evolução de API é aditiva, com consumidores/testes atualizados e diferenças
  semânticas explicitadas; qualquer migração mantém leitura de registros antigos.

### 5. CLV, execução e stake

- Usar lifecycle existente. Somente CLOSED válido, prospectivo, alinhado em
  evento/mercado/seleção/linha compõe estatísticas do gate.
- Exigir n>=200, mean>0, median>0, beat-close>=55%, além das restrições já
  existentes. Não contar linhas PENDING/NO_CLOSE/INVALID/MISMATCH como zeros.
- Relatar todos os recortes por liga, mercado, casa, odd band e tempo até
  kickoff, com n e insuficiência explícitos. Não escolher apenas bons recortes.
- Gap absoluto = executed - observed; relativo = gap/observed.
- CLV_before = observed/close-1; CLV_after = executed/close-1, usando a MESMA
  referência close. Erosão medida na população pareada com execução e close:
  (mean(CLV_before)-mean(CLV_after))/mean(CLV_before). Denominador positivo
  obrigatório; caso contrário, insuficiente/não aplicável, nunca aprovação.
  Razão >0.50 gera EXECUTION_EROSION. Melhor execução não é penalizada por
  usar valor absoluto do gap.
- Stake usa limite inferior 95% da vantagem e nunca apenas o ponto. Gate
  incompleto/reprovado, CLV insuficiente ou lower<=0 implica stake=0.
  O runner continua chamando o produtor único de decisão em `staking.py`.

### 6. Realtime, qualidade e terminal

- Integrar inferência congelada e PricedSignal no engine existente; estado
  incremental, SSE, reconexão e expiração continuam centrais.
- Cache por evento e fingerprint de modelo/quotes/política; mudanças de
  freshness/tempo devem invalidar elegibilidade mesmo sem novas quotes.
- Validar evento, preço finito >1, casa, mercado, seleção, linha e timestamps
  antes de promoção a sinal. Handicaps negativos legítimos são permitidos;
  linha deve coincidir com seleção. Quote futura não sustenta decisão PIT.
- Sinal apresenta LIVE/STALE/EXPIRED/RESEARCH; terminal mostra FORTE,
  LIMITED/RESEARCH, NO_BET, edge, spread, books, best/fair/model, freshness,
  movimento, CLV e histórico de execução medido ou desconhecido.
- Aproveitar captura/providers existentes e suas quotas. Nenhuma fabricação
  de movimento ou sinal para satisfazer smoke. Ausência de evento real durante
  a janela de observação é limitação, não sucesso simulado.

## Protocolo de prova e aceitação

1. Registrar candidatos e protocolo antes de consumir TEST. Preservar limites
   das janelas externas existentes; não encurtar execução para favorecer resultado.
2. Comparações probabilísticas pareadas com MARKET_RAW e MARKET_FAIR por
   janela e mercado. Máscaras de dados ausentes explícitas; n e cobertura por
   fonte. Agregados com populações diferentes não provam superioridade.
3. Ablação BASELINE, +SHRINKAGE, +DECAY, +HOME, +BLEND, +CALIBRATION,
   +FILTER, +BESTPRICE, +CLV. Registrar efeitos sequenciais e leave-one-out
   quando aplicável; não assumir aditividade. Preço comparado na mesma população.
4. +CLV não pode selecionar aposta com seu próprio fechamento futuro. Filtro
   só usa histórico já CLOSED antes da decisão; amostra ausente implica zero
   sinais elegíveis, com métricas null e n=0.
5. Robustez multi-liga/temporada, bandas, mercados, subsets de books e
   leave-one-league/season. Declarar se é reestimação ou exclusão de segmento
   da avaliação; não chamar uma coisa pela outra.
6. Relatório: Brier, LogLoss, ECE, ROI de cenário, EV, EVgap, CLV, beat-close,
   execução, drawdown, amostras, janelas, proveniência e limitações. CSV histórico
   sem timestamp de quote não prova execução ou CLV prospectivo.
7. Testes determinísticos para fonte real do calibrador, treino interno OOS,
   disponibilidade de resultado/xG, invariância a dados TEST/futuros,
   shrinkage/decay/home/blend, temperature, máscara fair, liquidação AH/OU,
   thresholds/fingerprint, PricedSignal, lifecycle/CLV, erosão e lower-bound.
8. Testar qualquer RED e qualquer evidência ausente bloqueando produção/stake.
9. Suíte Python completa, testes frontend e build; smoke real de backend,
   frontend, odds, health, freshness, movimento, best/fair, sinais, PricedSignal,
   NO_BET e SSE/reconnect. Evidência medida separada de testes com fixtures.
10. Revisar diff/staged, segredos e tamanho dos arquivos antes do commit/push.
    Verificar branch e sincronismo novamente antes da publicação.

## Entrega e limites de conclusão

O relatório final identifica commit e confirmação de push e dá status
IMPLEMENTADO/VALIDADO/EXPLORATÓRIO/PENDENTE/BLOCKED por bloco MODEL,
CALIBRATION, SELECTION, MARKETS, PRICED, CLV, ROBUSTNESS, STAKING, REALTIME,
TESTS, BUILD, PRODUCTION_ELIGIBLE, NO_BET e LIMITAÇÕES.

Sem CLV e execução suficientes não é possível aprovar produção nesta sessão
por construção. Melhorias de código ou de métricas não preenchem essa ausência.
Um resultado inferior ao mercado permanece publicado como inferior. A evidência
histórica existente foi lida para auditoria; a nova comparação é pesquisa
retrospectiva com política de seleção interna congelada, não holdout nunca visto.

## Auto-revisão da especificação

- Sem placeholders de decisão; definições de edge e spread aprovadas pelo usuário.
- Separação explícita entre modelo, preço, seleção e execução.
- Nenhuma ablação usa o próprio close futuro para selecionar.
- Fonte do calibrador e disponibilidade temporal são verificáveis por linha.
- Produção exige todos os critérios; nenhuma compatibilidade de API autoriza
  fallback permissivo de gate ou stake.
- Próximo estágio: revisão deste documento pelo usuário, seguida de plano de
  implementação em etapas no main.
