# Calibrated Value Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task, diretamente nesta sessão. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir as cinco falhas obrigatórias e medir melhorias de modelo/preço/seleção em OOS honesto, mantendo produção bloqueada enquanto faltar evidência.

**Architecture:** Evoluir o motor existente com parâmetros congelados por janela, calibração de probabilidades internas OOS e política central de produção. PricedSignal conecta quotes auditáveis, inferência, referências de fechamento e execução medida ao gate e terminal. Reutilizar corpus PIT, matriz, lifecycle CLV, métricas, estado incremental e SSE existentes.

**Tech Stack:** Python, NumPy, scikit-learn já instalado, SQLite existente, FastAPI, React/TypeScript, pytest e Vitest. Não adicionar dependências para estas mudanças. Consultar Context7 antes de alterações específicas nas APIs dessas bibliotecas.

**Spec:** `docs/superpowers/specs/2026-09-24-calibrated-value-selection-design.md`, incluindo adendo das cinco correções.

## Global Constraints

- Trabalhar no `main`, sem branch, worktree ou merge. Se behind após fetch, parar e avisar.
- Commit final: `Improve calibrated value signal selection`; push `origin main` após revisão. Consolidar alterações em um commit final, conforme pedido; não fazer commits intermediários deste plano.
- FORTE: `edge >= 0.08` **E** `EV >= 0.08`, `spread <= 0.12`, pelo menos 3 casas válidas distintas.
- Edge = model_prob - fair_prob. Spread = desvio-padrão populacional absoluto das odds da mesma seleção. Não somar edge e EV para atingir o limiar.
- EVgap absoluto <0.03; CLV CLOSED n>=200, mean>0, median>0, beat-close>=0.55; erosão >0.50 bloqueia.
- Brier E LogLoss melhores que MARKET_RAW E MARKET_FAIR em pelo menos 3 janelas, com estabilidade e demais gates preservados.
- Qualquer RED em MODEL/CLV/MARKET/EXECUTION/ROBUSTNESS/PROVENANCE/TEMPORAL implica NO_BET. UNKNOWN/ausência também impede produção.
- Stake dimensionada pela lower_95; lower<=0, CLV insuficiente ou production_eligible=false implica zero.
- Treino/seleção só no TRAIN; nunca tuning por ROI TEST, futuro, close fabricado, execução presumida ou dado inventado.
- Produção habilitável apenas AH/OU; manter infraestrutura dos demais mercados como pesquisa.
- Preservar artifacts antigos e o JSON Ensemble já não rastreado; novos resultados não sobrescrevem a referência histórica.
- Não imprimir/versionar .env, tokens, logs, bancos, caches, temporários ou arquivos >100MB.

## Review Focus

1. NaN/inf, booleanos como números e amostra ausente não podem satisfazer comparações de gate — testes nas tarefas 1, 5 e 6.
2. Previsão com odds alteradas mas p_model idêntico deve produzir calibrador idêntico; publicação tardia de resultado/xG deve excluir a linha — tarefas 2 e 3.
3. Fechamento de outra linha/casa/tipo, duplicatas e sinal selecionado com o próprio close futuro não podem inflar CLV — tarefas 5 e 8.
4. Fim do TTL sem chegada de novas quotes, reinício e reconexão SSE não podem manter FORTE ou reutilizar fingerprint vencido — tarefas 9 e 11.
5. AH negativo, quarto de linha, push e half-win/half-loss devem conservar liquidação correta, sem virar probabilidade binária ingênua — tarefa 4.

## Arquivos e fronteiras

**Evoluir:** `config.py` (política), `model.py` (ratings/blend), `model_walkforward.py` (harness e máscaras), `models/calibration.py` (calibradores), `models/promotion.py` (gate), `staking.py` e `strategy_runner.py` (decisão), `signals.py` (classificação), `odds_snapshots.py` (lifecycle/referências), `realtime/{state,signals,engine,views}.py`, `api/{realtime,quant_service,schemas}.py`, tipos/componentes live no frontend.

**Novos módulos focados:**
- `betgsn/model_training.py`: parâmetros candidatos, ajuste TRAIN, folds internos e auditoria.
- `betgsn/model_calibration.py`: linhas p_model OOS, seleção interna, bundle por mercado e fingerprint.
- `betgsn/settlement_distribution.py`: distribuição de liquidações AH/OU via regra existente.
- `betgsn/production_policy.py`: avaliação pura dos sete blocos e seleção operacional.
- `betgsn/priced_signals.py`: contrato, validação, referências e diagnóstico de execução.
- `betgsn/priced_signal_store.py`: persistência append-only de sinais e execução documentada, sem executar apostas.
- `betgsn/calibrated_value_validation.py`: orquestração de variantes, linhas OOS e relatório.
- `tools/calibrated_value_validation.py`: CLI de execução completa.
- `tools/realtime_smoke.py`: smoke real auditável, sem fixtures de odds.

Não duplicar bootstrap, settlement, de-vig, identidade canônica ou produtor de BetDecision. Interfaces abaixo são novos contratos a implementar; funções já existentes são identificadas pelo módulo.

