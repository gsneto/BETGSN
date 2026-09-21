# BETGSN — Camada de Odds Multi-Provider

Documento do AGENTE 2 (odds/bookmakers). Descreve os módulos reais, as
decisões e, principalmente, as **limitações**. Nada aqui promete lucro.

---

## 1. Visão geral

O domínio de odds do BETGSN responde a quatro perguntas:

1. **Qual é o preço?** (normalização multi-provider e multi-bookmaker)
2. **O preço é justo?** (implied probability, overround, de-vig, fair odds)
3. **Onde está o melhor preço e ele é comparável?** (line shopping, arbitragem)
4. **O preço está atual e de qual provider?** (health, fallback, snapshots, CLV)

Princípios não negociáveis:

- Nenhum provider é obrigatório. Se todos caírem, a degradação é explícita.
- Nunca inventar dado. Sem cobertura, o estado é `NO_COVERAGE`; sem chave,
  o provider simplesmente não existe na lista.
- Nunca apresentar odd antiga como atual. Dado velho vira `STALE`.
- Nunca comparar snapshots temporalmente incompatíveis sem sinalizar.

---

## 2. Módulos

| Módulo | Papel |
|---|---|
| `betgsn/odds_math.py` | implied probability, overround, de-vig (multiplicative/Shin/power), fair odds, edge |
| `betgsn/odds_normalize.py` | parser ÚNICO de payloads → `NormalizedQuote`; matching e dedupe |
| `betgsn/odds_health.py` | estados de provider, `HealthTracker`, `CreditController`, classificação de falhas |
| `betgsn/line_shopping.py` | melhor preço, consenso, comparabilidade temporal, arbitragem |
| `betgsn/odds_service.py` | orquestração multi-provider com fallback + snapshots |
| `betgsn/providers.py` | HTTP: The Odds API, ParlayAPI, retry limitado, créditos, erros classificados |
| `betgsn/odds_snapshots.py` | persistência append-only, abertura/atual, movimento, staleness, CLV |
| `betgsn/features/odds.py` | features point-in-time + `book_count` / `consensus_limited` |
| `betgsn/features/movement.py` | movimento point-in-time + `book_count` / `is_stale` |
| `betgsn/value_strategy.py` | scanner validado, agora com `book_count` / `consensus_limited` |

---

## 3. Providers

### 3.1 The Odds API (primário)

- Endpoints usados: `/v4/sports/{sport}/odds` (ao vivo) e
  `/v4/historical/sports/{sport}/odds` (histórico).
- Chave: `BETGSN_ODDS_API_KEY`.
- Quota: informada em headers `x-requests-remaining` / `x-requests-used` /
  `x-requests-last`, lidos por `providers.parse_credit_headers`.

### 3.2 ParlayAPI (secundário)

- Chave: `BETGSN_PARLAY_API_KEY`.
- Base: `BETGSN_PARLAY_API_BASE` — **sem default de propósito**. O contrato
  deste provider não é público/verificado; inventar um domínio seria pior
  que não ter provider. O adapter fica inerte sem chave + base.
- Rota: `BETGSN_PARLAY_ODDS_PATH` (default `/odds`).
- O payload é aceito como lista direta ou nos invólucros `data`/`events`/
  `odds`/`results`. Formato desconhecido vira `ProviderError` explícito.

### 3.3 Avaliação de outros providers (não integrados)

| Provider | Situação | Por quê |
|---|---|---|
| Betfair Exchange | não integrado | exige fluxo de autenticação/aplicação própria; avaliar depois |
| Pinnwire | não integrado | chave futura (`PINNWIRE_API_KEY`); sem contrato confirmado aqui |
| SkipOdds | não integrado | sem contrato confirmado; não justificável sem avaliação |
| Outros | não integrado | só com justificativa de cobertura/custo |

Nenhum deles é pré-requisito para o funcionamento global.

### 3.4 `.env` em worktrees de agente

As chaves ficam centralizadas no `.env` do **worktree principal**
(`BETGSN`), que não é copiado para os worktrees (`BETGSN-odds`, etc.).
`providers.env_file_candidates()` procura, em ordem:

