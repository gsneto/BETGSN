# BETGSN — Preditor Estatistico de Futebol

Aplicativo desktop que estima probabilidades de mercados de futebol a partir
de um modelo estatistico, compara com as odds de varias casas de aposta e
gera sinais (dicas) ranqueados por valor esperado (EV).

**BETGSN LIVE**: alem do preditor, o BETGSN agora e um **terminal de mercado
de odds em tempo real** — captura continua de 3 providers, movimento,
market fair, line-shopping e sinais EXPLICAVEIS via SSE. Ver secao
"Terminal de mercado em tempo real" abaixo.

Nao promete lucro. Mede edge. Se a probabilidade do modelo estiver errada,
o resultado estara errado — isso e matematica, nao promessa.

## Como rodar

### Interface React (nova)

```powershell
# 1) dependencias da API (uma vez)
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-api.txt

# 2) sobe o backend (FastAPI) — deixa rodando
.\.venv\Scripts\python betgsn.py --api

# 3) em outro terminal, sobe o frontend
cd web
npm install
npm run dev          # http://localhost:5180
```

O frontend fala com o backend por HTTP/WebSocket em `127.0.0.1:8787`.
Em dev o Vite faz proxy de `/api`, entao nao ha configuracao extra.

> **Porta:** o BETGSN usa a **5180** por padrao (a 5173 costuma estar
> ocupada por outros projetos). Para trocar:
> `$env:BETGSN_WEB_PORT = 5190; npm run dev`
> Se o backend estiver em outra maquina/porta:
> `$env:BETGSN_API_URL = "http://127.0.0.1:9000"; npm run dev`

### Interface desktop (legada, Tkinter)

A GUI antiga continua funcionando durante a migracao, para comparacao
lado a lado das duas interfaces.

```powershell
python betgsn.py              # abre a interface desktop
python betgsn.py --cli        # roda o pipeline e imprime os sinais no console
python betgsn.py --selftest   # valida o pipeline ponta a ponta (26 checks)
python betgsn.py --providers  # mostra quais fontes de dados reais tem chave
python betgsn.py --backtest   # backtest legado (split unico)
python betgsn.py --walkforward --min-history=250 --min-ev=3   # backtest rolling
python betgsn.py --sources    # estado dos caches de dados historicos

# dados historicos (opcional)
python betgsn.py --import-fduk --seasons=2000-2025   # ligas europeias + ODDS REAIS (CSV publico, sem chave)
python betgsn.py --import-odds --sport=soccer_brazil_campeonato `
    --start=2024-04-01 --end=2024-06-30              # odds historicas (The Odds API, plano pago)