## Convenções de verificação

Comandos Python executados na raiz:

```powershell
& ".venv\Scripts\python.exe" -m pytest -q tests/test_arquivo.py
```

Antes de comandos que escrevem arquivos, verificar o diretório pai com Test-Path. Os testes usam `tests/conftest.py` e nunca escrevem no store de produção. Em cada tarefa: teste RED pela falha esperada, implementação, teste GREEN e inspeção do diff. Não mudar expectativas para esconder regressão.

---

### Task 1: Política congelada e classificação sem FORTE permissivo

**Files:** Modify `betgsn/config.py`, `betgsn/signals.py`; Create `betgsn/production_policy.py`, `tests/test_production_policy.py`; atualizar chamadores/testes de `classify` localizados por busca.

**Interfaces:** `production_thresholds() -> dict[str, object]`, `production_policy_fingerprint() -> str` em config. Em production_policy: `GateBlock(status: str, reasons: tuple[str,...])`, `ProductionGate(blocks: dict[str,GateBlock], fingerprint: str)` com propriedade `production_eligible: bool`; `selection_reasons(*, edge: float|None, ev: float|None, spread: float|None, books_count: int, market: str, gate: ProductionGate, quote_valid: bool) -> tuple[str,...]`.

- [ ] Criar testes de limites para cada limiar e para ausência/NaN. Exemplo executável após definir interfaces:

```python
def test_one_red_always_blocks():
    names = ('MODEL','CLV','MARKET','EXECUTION','ROBUSTNESS','PROVENANCE','TEMPORAL')
    for red in names:
        blocks = {n: GateBlock('RED' if n == red else 'GREEN', ()) for n in names}
        assert not ProductionGate(blocks, production_policy_fingerprint()).production_eligible

def test_thresholds_are_individual():
    assert production_thresholds()['min_edge'] == 0.08
    assert production_thresholds()['min_ev'] == 0.08
    assert production_thresholds()['max_spread'] == 0.12
```

- [ ] Rodar `tests/test_production_policy.py`; confirmar que os novos contratos não existem ainda.
- [ ] Implementar constantes centrais imutáveis, SHA256 de JSON canônico e teste com digest literal calculado e revisado uma vez antes do OOS. Alterar threshold sem atualizar fingerprint esperado deve falhar.

```python
required = {'MODEL','CLV','MARKET','EXECUTION','ROBUSTNESS','PROVENANCE','TEMPORAL'}
eligible = set(blocks) == required and all(b.status == 'GREEN' for b in blocks.values())
# Sem tolerância que aceite 0.07999 ou 0.12001:
strong_numeric = edge >= 0.08 and ev >= 0.08 and spread <= 0.12 and books_count >= 3
```

- [ ] Rejeitar bool para amostra/valores, floats não finitos, contagens negativas; parâmetros ausentes produzem razões explícitas. Atualizar `signals.classify` para receber edge e evidência operacional opcional; ausência não pode produzir FORTE. Manter níveis de pesquisa sem stake operacional.
- [ ] Cobrir pares edge/EV (0.08/0.07999 e 0.07999/0.08), spread 0.12/0.12001, books 2/3, duplicação de casa, mercado pausado, gate incompleto, UNKNOWN e cada RED. Rodar testes novos mais `test_real_signal_contract.py` e `test_no_bet_first_class.py`; revisar contratos antes de seguir.

### Task 2: Corrigir fonte e temporalidade da calibração do modelo

**Files:** Create `betgsn/model_calibration.py`, `tests/test_model_calibration_source.py`; Modify `betgsn/model_walkforward.py`, `betgsn/models/calibration.py`; manter o calibrador de mercado em `value_walkforward.py` identificado como mercado.

**Interfaces:** `CalibrationRow(p_model: float, y: int, prediction_time: str, label_available_at: str, model_trained_until: str, market: str, sample_weight: float=1.0)`; `fit_model_calibrator(rows: Sequence[CalibrationRow], *, train_end: str, market: str) -> CalibrationBundle`. Bundle: método, parâmetros, n, fingerprint, fronteiras e `predict(ps: Sequence[float], prediction_time: str) -> list[float]`.

- [ ] Reproduzir com teste a rota errada: instrumentar `TemporalCalibrator.fit` e chamar harness com odds incompatíveis com p_model; verificar que os valores recebidos são p_model. Alterar odds sem alterar previsões não pode alterar parâmetros do calibrador.

```python
def test_calibration_row_carries_actual_model_boundary():
    row = CalibrationRow(.8, 1, '2024-02-01', '2024-02-03', '2024-02-02', '1x2')
    with pytest.raises(ValueError, match='model_trained_until'):
        fit_model_calibrator([row], train_end='2024-03-01', market='1x2')
```

- [ ] Rodar `tests/test_model_calibration_source.py`; confirmar falha de fonte/guard, sem editar o teste para usar odds como substituto.
- [ ] Fazer `_rows_with_model_probs` obter previsões de modelos internos treinados estritamente antes de cada bloco; jamais usar o modelo final TRAIN para prever suas próprias linhas de treino. Reutilizar `HistoricalCorpus.available_before`, embargo 2d e fit do baseline.

