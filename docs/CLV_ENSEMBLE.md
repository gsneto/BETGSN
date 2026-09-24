# CLV operacional e Ensemble — agent/clv-ensemble

Base publicada: `1538c27` (main e origin/main iguais no início).

## Auditoria do caminho existente

1. FIRST_WINS nasce em `BetgsnService._register_clv_entries`, chamado por
   `real_signal_report`. `line_at` calcula a mediana das últimas quotes por
   casa visíveis na decisão. Não usa `best_odds` como preço de entrada.
2. Identidade: event_key canônica, mercado e resultado. Captura resolve aliases
   usando FixtureMatchIndex: divisão, equipes e kickoff UTC exatos. Ambiguidade
   não é resolvida por aproximação.
3. `entry_timestamp` identifica a observação; `prediction_timestamp` a decisão.
   FIRST_WINS é UNIQUE(match_key, market, outcome, source), INSERT OR IGNORE.
4. Captura multi-provider usa LiveOddsCapture, OddsHistoryCache e o mesmo
   OddsSnapshotStore. Quotes posteriores não reescrevem entradas.
5. Close operacional: últimas quotes por casa na janela de 120 minutos antes
   do kickoff. Lifecycle agora exige todas as componentes usadas depois da
   decisão, kickoff consistente e evento já iniciado no instante do sweep.
6. CLV = entry_odd / closing_odd - 1. Trata-se de mediana contra mediana,
   não preço de execução contra fechamento. Mediana pode não ser preço de
   nenhuma casa; a casa representativa não é execução.
7. Sweep é leitura: chamadas repetidas não persistem duplicatas. Ausência
   continua PENDING/NO_CLOSE; dados inconsistentes INVALID/MISMATCH.
8. `scripts/capture_daily.ps1` chama o capturador e agora o relatório/sweep.
   Providers configurados são usados; disponibilidade real de casas depende
   da cobertura/plano. Nenhuma chamada paga foi feita nesta fase.
9. Deduplicação física é (evento, mercado, resultado, bookmaker, timestamp).
   Providers concorrentes com a mesma chave são FIRST_WINS: uma segunda
   origem não cria nova quote. Isso preserva idempotência, mas não conserva
   ambas as atribuições de provider em colisões.
10. Kickoff divergente sob a mesma chave e bookmaker original não observado
    são MISMATCH. Uma mudança legítima de kickoff requer reconciliação de
    identidade, não reescrita silenciosa dos timestamps.

## Observabilidade

`/api/quant/clv/status`: população inteira do store, contagem por estado,
estatísticas nulas-seguras, taxa de close e resolução, capture/provider health,
entradas e proveniência reconstruída com quotes originais e posteriores.
`/api/clv` continua representando os fixtures atuais, população diferente.

`selected_price` histórico não foi persistido pelo FIRST_WINS: é null e
declarado desconhecido. `execution_price=null`, `execution_status=UNKNOWN`.
A API e o relatório não mostram média zero quando n=0. O contrato numérico
legado interno do gate mantém seu sentinel com n=0; não há amostra nem
promoção. O gate não foi afrouxado.

Uma captura diária às 09h não cobre fechamentos à noite. O script suporta
invocações repetidas; nenhum agendamento novo foi criado. A cadência real
e as quotas devem permitir observações dentro da janela de 120 minutos.

## Ensemble

Reutiliza o harness `run_model_walkforward` e as mesmas 24 janelas externas
(730d seleção TRAIN / 2d embargo / 365d TEST). O fit de bases ML preserva
o histórico expansivo anterior ao train_end usado no protocolo ML existente,
agora exigindo disponibilidade dos resultados para o Ensemble.

Três folds rolling-origin dentro desse histórico TRAIN. Cada base usa somente
o passado do fold, com embargo interno de dois dias e labels disponíveis.
Elo, XGBoost e LightGBM usam defaults existentes. Boosters dividem seu bloco
em fit e validação temporal de early stopping. Meta: LogisticRegression sobre
as nove probabilidades OOS das bases. Nenhum tuning pelos resultados TEST.

O adapter não expõe previsões in-sample TRAIN ao calibrador do harness:
model_calibrated é raw para este Ensemble. Isso evita empilhar um calibrador
treinado em previsões in-sample sobre o stacking. Não reinterpretar como
comparação entre calibradores equivalentes dos modelos antigos.

Features são PIT no kickoff de cada partida e reutilizadas da matriz cacheada.
Resultados de TEST passados podem integrar features de jogos TEST posteriores
somente quando já publicados; os parâmetros permanecem congelados.
Audits por janela registram fronteiras, embargo, amostras e disponibilidade.

Métricas são binárias por linha 1X2 (não Brier multiclasses por partida).
Odd bands usam a mesma população de predictions TEST, sem novos fits.
Bootstrap pareado usa blocos mensais. ROI do harness é cenário com odds
históricas sem timestamp de publicação, não prova de execução lucrativa.