python betgsn.py --import-fixtures --league=71 --season=2024 --with-stats   # API-Football
python betgsn.py --capture-odds --sports=soccer_brazil_campeonato   # captura odds ao vivo
```

O nucleo estatistico nao tem dependencias externas: usa apenas a
biblioteca padrao do Python 3.10+ (Tkinter incluso). Ver `requirements.txt`.
As dependencias de `requirements-api.txt` sao necessarias **somente** para
a camada HTTP/WebSocket.

## Estrutura

```
BETGSN/
├── betgsn.py           launcher (GUI / API / CLI / selftest / providers)
├── betgsn/
│   ├── engine.py       odds, probabilidade implicita, vig, EV, Kelly, arbitragem
│   ├── model.py        ratings de ataque/defesa, Poisson + Dixon-Coles, cantos, cartoes
│   ├── markets.py      mercados (1X2, over/under, BTTS, handicap, cantos, cartoes)
│   ├── data.py         dataset local + gerador de odds multi-casa
│   ├── providers.py    fontes reais opcionais (The Odds API, API-Football, Football-Data)
│   ├── signals.py      gerador de sinais e dicas ranqueadas por EV
│   ├── pipeline.py     orquestracao: dados -> modelo -> mercados -> odds -> sinais
│   ├── backtest.py     validacao out-of-sample (calibracao + aposta)
│   ├── backtest_data.py     historico point-in-time, odds, settlement
│   ├── backtest_engine.py   backtest rolling walk-forward
│   ├── backtest_metrics.py  calibracao, Brier, EV, drawdown, IC, segmentos
│   ├── backtest_store.py    persistencia SQLite das execucoes
│   ├── backtest_sources.py  importacao de odds e temporadas reais + cache
│   ├── timeutil.py     normalizacao de kickoff para UTC (anti data leakage)
│   ├── theme.py        design system da GUI legada
│   ├── widgets.py      componentes visuais da GUI legada
│   ├── gui.py          interface desktop (Tkinter)
│   └── api/            camada HTTP/WebSocket (FONTE DE VERDADE para o React)
│       ├── schemas.py  contratos Pydantic (dashboard, sinais, jogos, odds, modelo)
│       ├── backtest_schemas.py  contratos do backtest
│       ├── service.py  executa o pipeline e traduz RunResult -> contratos
│       ├── backtest_service.py  jobs de backtest + progresso
│       └── server.py   rotas FastAPI + WebSocket de status
├── web/                interface React (camada de APRESENTACAO)
│   └── src/
│       ├── api/        cliente HTTP centralizado (nada de fetch espalhado)
│       ├── types/      espelho TypeScript dos contratos Pydantic
│       ├── hooks/      useApiResource, useBetgsnSocket, useBacktestJob
│       ├── store/      estado global (Context + reducer, sem Redux)
│       ├── layout/     AppHeader, TabBar, ControlBar, StatusBar
│       ├── components/ primitivos de UI + componentes de dominio
│       └── pages/      Signals, Games, Odds, Stats, Model, Backtest
├── tests/              testes da API, do backtest e de data leakage
├── data/               espaco para datasets reais (CSV/JSON)
└── output/             relatorios exportados + backtests/betgsn_backtest.db
```

## Arquitetura da migracao

A regra e simples: **o frontend apresenta, o backend calcula.**

```
┌──────────────────────────────────────┐
│  React 19 + TypeScript + Vite        │
│  Tailwind CSS v4 · design tokens      │
│  Sinais · Jogos · Casas/Odds ·        │
│  Estatísticas · Modelo                │
└────────────────┬─────────────────────┘
                 │ HTTP (/api) + WebSocket (/api/ws)
┌────────────────▼─────────────────────┐
│  FastAPI (betgsn/api)                │
│  ────────────────────────────────    │
│  pipeline · engine · model ·         │
│  markets · signals · backtest        │
│  (fonte de verdade estatistica)      │
└──────────────────────────────────────┘
```

O React **nao** recalcula Poisson, Dixon-Coles, probabilidades, edge, EV,
Kelly nem pesos. Ele recebe valores prontos e apenas formata, ordena,
filtra e visualiza. Os contratos estao em `betgsn/api/schemas.py` e sao
espelhados em `web/src/types/api.ts`.

### Endpoints

| Metodo | Rota | Conteudo |
| --- | --- | --- |
| GET | `/api/health` | status do sistema |
| GET | `/api/dashboard` | resumo + KPIs |
| POST | `/api/recalculate` | roda o pipeline com nova configuracao |
| GET | `/api/signals` | sinais ranqueados por EV |
| GET | `/api/games` | probabilidades por jogo |
| GET | `/api/odds` | resumo por casa |
| GET | `/api/odds/comparison` | comparacao multi-casa + arbitragem |
| GET | `/api/stats` | breakdowns e ratings |
| GET | `/api/model` | parametros e documentacao do modelo |
| GET | `/api/model/performance` | backtest (calibracao + aposta) |
| GET | `/api/backtest/options` | periodo, competicoes, mercados, defaults |
| POST | `/api/backtest/run` | dispara o backtest em background |
| GET | `/api/backtest/status` | progresso da execucao atual |
| GET | `/api/backtest/runs` | execucoes salvas |
| GET | `/api/backtest/runs/{id}` | metricas completas |
| GET | `/api/backtest/runs/{id}/signals` | sinais paginados e pesquisaveis |
| DELETE | `/api/backtest/runs/{id}` | remove uma execucao |
| GET | `/api/backtest/compare` | compara duas execucoes |
| WS | `/api/ws` | status/progresso em tempo real |

Docs interativas em `http://127.0.0.1:8787/docs`.

## Terminal de mercado em tempo real (BETGSN LIVE)

A aba **LIVE** e um market terminal — nao uma pagina de jogos e muito
menos um bot de apostas. O fluxo:

```
PROVIDERS -> ODDS EM TEMPO REAL -> NORMALIZACAO -> EVENT MATCHING ->
SNAPSHOT STORE (append-only) -> MOVEMENT -> MARKET FAIR -> LINE
SHOPPING -> MICROSTRUCTURE -> SIGNALS -> TERMINAL -> USUARIO DECIDE
```