```python
assert utc_key(row.model_trained_until) < utc_key(row.prediction_time)
assert utc_key(row.label_available_at) < utc_key(train_end)
ps = [r.p_model for r in eligible_rows]
# Não reutilizar _raw_prob_pairs neste caminho.
```

- [ ] Separar seleção do calibrador no último bloco cronológico interno (25% dos dias elegíveis), ajuste nos anteriores; mínimo 50 linhas e ambas as classes, senão raw com motivo. Escolher por soma Brier+LogLoss interna, exigindo ambas não piores que raw; desempate raw/platt/temperature/isotonic por ordem explícita. Nunca ECE ou ROI TEST como seletor.
- [ ] Adicionar temperature positiva de logits com grade TRAIN fixa `(0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)` e menor LogLoss de ajuste; Platt/isotonic reutilizam sklearn. Registrar grade, fitted params, amostras ponderadas, método e SHA256. Validar classes, alinhamento e finitude antes de fit; indisponibilidade tem motivo serializado.
- [ ] Corrigir máscara fair com índices explícitos e labels correspondentes; comparar fontes na interseção pareada. Manter contagens de cobertura. Atualizar schema/fingerprint do novo resultado e preservar referência antiga.
- [ ] Testar os três métodos com dados determinísticos, TEST alterado sem mudar fit, OOS interno real, disponibilidade tardia, fair ausente no meio das linhas e bundle roundtrip. Rodar `test_model_calibration_source.py`, `test_model_walkforward.py`, `test_quant_leakage.py` e `test_ensemble_walkforward.py`.

### Task 3: Shrinkage, decay, home por liga e blend congelados

**Files:** Create `betgsn/model_training.py`, `tests/test_model_training.py`; Modify `betgsn/model.py`, `betgsn/model_walkforward.py`, `betgsn/pipeline.py`, `betgsn/real_signals.py`.

**Interfaces:** `TrainingParams(shrinkage: float, decay_days: float|None, league_home: bool, blend_weights: tuple[float,float,float,float])`; pesos em ordem rating/gols/forma/xG. `fit_train_snapshot(matches: Sequence[HistoricalMatch], *, train_start: str, train_end: str, params: TrainingParams) -> TrainedSnapshot`; snapshot expõe `matrix(fixture: Fixture) -> ScoreMatrix|None`, auditoria, ratings `(liga,time)`, home_by_league, `fingerprint`. `select_train_params(matches, *, train_start, train_end, enabled: tuple[str,...]) -> TrainingParams`.

- [ ] Criar teste com poucos jogos versus muitos e mesma média observada; amostra pequena deve ficar mais próxima do prior. Testar mesmo nome em duas ligas sem mistura e resultado publicado após cutoff excluído.

```python
def test_effective_sample_shrinks_with_unequal_weights():
    weights = [1.0, 0.01]
    n_eff = sum(weights)**2 / sum(w*w for w in weights)
    assert 1 < n_eff < 1.03
# No teste de fit: comparar |attack-1| para amostras pequena e grande,
# usando o mesmo prior/global e goals ratio, não apenas espelhar a fórmula.
```

- [ ] Rodar `tests/test_model_training.py` e confirmar falhas específicas.
- [ ] Estender ratings com pseudocontagem de prior neutro durante ajuste; manter default zero para reproduzir baseline histórico. Validar pesos finitos positivos; contagem bruta não é `int(sum(weights))`.

```python
weight = exp(-log(2) * age_days / half_life_days) if half_life_days else 1.0
shrunk = (n_eff * observed_strength + prior_count) / (n_eff + prior_count)
# Home global = soma gols casa / soma gols fora sobre TRAIN elegível.
# Liga: mínimo 100 partidas; prior global com massa 200 partidas.
# Abaixo de 100, usar global e registrar INSUFFICIENT_LEAGUE_SAMPLE.
```

- [ ] Fixar candidatos antes do TEST: shrinkage `(0,10,30)`, half-life `(None,180,365)`, home global versus liga; pesos blend `(1,0,0,0)`, `(.5,.5,0,0)`, `(.5,.25,.25,0)`, `(.5,.25,0,.25)`. Forma usa gols das últimas 5 partidas disponíveis antes do fit; xG apenas REAL com timestamp/source auditáveis; renormalizar fontes presentes e registrar disponibilidade.
- [ ] Selecionar sequencialmente apenas no TRAIN em três folds rolling-origin, deixando no mínimo 300 jogos no primeiro ajuste. Preservar protocolo externo e distinguir histórico ratings 1095d de janela de seleção 730d; registrar ambos. Score soma Brier+LogLoss de validação interna, tie-break menor complexidade. Grid inválido/insuficiente mantém default e motivo.
- [ ] Congelar snapshot antes do TEST e usar as mesmas funções no live. Testar invariância a futuro/test labels, decay em dias e ordenação, liga pequena/global, pesos válidos, sem xG inventado, hashing de parâmetros/fronteiras e roundtrip. Rodar `test_model_training.py`, `test_xg_foundation.py`, `test_temporal_foundation.py`, `test_real_signals.py`.