1. `BETGSN_ENV_FILE` (override explícito);
2. `.env` do checkout atual;
3. `.env` do worktree principal, descoberto via
   `git rev-parse --git-common-dir`.

O primeiro arquivo que define uma chave vence (o ambiente do sistema sempre
tem prioridade). Nenhuma chave é copiada, impressa ou versionada.

---

## 4. Normalização

Toda cotação vira um `NormalizedQuote`:

`event_id, provider, sport_key, league, home_team, away_team, kickoff,
bookmaker, market, selection, price, timestamp, line`.

- `event_id` **não depende do provider**: é
  `normalize_team(mandante)|normalize_team(visitante)|kickoff_utc`. Isso
  permite juntar o mesmo jogo visto por providers diferentes.
- Mercados mapeados: `h2h` → `Resultado Final (1X2)`, `totals` →
  `Total de Gols`, `btts` → `Ambas Marcam`, `spreads` →
  `Handicap Asiatico`. Mercado desconhecido é **descartado**, nunca
  renomeado para um mercado parecido.
- `providers.odds_event_to_internal` delega para o mesmo parser
  (`odds_normalize.grouped_from_event`), então existe um parser só.
- Dedupe mantém a observação mais recente por
  (evento, casa, mercado, resultado); cotações pós-kickoff são excluídas.

---

## 5. De-vig e fair odds

`odds_math.devig(odds, method=...)` remove a margem de um mercado:

- `multiplicative`: `p_i / Σp`. Estável, é o mesmo método do `engine.py`.
- `shin`: modelo de Shin (1993) com apostadores informados. Costuma dar
  mais probabilidade ao favorito e menos ao longshot que o proporcional.
- `power`: `p_i^k` normalizado, corrige assimetria da margem.

`MarketProbabilities` carrega `overround`, `margin`, `method` e
`complete`. **`complete=False` significa que o mercado não está inteiro**:
normalizar aí é uma aproximação local, não uma probabilidade real.

Odds justas saem por `fair_odds_for` / `fair_decimal_odds`; o edge de um
preço contra o justo sai por `edge_vs_fair`.

---

## 6. Line shopping e comparabilidade

`line_shopping.line_shop(quotes, market, selection, event_id=...)` devolve
`LineShoppingResult` com:

- `best` / `worst` (`PriceOption`: casa, preço, timestamp, provider);
- `median_odd` (consenso robusto), `n_books` (= `book_count`);
- `fair_probability` / `fair_odd` / `edge` (consenso sem margem);
- `timestamp_span_seconds` e `comparable`;
- `warnings`: `CONSENSUS_LIMITED`, `TIMESTAMPS_INCOMPARABLE`,
  `MARKET_INCOMPLETE`.

Regra temporal: se as odds comparadas foram observadas com mais de
`DEFAULT_MAX_TIMESTAMP_SPAN_SECONDS` (900 s) de diferença, `comparable` é
`False`. O "melhor preço" pode ser só defasagem de dado.

Regra dos poucos bookmakers: com menos de `MIN_CONSENSUS_BOOKS` (3) casas,
`consensus_limited=True`. Com 2 casas, `book_count == 2` e o consenso é
marcado como limitado. **Nunca se inventa uma terceira casa.**

---

## 7. Arbitragem

`line_shopping.detect_arbitrage(quotes, min_books=2)`:

- usa a **melhor odd de cada resultado** dentro de um grupo completo de
  mercado (via `engine.market_groups`);
- delega o cálculo de stakes para `engine.scan_arbitrage` (contrato já
  existente) — margem = `1 - Σ(1/odd)`;
- exige `min_books` casas distintas entre as pernas;
- marca `comparable=False` quando as pernas vêm de instantes
  incompatíveis. Arbitragem entre snapshots defasados normalmente não
  existe na prática.

Não há dependência do `parlayapi-arb-scanner`: a lógica usa a matemática
já testada do projeto.

---

## 8. Health, fallback e créditos

Estados (`odds_health.ProviderState`):

| Estado | Significado |
|---|---|
| `HEALTHY` | última coleta funcionou e o dado é recente |
| `DEGRADED` | falha transitória (429/5xx/timeout/rede) |
| `UNAVAILABLE` | falha dura (401/403/sem créditos) ou falhas seguidas |
| `STALE` | há dado, mas velho demais para ser mercado atual |
| `NO_COVERAGE` | provider respondeu, mas não cobre o esporte/evento |