### Como iniciar / parar / verificar

```powershell
.\start.ps1     # backend + realtime engine + frontend + navegador
.\stop.ps1     # para TUDO (engine graceful, backend, frontend)
.\status.ps1   # "o sistema esta vivo?" — providers, ticks, ultimos eventos
```

O engine sobe junto com o backend (a menos que `BETGSN_REALTIME=0`).
A captura roda em loop com intervalo configuravel — ver tabela abaixo.

### O que o terminal responde em segundos

- quais jogos estao por vir e quais mercados tem odds;
- quantas casas cotam, qual a melhor odd (e DE QUEM), a mediana, o fair;
- o que mudou, quando mudou e quais casas moveram;
- onde ha dispersao e line-shopping;
- quais sinais estao ativos, DE ONDE vem e POR QUE;
- quao recente e cada quote (FRESH / RECENT / STALE / UNKNOWN com idade).

### Sinais informativos (nunca apostas)

Tipos emitidos pelo signal engine (`betgsn/realtime/signals.py`), todos
carregando `reason` (o por que), `evidence` (os numeros), bookmakers
participantes, `observed_at` e `production: NO_BET`:

| Tipo | O que significa |
| --- | --- |
| `STALE_PRICE` | uma casa continua com quote antiga enquanto as demais atualizaram |
| `BOOKMAKER_OUTLIER` | preco desvia da mediana das DEMAIS casas |
| `CONSENSUS_MOVE` | varias casas moveram na mesma direcao na mesma janela |
| `BOOKMAKER_LEAD` / `BOOKMAKER_LAG` | quem moveu primeiro e quem so moveu depois |
| `RAPID_CONVERGENCE` | spread entre casas encolheu apos movimentos |
| `DISPERSION_SPIKE` | dispersao entre casas disparou |
| `PRICE_REVERSAL` | uma casa inverteu a direcao do movimento |
| `BEST_PRICE_GAP` | melhor preco acima da mediana (line-shopping) |

Sinais envelhecem: ACTIVE -> STALE -> EXPIRED (TTL configuravel) e a
condicao que sumiu expira o sinal com motivo (`CONDITION_CLEARED`).
Evento UNMATCHED (sem fixture correspondente) nunca gera sinal.

MARKET (mediana entre casas), MARKET_FAIR (devig) e MODEL (pipeline)
sao apresentados SEPARADOS no detalhe do jogo. `model_minus_market` e
informacao quantitativa — a evidencia OOS atual mostra que os modelos
experimentais NAO superam o mercado.

### Endpoints realtime

| Metodo | Rota | Conteudo |
| --- | --- |
| GET | `/api/realtime/status` | retrato do sistema (engine, providers, ultimos eventos) |
| GET | `/api/realtime/board` | signal board: eventos + mercados + sinais + movimentos |
| GET | `/api/realtime/match?event_key=` | detalhe: grid por casa, timeline, fair, modelo, CLV |
| GET | `/api/realtime/signals` | sinais ativos + registro de alphas |
| GET | `/api/realtime/providers` | saude dos providers (loop + store) |
| GET | `/api/realtime/stream` | SSE: ODDS_UPDATE, MOVEMENT, SIGNAL_*, PROVIDER_STATUS, HEALTH_UPDATE |
| POST | `/api/realtime/start` / `stop` | liga / desliga o engine |

O frontend consome o SSE apenas como gatilho de "algo mudou": os dados
vem dos endpoints tipados (sem estado duplicado no cliente). O stream
encerra com `event: CLOSE` apos `max_seconds` e o EventSource reconecta
sozinho.

### Configuracao por ambiente

| Variavel | Default | Efeito |
| --- | --- | --- |
| `BETGSN_REALTIME` | `1` | `0` desliga o engine |
| `BETGSN_REALTIME_INTERVAL` | `300` | segundos entre ticks (minimo 30) |
| `BETGSN_REALTIME_PROVIDERS` | The Odds API, ParlayAPI, OddsPapi | providers operacionais |
| `BETGSN_REALTIME_SPORTS` | derivado dos fixtures | sport keys capturados |
| `BETGSN_REALTIME_MARKETS` | `h2h,totals,btts` | mercados pedidos |
| `BETGSN_REALTIME_FRESH/RECENT/STALE_SECONDS` | 300/900/3600 | thresholds de frescor |
| `BETGSN_REALTIME_CONSENSUS_BOOKS` | `3` | casas minimas para CONSENSUS_MOVE |
| `BETGSN_REALTIME_RAPID_SECONDS` | `180` | janela (s) em que um movimento conta como RAPID |
| `BETGSN_REALTIME_TTL_SECONDS` | `1800` | idade maxima de um sinal antes de EXPIRED |