### Task 4: AH negativo e distribuição de liquidação AH/OU

**Files:** Create `betgsn/settlement_distribution.py`, `tests/test_market_settlement_distribution.py`; Modify `betgsn/realtime/state.py`, `betgsn/markets.py`; usar `backtest_data.py:settle_outcome`, corrigindo ali quarto de linha se teste provar lacuna.

**Interfaces:** `SettlementDistribution(win: float, half_win: float, push: float, half_loss: float, loss: float)` com `expected_return(price: float) -> float`, `effective_probability: float`, `settled_exposure: float`; `distribution_for(matrix: ScoreMatrix, market: str, selection: str) -> SettlementDistribution`. Validação de linha em função compartilhada `validate_selection_line(market: str, selection: str, line: float|None) -> str|None`.

- [ ] Testar ingestão de quote AH Casa -1.5 aceita; totals -1.5, AH sem side, mismatch -1.5/+1.5, NaN/inf e incremento não múltiplo de 0.25 rejeitados com razão.
- [ ] Rodar regressões de `test_realtime_state_views.py` e novo teste antes de corrigir a rejeição genérica.
- [ ] Usar parser canônico de seleção; para quarter-line dividir stake em duas linhas adjacentes de meia unidade. Enumerar grid e agregar retornos segundo settlement existente. Não tratar under inteiro como complemento simples de over quando há push.

```python
def test_quarter_handicap_has_half_loss():
    matrix = ScoreMatrix((1.0, 1.0), [[1.0]], 0)  # fixture de teste: empate certo
    d = distribution_for(matrix, 'Handicap Asiatico', 'AH Casa -0.25')
    assert d.half_loss == 1.0
    assert d.expected_return(2.0) == -0.5

# Fórmula usada pelo contrato:
ev = (win + half_win/2) * (price-1) - loss - half_loss/2
mass = win + half_win/2 + loss + half_loss/2
p_effective = (win + half_win/2) / mass if mass else None
```

- [ ] Calibrar p_effective com peso da exposição liquidada; push integral tem peso zero. Para EV calibrado, usar `mass*(p_cal*price-1)` preservando a massa prevista de liquidação. Registrar que isso calibra probabilidade efetiva, não identifica separadamente todos os outcomes.
- [ ] Testar +0.25, -0.25, -1, -1.5, OU 2/2.25/2.5, soma de distribuição=1, identidade de retorno contra settle_outcome por placar e market calibration isolada. Rodar `test_market_settlement_distribution.py`, `test_market_groups.py`, `test_backtest_metrics_ev.py`, `test_realtime_state_views.py`.

### Task 5: Gate CLV obrigatório e sete blocos conjuntivos

**Files:** Modify `betgsn/models/promotion.py`, `betgsn/odds_snapshots.py`, `betgsn/value_walkforward.py:prospective_clv_evidence`, `betgsn/api/quant_service.py`, `tools/clv_report.py`; Create `tests/test_strict_clv_gate.py`; evoluir `production_policy.py`.

**Interfaces:** `clv_gate(summary: ClvLifecycleSummary) -> GateBlock` em production_policy; `clv_statistics` mantém campos atuais e adiciona inteiro `n_positive` e proveniência da população CLOSED. `evaluate_promotion` recebe evidência estrita de produção e devolve critérios/sete blocos, sem liberar por campos legados ausentes.

- [ ] Escrever testes parametrizados n=199/200, positivos=109/110 em 200, média zero/negativa, mediana zero/negativa, null, NaN, retrospectivo e lifecycle misturado.

```python
def test_closed_only_and_exact_boundary(clv_summary_factory):
    summary = clv_summary_factory(closed=[.02]*110 + [-.001]*90, pending=700)
    assert clv_gate(summary).status == 'GREEN'
    fewer = clv_summary_factory(closed=[.02]*109 + [-.001]*91, pending=700)
    assert clv_gate(fewer).status == 'RED'
```

`clv_summary_factory` é fixture local a criar neste arquivo: construir ClvEntryRecord e CLVResult com IDs únicos, timestamps de decisão<close<kickoff, status OK; PENDING tem result=None. Nenhuma escrita no banco operacional.

- [ ] Rodar `tests/test_strict_clv_gate.py` e confirmar que n200/mediana/55% não estão implementados.
- [ ] Centralizar MIN_CLV_SAMPLE=200 e MIN_WINDOWS=3; avaliar sem arredondar antes de comparar. Usar inteiros para beat-close: `100*n_positive >= 55*n_closed`. Exigir proveniência explícita, estado CLOSED e `result.valid`; deduplicar pela identidade da entrada, não por número de quotes.
- [ ] Ausências não passam por retrocompatibilidade na elegibilidade de produção. Preservar separação entre status científico VALIDATED e produção; qualquer critério RED de produção é bloqueante. Manter exigência de CI positivo onde já aplicável, ruído, margem e drawdown existentes.
- [ ] Bloquear MODEL se faltar comparação conjunta nas mesmas janelas vs RAW e FAIR, EVgap ou estabilidade; reportar métricas reais por janela. ROBUSTNESS exige multi-liga/temporada e nenhum resultado dependente só de uma liga.
- [ ] Testar todos os recortes CLV (liga/mercado/book/banda/tempo até kickoff), amostras pequenas visíveis, referências incompatíveis e entradas repetidas não inflando n. Executar `test_strict_clv_gate.py`, `test_promotion_gate.py`, `test_clv_lifecycle.py`, `test_clv_operational_boundaries.py`, `test_quant_api.py`. Atualizar testes permissivos com justificativa explícita de endurecimento.