## Revisão e decisões

- Corrigido off-by-one do último chunk; testes reproduziram a falha antes
  da correção. A implementação anterior aceitava fold final abaixo do mínimo.
- Meta novo em vez de WeightedEnsemble: pesos convexos com cutoff único não
  representam a regressão multinomial com folds rolling. Custo: mais código
  e deslocamento entre bases parciais e finais, declarado e avaliado OOS.
- Foram interrompidas execuções incompletas; warnings no stderr encerravam
  o wrapper PowerShell. Execução atual preserva exit code Python e stderr.
- Mantidos os campos in-memory de auditoria fora da serialização geral para
  limitar tamanho. Consumidores existentes usam atributos do objeto.
- Observações e resultados de testes são fixtures; não foram adicionados
  fechamentos sintéticos ao store operacional.
- Sugestões menores restantes: centralizar string de versão do Ensemble e
  exibir resolve_rate no frontend. O campo já é consultável pela API.

## Critério de decisão

Sem evidência suficiente e sem CLV válido: **NO BET**. Nenhum ranking global,
nenhum vencedor declarado, nenhuma alteração do main ou merge.

## Resultado observado (24/09/2026)

Artefato completo: `output/engineering/quant/ensemble_oos_validation.json`.
24/24 janelas, 546.533 linhas; 70 folds internos usados. Dois folds iniciais
foram descartados por amostra insuficiente para early stopping, como previsto
no adapter. Ambas as janelas ainda possuem dois folds OOS válidos.

| Fonte | Brier | LogLoss | ECE | n |
|---|---:|---:|---:|---:|
| MARKET_RAW | 0,200774 | 0,587537 | 0,011563 | 546.533 |
| MARKET_FAIR | 0,200780 | 0,587617 | 0,006335 | 546.533 |
| ENSEMBLE | 0,203639 | 0,594372 | 0,002493 | 546.533 |

Menor ECE não compensa por definição Brier/LogLoss piores. Bootstrap pareado
de LogLoss, 2.000 reamostragens, 280 blocos mensais, diferença **mercado menos
Ensemble**:

- RAW: -0,006835; IC95% [-0,007365; -0,006313], `piora_robusta`.
- FAIR: -0,006755; IC95% [-0,007273; -0,006238], `piora_robusta`.

ROI de cenário da estratégia EV>0: -5,1893%, n=215.297; bootstrap do harness
[-5,9009%; -4,4672%]. Inclui melhor preço/line-shopping; não mede execução.

| Odd band | n | LogLoss Ensemble | LogLoss RAW |
|---|---:|---:|---:|
| <1,40 | 11.498 | 0,5117 | 0,4859 |
| 1,40–2,00 | 64.235 | 0,6853 | 0,6730 |
| 2,00–3,00 | 138.233 | 0,6772 | 0,6686 |
| >=3,00 | 332.567 | 0,5452 | 0,5408 |

O artefato contém os valores completos, métricas FAIR por faixa, resultados
por janela e os audits de treinamento.

### Referência secundária Poisson/DC

Cache existente de BASELINE_V1 validado pelo próprio fingerprint: 24 janelas,
Brier 0,209143, LogLoss 0,607036, ECE 0,002688, n=490.736. O Ensemble apresenta
métricas agregadas menores, mas as populações diferem (ratings ausentes em
parte das partidas do baseline). Comparação **exploratória, não pareada**;
não constitui evidência de superioridade sobre Poisson/DC. MARKET continua
sendo o comparativo pareado principal.

### CLV e qualidade

- FIRST_WINS 480: PENDING 477, NO_CLOSE 3, CLOSED/INVALID/MISMATCH 0.
- CLV válido n=0; média/mediana null. A coleta de fechamentos depende de
  capturas futuras próximas dos jogos. Nenhum fechamento foi fabricado.
- Python: 1.454 passed, 1 skipped; frontend: 174 passed; build passou.
- Revisão externa identificou a fronteira do último fold; reproduzida e
  corrigida. Verificações adicionais endureceram decisão/close e embargo.
- O primeiro artefato recebeu fingerprint **pós-execução**, declarado no JSON,
  após auditoria de 24 janelas/70 folds. Futuras execuções registram no início.
- Promotion gate intacto; `production_eligible=false`; **NO BET**.

Limitações: sem agendamento instalado por esta fase; sem amostra CLV; preço
selecionado/execução históricos ausentes; colisões de provider preservam a
primeira atribuição; Poisson/DC sem comparação pareada nesta entrega; cache
de features legado usa assinatura do corpus e versão (não hash de todo código
do FeatureBuilder), enquanto o artefato Ensemble valida corpus e código.