Providers da operacao atual: **The Odds API**, **ParlayAPI**, **OddsPapi**.
Odds-API.io e OpticOdds ficam **LEGACY**: permanecem registrados para
health/historico, mas NAO participam do capture nem do engine
(`operational_providers()`). Para uma validacao pontual de um legacy, use
`--providers=<nome>` explicitamente (com aviso). Um provider que falha
entra em **circuit breaker** (cooldown; half-open apos o periodo) e deixa
de ser chamado ate recuperar — nao martela chave invalida. Nenhum provider
derruba o sistema: falha vira health/erro contabilizado e o tick seguinte
segue. O log operacional fica em `output/logs/realtime.jsonl` (rotativo,
fora do Git).

### Honestidade de dados (regras fixas)

- quote sem timestamp real nao entra; carimbo futuro e rejeitado e vira
  problema de qualidade visivel no terminal;
- o snapshot store e append-only e deduplicado por
  (match, market, outcome, book, timestamp) — nada e sobrescrito;
- CLV nunca fabrica close: `n = 0, mean = null` quando nao ha entradas;
- promotion gate e NO BET permanecem intactos: sinal informativo NAO e
  acao elegivel de producao.

## Alpha Lab (laboratorio quantitativo)

O **Alpha Lab** descobre honestamente se um sinal antecipa movimento de
preco — sem fabricar ROI. Ver `docs/ALPHA_LAB.md`.

- Replay **point-in-time** do store: o estado do sinal em T usa so quotes
  `<= T`; o futuro e lido apenas para MEDIR (+5/15/30/60min e closing).
- Mesmos thresholds da producao (`SignalRules`); nenhuma logica paralela.
- Veredito por evidencia: n, IC bootstrap, consistencia por dimensao.
- Registry central: `RESEARCH` / `INSUFFICIENT_DATA` / `VALIDATED` /
  `FRAGILE` / `NO_EVIDENCE` / `BLOCKED` / `PRODUCTION_CANDIDATE`.

Comando:

```
python tools/alpha_lab_run.py --stride=1800
python tools/provider_diagnostics.py
```

Resultado atual (fingerprint `1b6e922b85ad2c08`, 51.865 observacoes):

| Sinal | n | Metrica | Status |
|---|---:|---|---|
| BOOKMAKER_OUTLIER | 40.919 | move assinado +5m ~0,05% (IC inclui 0) | **NO_EVIDENCE** |
| BEST_PRICE_GAP | 7.155 | convergencia 30,9% (gap persiste) | VALIDATED (observacao) |
| DISPERSION_SPIKE | 3.791 | convergencia 30,7% | VALIDATED (observacao) |

Leitura honesta: o OUTLIER (62% dos sinais criados) **nao** antecipa o
mercado — majoritariamente reverte. BEST_PRICE_GAP e DISPERSION_SPIKE
persistem, mas sem CLV/closing nao ha veredito de edge. **CLV BLOCKED
(n=0), EXECUTION UNKNOWN → NO_BET.**

Endpoints de observabilidade e laboratorio:

```
GET /api/realtime/metrics      contadores (dado/provider/matching/freshness)
GET /api/realtime/market       vista de mercado ao vivo (INFORMACIONAL, NO_BET)
GET /api/quant/alpha-lab       artefatos do Alpha Lab (somente leitura)
```

## Backtest (validacao historica)

A aba **BACKTEST** responde: *"se o algoritmo tivesse rodado naquele
momento, usando so o que existia antes da partida, quais sinais ele teria
produzido — e quao confiaveis eram as probabilidades?"*

### Zero data leakage

Para cada partida M com kickoff T, o motor:

1. corta o contexto com `corpus.matches_before(T)` — **estritamente** antes
   de T (partidas simultaneas tambem ficam de fora);