### Task 6: Stake exclusivamente lower_95 e gates obrigatórios

**Files:** Modify `betgsn/staking.py`, `betgsn/strategy_runner.py`, chamadores em `api/service.py`; Extend `tests/test_staking.py`, `tests/test_strategy_extensibility.py`, `tests/test_no_bet_first_class.py`.

**Interfaces:** `decide_bet` preserva ROI/SE diagnósticos, adiciona `lower_bound_95: float|None=None` e `production_gate: ProductionGate|None=None`. Ausência de gate bloqueia decisão operacional; nenhum default presume aprovação. Lower ausente deriva IC95% bilateral normal `roi - 1.96*roi_se` com método declarado; lower fornecido precisa ser finito e documentado na evidência.

O código legado usa z=1.6448536269514722 (limite unilateral 95%). A nova
política operacional usa o limite inferior bilateral 95%, mais conservador,
com versão/fingerprint explícitos; não alterar silenciosamente a constante
de seleção TRAIN `_Z_CONSERVATIVE` em `value_walkforward.py`. O método do IC
e sua população devem acompanhar o lower fornecido; não aceitar número
arbitrário como evidência. O teste de dimensionamento abaixo isola apenas
a fórmula, usando gate unitário controlado.

- [ ] Escrever regressão demonstrando dimensão pela borda inferior, não pelo ponto, e bloqueio com gate ausente/RED.

```python
def test_size_uses_lower95(all_green_gate):
    d = decide_bet(.12, .01, 2.0, lower_bound_95=.04,
                   evidence_status='timestamped', n_bets=1000,
                   production_gate=all_green_gate, promotion_eligible=True)
    assert d.fraction == pytest.approx(full_kelly(.04, 2.0)*DEFAULT_KELLY_FRACTION)
```

Fixture `all_green_gate` instancia os sete blocos GREEN; é cenário unitário, não prova de gate aprovado com dados reais.

- [ ] Rodar o teste e confirmar dimensão incorreta antes da mudança.
- [ ] Implementar `kelly = full_kelly(lower, odd)` após validar lower e gate. Usar lower também no campo `kelly_full` que sustenta stake; ponto pode ficar apenas em diagnóstico nomeado.
- [ ] Transportar o gate completo no runner. `promotion_eligible=True` sozinho não substitui blocos/evidência. Bloquear por CLV insuficiente, lower<=0, NaN/inf, odd<=1, amostra ausente e cada RED; manter `BetDecision(NO_BET).fraction==0`.
- [ ] Rodar `test_staking.py`, `test_strategy_extensibility.py`, `test_no_bet_first_class.py`, `test_portfolio_policy.py`, `test_real_signal_contract.py`; buscar produtores alternativos de stake e assegurar que UI operacional não exiba stake de pesquisa como recomendação autorizada.

### Task 7: PricedSignal, referências sharp e execução medida

**Files:** Create `betgsn/priced_signals.py`, `betgsn/priced_signal_store.py`, `tests/test_priced_signals.py`; Modify `betgsn/odds_snapshots.py` apenas para expor referências necessárias sem reescrever FIRST_WINS.

**Interfaces:** frozen `PricedSignal` com todos os campos da spec + `ev`, `line`, `policy_fingerprint`, `model_fingerprint`, `quote_fingerprint`, `gate`, `research_reasons`. `build_priced_signal(*, quote: NormalizedQuote, selected_quote: NormalizedQuote, model_prob: float|None, fair_prob: float|None, ev: float|None, spread: float|None, books: Sequence[NormalizedQuote], decision_timestamp: str, gate: ProductionGate, model_fingerprint: str) -> PricedSignal` lança `ValueError` com código DROP para campos essenciais inválidos. `ExecutionRecord(signal_id, executed_price, executed_at, book, source_reference)`; `execution_diagnostics(signal: PricedSignal, executions: Sequence[ExecutionRecord], close: dict|None) -> dict`.

- [ ] Testar observado/timestamp ausentes DROP, execução ausente null/UNKNOWN, seleção de outro evento/linha rejeitada, proveniência serializada sem secrets, IDs determinísticos e alteração de quote mudando fingerprint.
- [ ] Rodar `tests/test_priced_signals.py` RED; implementar dataclasses e validação usando identidade/parser existentes. Usar SHA256 JSON canônico, UUID determinístico, UTC real. Não usar `hash()` dependente de processo.
- [ ] Criar `PricedSignalStore(path)` com `append_signal(signal)`, `append_execution(record)`, `signals(event_id)`, `executions(signal_id)`. Tabelas novas versionadas no store existente ou arquivo de domínio definido pelo output_root; append-only e transação com UNIQUE IDs. Campos antigos continuam null. Sem endpoint de auto-bet.
- [ ] Registrar execução somente quando importada de registro explícito com preço, timestamp, casa e referência; não inferir de clique no terminal. Rejeitar execução anterior à decisão e referência de casa incompatível. Auditoria de correção nunca UPDATE de histórico.
- [ ] Separar `sharp_reference` por casa, `bookmaker_close`, `exchange_close`, `consensus_close`. Betfair Sportsbook não é Betfair Exchange; comissão ausente declara CLV bruto, sem afirmar resultado líquido. Referência ausente null + motivo.

