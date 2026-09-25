# SDD ledger — plan: docs/superpowers/plans/2026-09-24-calibrated-value-selection.md

BASE: 9985b45. Execução aprovada diretamente em main; fetch inicial 0/0.

Ruling: ledger versionável em docs junto ao plano, ferramentas nativas de edição
e testes em lugar dos wrappers shell POSIX — ambiente PowerShell e commit final
único autorizado — custo: atualização manual do registro de testes.

Pre-flight: Tasks 1/5/6/9 usam ProductionGate.production_eligible; status ausente
não aprova. Tasks 2/3/8 usam previsões internas com fronteira real de fit.
Tasks 4/7/8 usam probabilidade efetiva e EV ponderado pela massa liquidada.
Tasks 7/9/10 usam executed_price; mapear execution_price legado explicitamente.
Task 6: lower bilateral 95% operacional, sem mudar seletor TRAIN legado.
Task 1: em andamento; política, fingerprint e classificação estrita.

Task 1: RED 21 testes (2 falhas reais de FORTE e interfaces novas ausentes),
GREEN 43 testes policy/real_signal_contract/no_bet_first_class. Fingerprint
7b0a4d8837f6e54b48693c22d8c678ccccbb553dbf96816f4442e5e2c78edef3 revisado.
Integração ao produtor operacional completo depende das tarefas 5/7/9.
Task 2: RED sonda do harness chama calibrador de mercado; novos testes RED,
7 testes específicos GREEN após novo caminho p_model. Regressões legadas
expuseram fixture _Match sem result_available_at.
Ruling: completar fixture com campos HistoricalMatch ausentes — o guard de
publicação deve continuar estrito — custo: fixture precisa evoluir com contrato.

Ruling: antecipar correções isoladas AH/stake/CLV antes da modelagem extensiva
para fechar os cinco achados — dependem apenas dos contratos já definidos —
custo: tarefas 3–10 ainda necessitam integração e não são completas.
Cinco conjuntos específicos: 61 passed (policy, model_calibration_source,
strict_clv_gate, lower95_stake, ah_signed_line), todos após RED observado.
Suite targeted model/ensemble/leakage: 55 passed, 72 warnings LightGBM legados.
Suite global inicial excedeu timeout 120s; reiniciada via job
05ef10798e90401780ff073f2f606e9b. Não declarar suite verde até exit real.
Ruling: testes que liberavam BET só por timestamped/promotion legado passam a
exigir NO_BET — comportamento permissivo foi explicitamente revogado — custo:
consumidores precisam transportar evidência dos sete blocos antes de liberar.