2. refaz o fit dos ratings **so** com esse corte;
3. recalcula a media de gols da liga **so** com esse corte;
4. chama `pipeline.analyze_fixture` — o **mesmo** caminho do Scanner;
5. gera odds point-in-time;
6. chama `signals.generate_signals` — a **mesma** funcao do Scanner;
7. **congela** o sinal num `FrozenSignal` (imutavel, sem campo de resultado);
8. **so entao** resolve contra o placar real, gerando um `SettledSignal`.

O passo 7/8 e estrutural, nao uma convencao: `FrozenSignal` nao possui
campo de resultado, entao e impossivel escrever o futuro dentro da
previsao. Os testes provam isso alterando resultados futuros e exigindo
que o sinal anterior permaneca identico.

### Metricas

Nao e taxa de acerto. E calibracao:

- **calibracao** por faixa de probabilidade (previsto vs observado) com IC
  de Wilson — *"quando o modelo diz 60%, acontece 60%?"*;
- **Brier score** e **Log Loss** (probabilisticos);
- **analise por faixa de EV** — o EV previsto tem poder preditivo?;
- **performance temporal** (dia/semana/mes) — o desempenho se concentra
  numa janela?;
- **curva de capital virtual** com drawdown, sequencias e volatilidade;
- **segmentacao** por competicao, mercado, odd, probabilidade, EV,
  confianca, mes e temporada;
- **intervalos de confianca** e marcacao de **AMOSTRA INSUFICIENTE**
  (abaixo de 30 sinais liquidados).

### Auditoria

Cada execucao tem `run_id`, hash da configuracao e versao do modelo. A
tabela de sinais permite abrir qualquer previsao e ver exatamente o que o
algoritmo usou: quantas partidas anteriores existiam, a media de gols
naquele instante, os ratings e lambdas, a origem e o timestamp da odd.
A comparacao A vs B esta pronta para "modelo antigo vs modelo novo".

### Sobre as odds (limitacao honesta)

O BETGSN **nao possui odds historicas reais por padrao**. Gerar odds a
partir das probabilidades do proprio modelo seria circular: o modelo
pareceria ter edge onde o ruido tivesse sido favoravel.

Ha **duas fontes de odds** no backtest:

| Fonte | O que e | Para que serve |
| --- | --- | --- |
| `naive_synthetic` | baseline que so conhece as medias da liga anteriores ao kickoff e ignora a forca dos times | *"o modelo agrega informacao sobre esse baseline?"* — **nao e um bookmaker real** |
| `real_historical` | capturas reais da The Odds API, com timestamp por snapshot | *"o modelo bate o mercado?"* — a pergunta que importa |

### Importando dados reais

```powershell
# 1) LIGAS EUROPEIAS + ODDS REAIS — fonte publica, sem chave de API
python betgsn.py --import-fduk --seasons=2000-2025
#    ~600 CSVs (football-data.co.uk), rate limit 0.6s, cache em disco.
#    Traz resultados, estatisticas de partida e odds reais de Pinnacle,
#    Betfair Exchange, Bet365 e mais — abertura E fechamento.

# 2) ODDS AO VIVO -> historico (The Odds API, plano gratuito)
$env:BETGSN_ODDS_API_KEY = "..."          # the-odds-api.com
python betgsn.py --capture-odds --sports=soccer_brazil_campeonato
#    Agende 1x/dia. Cada captura vira odds historica quando a partida
#    acontecer. E o caminho para acumular serie sem pagar.

# 2a) CAPTURA DIARIA RECORRENTE (todos os providers configurados)
.\scripts\capture_daily.ps1
#    Wrapper fino do --capture-odds: log em output\logs\, exit code do
#    CLI (0 ok / 1 config / 2 erros de captura), sem chaves no script.
#    Idempotente (INSERT OR IGNORE por chave fisica); falha de um
#    provider nao interrompe os demais.
#
#    Agendamento (Task Scheduler) — configurar EXPLICITAMENTE:
#      schtasks /Create /SC DAILY /ST 09:00 /TN BETGSN-capture-odds ^
#        /TR "pwsh -NoProfile -File C:\caminho\BETGSN\scripts\capture_daily.ps1"
#
#    A captura diaria e o que alimenta o CLV prospectivo (entradas
#    FIRST-WINS no instante da decisao + fechamento pos-entry). Monitor:
#      python tools/clv_report.py    # n, media, distribuicao, BLOCKED <30

# 3) TEMPORADAS REAIS (API-Football, plano gratuito 2022-2024)
$env:BETGSN_APIFOOTBALL_KEY = "..."       # api-football.com
python betgsn.py --import-fixtures --league=71 --season=2024 --with-stats

python betgsn.py --sources                # confere o que foi importado
```