```python
absolute = executed - observed
relative = absolute / observed
before = observed / closing - 1
after = executed / closing - 1
# Agregar somente a população pareada com execução E close válidos.
erosion = (mean_before - mean_after) / mean_before if mean_before > 0 else None
```

- [ ] Testar 2.10 observado, 2.02 executado, close 2.00: gap=-0.08, relativo=-0.08/2.10, before=.05, after=.01, erosão=.8 => RED EXECUTION_EROSION. Exatamente .5 não ativa erosão; melhora de preço não conta como piora. Sem close/gap medido continua UNKNOWN.
- [ ] Testar idempotência, reinício/roundtrip e primeira entrada preservada; executar `test_priced_signals.py` e testes CLV operacionais. Fazer revisão de segurança inline de SQL parametrizado, paths e JSON antes da integração API.

### Task 8: Harness OOS completo, ablação e robustez

**Files:** Create `betgsn/calibrated_value_validation.py`, `tools/calibrated_value_validation.py`, `tests/test_calibrated_value_validation.py`; Modify `betgsn/model_walkforward.py`, `betgsn/line_shopping_audit.py` para reuso de linhas, sem reinterpretar artifacts antigos.

**Interfaces:** `run_calibrated_value_validation(*, config: WalkForwardConfig, matches: Sequence[HistoricalMatch], bets: Sequence[dict], progress=None) -> dict`; CLI `--full --output PATH`, sem flag de ajuste por ROI. `evaluate_variants` usa nomes fixos BASELINE, SHRINKAGE, DECAY, HOME, BLEND, CALIBRATION, FILTER, BESTPRICE, CLV.

- [ ] Construir corpus de testes pequeno determinístico com três janelas e previsões controladas; labels alterados só no TEST não mudam parâmetros/fingerprints de treinamento. Artifacts de teste são explicitamente sintéticos e nunca prova econômica.
- [ ] Rodar `tests/test_calibrated_value_validation.py` RED; implementar coleta real reutilizando FootballDataClient/csv_odds_store. Preservar book/market/line/season, distribuição de liquidação, preços best/median, n_books, spread, timestamps e razões de ausência.
- [ ] Congelar manifesto de execução antes de iniciar TEST: SHA256 conteúdo corpus, arquivos de código relevantes, grids, thresholds, seeds, janelas e mercado. Preservar 730/2/365 externos e histórico ratings declarado; não recalcular limites por variante/população.
- [ ] Comparar variantes nas mesmas linhas com cobertura comum e também cobertura nativa explicitada. Ablação incremental usa ordem predefinida; leave-one-component-out refaz apenas fits necessários no TRAIN. FILTER usa edge E EV >=.08, spread<=.12 e books>=3; filtros históricos sem timestamp são apenas cenário RESEARCH. BESTPRICE compara mesma população a median/best e separa mudança de seleção.
- [ ] CLV no instante da decisão só usa closes anteriores disponíveis. Own-close futuro é proibido:

```python
def test_future_close_cannot_enable_signal():
    decision = '2024-01-01T12:00:00Z'
    closes = [{'closed_at': '2024-01-01T13:00:00Z', 'clv': .1}]*200
    eligible = [r for r in closes if utc_key(r['closed_at']) < utc_key(decision)]
    assert eligible == []
```

Além desse teste mínimo, executar o runner completo com a mesma população e confirmar CLV variante n=0/NO_BET, em vez de inserir fechamento conhecido posteriormente.
- [ ] Usar métricas/bootstrap em `evaluation.py`, `value_walkforward.py` e `models/robustness.py`; labels alinhados por IDs e janela. Reportar Brier, LogLoss, ECE, retorno/EV/EVgap, CLV, beat-close, execução e drawdown com n e null para desconhecido.
- [ ] Reportar liga/temporada/mercado/book/banda/janela, subsets e leave-one-league/season. Separar exclusão de segmento de refit leave-one-out; refit mantém seleção interna e nunca escolhe o melhor resultado. Manter todos os recortes e CI por blocos temporais.
- [ ] Testar linha fair ausente no meio, push, books faltantes, zero sinais e relatório JSON estrito `allow_nan=False`; executar testes do harness, `test_evaluation_statistics.py`, `test_line_shopping_audit.py`, `test_robustness.py`, `test_benchmark_manifest.py`.

### Task 9: Integração realtime, cache e API

**Files:** Modify `betgsn/realtime/{engine,signals,state,views}.py`, `betgsn/api/{realtime,quant_service,schemas}.py`, `betgsn/real_signals.py`; Create `tests/test_priced_realtime.py`; evoluir `tests/test_realtime_api.py`.