Fluxo de `OddsService.fetch`:

```
provider primário
  → configurado? não: pula
  → health UNAVAILABLE / sem créditos? pula (SKIPPED)
  → chamada com retry LIMITADO (máx. max_attempts_per_provider)
  → resposta vazia: NO_COVERAGE, tenta o próximo
  → resposta com odds: normaliza, deduplica, grava snapshot, devolve
```

- Retry é limitado e respeita `Retry-After` (teto de 30 s). Nunca infinito.
- 401/403/402 **não** são repetidos.
- Se todos falharem, devolve a última coleta conhecida com
  `stale=True` (se velha) e `state=STALE`. `OddsFetch.ok` só é `True`
  quando `state=HEALTHY` e `stale=False`.
- `CreditController` usa o saldo dos headers quando existe; senão, um teto
  local. Nunca estima crédito que não conhece (`None`).

---

## 9. Snapshots, movimento e CLV

`OddsSnapshotStore` (SQLite append-only) agora oferece:

- `observations_at_or_before` (consulta "as of", inclusive);
- `opening_line` / `latest_observation` — mediana entre casas, com
  timestamp e nº de casas;
- `movement` — abertura→atual por mediana entre casas; `status`
  `OK`/`INSUFFICIENT_DATA`/`NO_DATA` (nunca zero onde falta dado);
- `staleness_seconds`;
- `closing_line` (já existente) e `clv` (já existente);
- `clv_prospective(entry_timestamp=...)` — exige que a entrada seja
  **anterior** ao fechamento; caso contrário devolve
  `CLOSING_BEFORE_ENTRY` sem calcular CLV. Isso impede usar informação
  futura para justificar uma decisão de entrada.
- `stats()` inclui a quebra por provider.

---

## 10. MCP — avaliação

Nenhum servidor MCP de odds está configurado neste ambiente (só `pc` e
`playwright`). A avaliação de `odds-api-mcp-server`, `parlay-api-mcp`,
Pinnwire e SkipOdds deve seguir estes critérios, sem inventar contratos:

- autenticação (tipo de credencial, rotação);
- custo e limites (créditos, rate limit, plano);
- cobertura (esportes, bookmakers, mercados, histórico);
- estabilidade (uptime, formato de erro, versionamento);
- ferramentas expostas (busca de odds, histórico, quota).

Princípio arquitetural: **MCP é camada de acesso/orquestração**. A lógica
quantitativa crítica (de-vig, fair odds, line shopping, arbitragem, CLV)
fica no processo, nos módulos acima. Se o MCP falhar ou não existir, os
adapters HTTP normais continuam funcionando — e o `OddsService` não
depende de MCP.

---

## 11. Testes

- `tests/test_odds_layer.py` — math, normalização, providers (429/403/
  timeout/retry), health, créditos, line shopping, arbitragem, fallback,
  stale, snapshots.
- `tests/test_odds_snapshots.py` — as-of, abertura/atual, movimento,
  staleness, CLV prospectivo, stats por provider.
- `tests/test_odds_movement.py` — book count, staleness, features de odds.

Rodar:

```
python -m pytest tests/test_odds_layer.py tests/test_odds_snapshots.py tests/test_odds_movement.py
```

---

## 12. Limitações honestas

- O adapter ParlayAPI é **configurável e não verificado**: só é ativado
  com base explícita e o schema deve ser confirmado antes de uso real.
- Shin e power são **modelos** de remoção de margem; nenhum deles
  descobre a probabilidade verdadeira.
- Arbitragem detectada entre snapshots defasados é marcada como
  incomparável e normalmente não é executável.
- CLV exige fechamento dentro da janela (`CLOSING_WINDOW_MINUTES`); sem
  isso o resultado é `NO_CLOSING_ODDS`, nunca um número inventado.
- Com 1 ou 2 bookmakers o consenso é marcado como limitado; a estratégia
  validada (`value_strategy`) continua exigindo `MIN_BOOKS = 3`.