Chaves ficam em `.env` (na raiz, ignorado pelo Git) ou no ambiente. Copie
`.env.example` para começar. **Nunca** versione o `.env`.

### As três fontes de odds

| Fonte | O que é | Custo | Onde funciona |
| --- | --- | --- | --- |
| `naive_synthetic` | baseline que só conhece as médias da liga | grátis | sempre (offline) |
| `football_data_uk` | **odds reais** de 10 bookmakers, abertura e fechamento | grátis | ligas europeias + 16 extras |
| `real_historical` | capturas da The Odds API com timestamp | plano pago | qualquer (via `--capture-odds`) |

O `naive_synthetic` **não é um bookmaker**: ele precifica todo jogo com a
mesma distribuição. Medir o modelo contra ele infla o resultado — foi
exatamente o que o backtest expôs (veja abaixo).

### O que o backtest revelou

Top-5 europeu (Premier League, La Liga, Serie A, Bundesliga, Ligue 1),
2018-2025, 13.334 partidas avaliadas, 3 mercados (1X2, over/under,
handicap), banca virtual de 1.000:

| Cenário | Sinais | Taxa | EV previsto | Retorno real | Gap EV | ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Todas as casas · fechamento | 15.127 | 32,3% | +22,4% | −1,68% | +24,1pp | **−5,49%** |
| Todas as casas · abertura | 17.188 | 31,3% | +21,4% | −3,13% | +24,5pp | **−6,98%** |
| Casas afiadas · fechamento | 5.830 | 37,4% | +23,1% | +0,46% | +22,6pp | **−1,29%** |
| Casas afiadas · abertura | 5.672 | 37,0% | +21,7% | −0,53% | +22,2pp | **−1,93%** |

Antes, contra o baseline sintético, a mesma configuração mostrava
**+219.348%**. Com odds reais, o modelo **perde dinheiro em 3 de 4
cenários**.

O gap entre EV previsto e retorno realizado é de **+22 a +24 pp** em todos
os cenários: o modelo é sistematicamente **superconfiante**, não mal
calibrado por acaso.

Esse número de +219.348% não media habilidade — media a distância entre o
modelo e um espelho. Trocado o espelho pelo mercado real, a vantagem
desaparece. **Esse era o ponto do backtest.**

### Limitação de design: consenso exige ≥3 casas

`signals.MIN_BOOKS = 3`. Filtrar as odds para **uma única casa** sempre
resulta em `DESCARTE` — não é bug, é a regra que impede "consenso" de uma
fonte só. Use um subconjunto com pelo menos 3 casas (ex.: Pinnacle +
Betfair Exchange + Bet365).

### Sobre a licença do football-data.co.uk

O site libera uso por **pessoas físicas para análise pessoal** e proíbe
redistribuição comercial e coleta automatizada agressiva. O importador
baixa com rate limit, User-Agent identificado e cache (não rebaixa o que
já tem). **Não redistribua os CSVs** nem os use para treinar modelo que
você vá vender.

### Garantia point-in-time nas odds

Um snapshot posterior ao kickoff e inutil (e envenena o backtest). O
importador grava o `timestamp` informado pela API; o cache so devolve
snapshots **estritamente anteriores** ao kickoff e dentro da janela de
validade (`odds_max_age_hours`, padrao 24h). Sem snapshot valido, o
backtest **falha** em vez de cair para o mercado sintetico — misturar as
duas hipoteses no mesmo resultado seria pior que nao rodar.

### Fuso horario

Todo kickoff e normalizado para UTC (`betgsn/timeutil.py`) antes de
qualquer ordenacao ou corte. Isso permite misturar fontes com fusos
diferentes (dataset local sem fuso, API-Football com offset) sem que a
ordem cronologica se quebre — o que seria uma forma silenciosa de data
leakage. O horario original fica em `kickoff`; a comparacao usa
`kickoff_utc`.