**Interfaces:** engine recebe snapshot congelado/bundle e gate auditáveis; `priced_signals(event_id: str|None=None) -> list[dict]` em engine. Board adiciona `priced_signals`, `production_gate`, `policy_fingerprint`; eventos existentes mantidos. Novo endpoint de leitura `/api/realtime/priced-signals`; nenhuma ação de aposta.

- [ ] Testar engine com três quotes, modelo e gate completo emitindo PricedSignal, duas casas LIMITED, sem modelo RESEARCH, qualquer RED NO_BET e stake zero. Mockar fonte externa somente nos testes unitários.
- [ ] Executar `tests/test_priced_realtime.py` RED; integrar funções puras sem refazer fit por quote. Snapshot live só pode usar dados disponíveis até seu train_end e decisão posterior; snapshot demo não é aceito como modelo real.
- [ ] Cache de inferência por `(event_id, model_fingerprint)`; cache de seleção por quote/policy/gate fingerprint e limite de freshness. TTL/sweep reavalia elegibilidade mesmo sem quote nova. Não reutilizar gate antigo após mudança no CLV/execution.
- [ ] Persistir primeiro sinal com proveniência e emitir atualização/expiração SSE. Provider/quote inválida produz DROP com razão sanitizada; não deixar falha de um evento interromper os demais. Desempenho: medir custo por evento antes/depois e provar em teste que evento não afetado não roda matriz novamente; carregar skill de performance antes de otimizações adicionais.
- [ ] Testar quote futura, timestamp offset equivalente, missing selection/market/line, reinício/store, TTL só com relógio e mudança de fingerprint; executar `test_priced_realtime.py`, `test_realtime_api.py`, `test_realtime_engine.py`, `test_realtime_signals.py`.

### Task 10: Terminal live com priced/gates/execution

**Files:** Modify `web/src/types/realtime.ts`, `web/src/api/realtime.ts`, `web/src/pages/LivePage.tsx`, `web/src/components/live/MatchDetail.tsx`, componentes `live` existentes; Create `web/src/components/live/PricedSignalCard.tsx`, `web/src/components/live/PricedSignalCard.test.tsx`.

**Interfaces:** tipos espelham PricedSignal/API com null explícito; cartão recebe `signal: PricedSignal` e apresenta fontes/preços e gate sem computar autorização no frontend.

- [ ] Criar testes de renderização para preço de execução null => UNKNOWN, gate RED => NO_BET/stake zero, dois books LIMITED, FRACA só RESEARCH e cada preço com seu rótulo.

```tsx
expect(screen.getByText('UNKNOWN')).toBeInTheDocument();
expect(screen.getByText('NO_BET')).toBeInTheDocument();
expect(screen.queryByRole('button', {name: /apostar automaticamente/i})).not.toBeInTheDocument();
```

- [ ] Rodar no diretório `web`: `npm test -- src/components/live/PricedSignalCard.test.tsx`; confirmar componente ausente.
- [ ] Implementar visualização usando Badge/Card/formatação existentes; exibir edge/EV/spread/books, best/fair/model, freshness, movimento, referências sharp/close e execução/gaps. null não vira 0 nem traço sem explicação; status/razão vêm do backend.
- [ ] Conservar stream/reconnect e filtros atuais. Não usar freshest book como prova de frescor do preço selecionado. Sem ação de auto-bet e sem segredos no bundle.
- [ ] Rodar `npm test -- src/components/live/PricedSignalCard.test.tsx src/pages/LivePage.test.tsx`, depois `npm run build`; verificar contratos API em conjunto com Task 9.

### Task 11: Execução da prova completa e smoke real

**Files:** Create `tools/realtime_smoke.py`, `tests/test_realtime_smoke_contract.py`; relatórios gerados em `output/engineering/quant/calibrated_value_20260924/` ignorados pelo Git; resumo versionado em `docs/CALIBRATED_VALUE_SELECTION.md`.

- [ ] Testar parser do smoke para SSE, reconnect, null e ausência de movimento sem declarar PASS fictício. CLI `--api-url http://127.0.0.1:8787 --web-url http://localhost:5180 --duration-seconds 180 --output PATH`; não aceitar token como argumento impresso.
- [ ] Implementar smoke com requests via bibliotecas já instaladas: health, board, providers, priced, SSE por tempo delimitado, reconexão com último ID e ausência de duplicação. Sanitizar erros; registrar contagens/IDs/freshness, nunca headers secretos. Sem novas quotes/movimento real, item fica BLOCKED/NOT_OBSERVED, não inventado.
- [ ] Rodar suíte Python completa: `& ".venv\Scripts\python.exe" -m pytest -q`; rodar em `web` `npm test` e `npm run build`. Corrigir falhas com reprodução específica, repetir apenas checks afetados e full final após alterações relevantes.
- [ ] Executar OOS completo, registrar início antes do TEST e esperar exit code real:

```powershell
& ".venv\Scripts\python.exe" tools/calibrated_value_validation.py --full --output output/engineering/quant/calibrated_value_20260924/report.json
```

Usar job_start/job_status para execução longa; verificar pais antes de gerar arquivos. Nenhum limite de janelas ou corte de corpus oculto. Não alterar grid após observar resultado.
- [ ] Verificar JSON com todas as variantes, n, janelas, fingerprints, segmentos, limitações e sem NaN; referência BASELINE deve ser reproduzida ou divergência explicada por correção de bug/fonte, nunca escondida.
- [ ] Verificar portas/processos antes de subir serviços, reutilizar instâncias somente se correspondem a este código. Subir backend/frontend pelos comandos de `dev.ps1` com logs locais ignorados; `BETGSN_REALTIME=1` apenas no processo de smoke, mantendo quotas existentes. Não instalar agendamento.
- [ ] Rodar smoke CLI e conferir terminal em navegador, network/SSE e reconnect. Registrar data real, health dos providers e cobertura. Encerrar apenas processos criados pela tarefa quando necessário e documentar os que permanecerem ativos.
- [ ] Escrever tabela BASELINE/variantes vs MARKET_RAW/FAIR, benefícios marginais, n/janelas e status por bloco. CLV sem amostra ou execução não medida permanecem bloqueios de produção, mesmo que testes/build passem.

### Task 12: Revisão final, publicação e relatório

**Files:** arquivos intencionais em `betgsn`, `tests`, `tools`, `docs`, `web`, `scripts`; nenhum artifact operacional bruto no staged.

- [ ] Revisar diff por fluxo: fonte p_model real, cutoff/labels, máscaras, gates, lower_95, AH, persistence e UI. Carregar skills de revisão/verificação aplicáveis; revisão inline, sem delegação não solicitada.
- [ ] Revisar segurança dos novos caminhos: SQL parametrizado, path do store fixado em output_root, dados de providers não executáveis, JSON não confiável, mensagens sem tokens, ausência de secrets no frontend. Rodar testes de contrato/smoke parser.
- [ ] Executar e revisar:

```powershell
git status --short --branch
git diff --stat
git diff --check
git log --oneline -10
git fetch origin
git rev-list --left-right --count HEAD...origin/main
```

- [ ] Se behind, parar sem pull. Conferir tamanho e nomes apenas dos arquivos candidatos, excluir logs/db/env/tmp/caches e >100MB. Stage por caminhos explícitos da implementação, nunca `git add .`; não incluir o Ensemble pré-existente.
- [ ] Revisar `git diff --cached --stat`, `git diff --cached --check`, `git diff --cached`; confirmar somente código/testes/docs pretendidos e ausência de secrets. Registrar resultados finais dos checks no relatório antes de stage definitivo.
- [ ] Commit e push, sem amend/force:

```powershell
git commit -m "Improve calibrated value signal selection"
git push origin main
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

- [ ] Se hook falhar, corrigir causa e criar commit novo; se push rejeitado por atualização remota, parar e relatar. Não declarar push por inferência: confirmar saída e SHAs.
- [ ] Entregar commit/main/push, tabela de métricas e status IMPLEMENTADO/VALIDADO/EXPLORATÓRIO/PENDENTE/BLOCKED para MODEL/CALIBRATION/SELECTION/MARKETS/PRICED/CLV/ROBUSTNESS/STAKING/REALTIME/TESTS/BUILD/PRODUCTION_ELIGIBLE/NO_BET/LIMITAÇÕES. Resultado inferior ao mercado deve constar explicitamente.

## Auto-revisão de cobertura

| Requisito da especificação | Tarefas |
|---|---|
| Cinco correções obrigatórias | 1, 2, 4, 5, 6 |
| Shrinkage/decay/home/blend train-only e fingerprints | 2, 3, 8 |
| Platt/isotonic/temperature por mercado | 2, 4 |
| Market RAW/FAIR pareados e 3+ janelas | 2, 5, 8 |
| Sharps, PricedSignal, execução, erosão | 7, 9, 10 |
| CLV CLOSED n200/mean/median/55%, recortes | 5, 7, 8 |
| AH/OU, mercados pausados, pesquisa mantida | 1, 4, 9, 10 |
| Lower95, 1 RED, NO_BET | 1, 5, 6, 9 |
| Robustez, ablação, preço/população separados | 8, 11 |
| Realtime/data quality/incremental/cache/SSE | 4, 9, 10, 11 |
| Testes/build/full OOS/smoke real | 1–11 |
| Segurança/Git/relatório | 7, 9, 11, 12 |

Interfaces do plano usam `ProductionGate.production_eligible`, `CalibrationRow.p_model`,
`TrainingParams.blend_weights`, `PricedSignal.executed_price` e
`SettlementDistribution.expected_return` de forma consistente. Campos de contratos
legados `execution_price` serão mapeados explicitamente, não renomeados silenciosamente.
Todos os cinco Review Focus têm testes nas tarefas responsáveis. Nenhum artifact
novo de desempenho econômico foi produzido durante o planejamento.

**Handoff:** revisar este plano antes da implementação; execução direta no main
preservada. O plano não autoriza liberação de produção sem evidência real.