No Windows, fusos por nome IANA (ex.: `America/Sao_Paulo`) exigem o pacote
`tzdata` (`pip install tzdata`). Horarios com offset explicito — o que as
duas APIs entregam — nao precisam dele.

### Testes

```powershell
# backend: paridade, contratos, backtest, data leakage, importacao
.\.venv\Scripts\python -m pytest tests -q

# frontend: formatadores, cliente HTTP, hooks e componentes
cd web
npm test

# validacao do motor estatistico
.\.venv\Scripts\python betgsn.py --selftest
```

## Estrutura

## O modelo

1. **Ratings de time** (`fit_ratings`) — ataque e defesa por ponto fixo, estilo
   Maher. `attack ~ 1.0` e a media da liga; `attack 1.30` cria ~30% mais gols.
2. **Gols esperados** — `lambda = ataque * defesa_adversaria * base`, com
   vantagem de casa e mistura de GOLS e xG (xG e mais estavel).
3. **Matriz de placar** — Poisson bivariado com correcao Dixon-Coles
   (`rho = -0.05`) para placares baixos. Dela saem 1X2, over/under, BTTS,
   handicap e total por time.
4. **Cantos e cartoes** — Poisson de contagem com taxa (ataque + defesa
   adversaria)/2; cartoes ajustaveis por rigor do arbitro.
5. **Mercado x modelo** — pega a melhor odd de cada casa, remove o vig do
   consenso (mediana entre casas) e compara:
   `edge = prob_modelo - prob_mercado`, `EV = prob_modelo * melhor_odd - 1`.
6. **Sinal e stake** — FORTE `EV >= 8%`, MEDIA `>= 4.5%`, FRACA `>= 2%`.
   Stake por Kelly fracionado (padrao quarter-Kelly) com teto configuravel
   (padrao 1% da banca por aposta) e exposicao total de 25%.

## Matemática percentual da banca

Para cada sinal a interface mostra:

```text
EV por unidade       = probabilidade_modelo * odd - 1
stake recomendada    = banca_atual * min(quarter_kelly, risco_maximo)
lucro esperado       = stake * EV
lucro se vencer      = stake * (odd - 1)
perda se perder      = stake
exposição total      = soma das stakes / banca
```

O campo **Banca** recebe o valor inicial. O campo **Risco/aposta %** define
o teto percentual por aposta; por padrão é 1%. Assim, banca de 1.000 gera
teto de 10 por aposta e banca de 2.500 gera teto de 25. Depois de um
resultado real, a banca nova vira a base da próxima stake: isso é crescimento
composto. A exposição total fica limitada a 25% da banca e a interface
exibe lucro esperado e perda máxima.

EV é retorno médio teórico, não lucro garantido. O backtest deve validar a
calibração do modelo antes de aumentar risco.

## Abas da interface

- **SINAIS** — dicas ranqueadas por EV, com confianca, odd, casa, edge e stake.
- **JOGOS** — probabilidades do modelo por jogo (1X2, over 2.5, BTTS, cantos, cartoes).
- **CASAS / ODDS** — comparacao de odds entre as 10 casas por mercado, melhor odd
  e deteccao de arbitragem.
- **ESTATISTICAS** — ratings de ataque/defesa, xG, cantos, cartoes e pontos por time.
- **MODELO** — documentacao interna de como interpretar tudo.

## Fontes de dados reais (opcional)

O app roda offline com um dataset local sintetico (seed fixo) para testar o
pipeline. Para dados reais, defina as chaves de ambiente:

```powershell
$env:BETGSN_ODDS_API_KEY      = "..."   # the-odds-api.com  (odds multi-casa)
$env:BETGSN_APIFOOTBALL_KEY   = "..."   # api-football.com  (historico, stats, xG)
$env:BETGSN_FOOTBALLDATA_KEY  = "..."   # football-data.org (resultados, tabelas)
```

Confira com `python betgsn.py --providers`.

## Aviso

- O dataset local e **sintetico**. Serve para testar o pipeline, **nao** para
  apostar dinheiro real.
- EV positivo nao garante ganhar a aposta. Garante retorno positivo no longo
  prazo **se** a probabilidade do modelo estiver certa.
- Edge publicado morre: se todos usarem o mesmo modelo, o mercado se ajusta.
- Multiplas longas multiplicam a margem da casa junto com a odd.
- Gestao de banca e metade do jogo. Kelly fracionado existe por um motivo.
