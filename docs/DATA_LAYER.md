# BETGSN — Camada de Dados Multi-Fonte (`betgsn/datalayer`)

Documento da **AGENTE 1 — DATA**. Descreve a camada que elimina a
dependência de uma única fonte de fixtures/resultados, com correção
temporal, proveniência e degradação controlada.

Regra central: **nenhum dado é fabricado**. Ausência é um estado explícito
e datado (`MISSING` / `NO_COVERAGE` / `STALE`), nunca um zero silencioso.

---

## 1. Fontes

| Fonte | Módulo | Chave | Fixtures | Resultados | Estatísticas | xG | Odds |
|---|---|---|:---:|:---:|:---:|:---:|:---:|
| football-data.co.uk | `football_data_uk` | não | sim | sim | sim | **não** | sim (abertura/fechamento) |
| API-Football | `providers.ApiFootballProvider` | `BETGSN_APIFOOTBALL_KEY` | sim | sim | sim | sim (quando a liga publica) | não |
| Football-Data.org | `providers.FootballDataProvider` | `BETGSN_FOOTBALLDATA_KEY` | sim | sim | não | não | não |

- **Prioridade** é por registro (`priority`, menor = melhor) e é combinada
  com a saúde observada da fonte (`HEALTHY` antes de `DEGRADED`).
- **xG** nunca é derivado de chutes. Sem `expected_goals`, o resultado é
  `UNAVAILABLE` com motivo (`datalayer.xg`).
- Odds de bookmakers **não** são implementadas aqui (domínio do Agente 2).

## 2. Fallback e estados de falha

Cada tentativa de fonte passa por `datalayer.errors`, que classifica a
falha e decide se há retry:

| Situação | `ErrorKind` | Retentável | Efeito |
|---|---|:---:|---|
| 401 | `AUTH` | não | fallback; cooldown 1h |
| 403 | `FORBIDDEN` | não | fallback; cooldown 1h |
| 404 | `NOT_FOUND` | não | fallback |
| 429 | `RATE_LIMIT` | **sim** (limitado) | backoff exponencial; cooldown 60s |
| timeout | `TIMEOUT` | **sim** | backoff exponencial |
| conexão caiu | `CONNECTION` | **sim** | backoff exponencial |
| 5xx | `SERVER` | **sim** | backoff exponencial; cooldown 30s |
| quota esgotada | `QUOTA` | não | fallback; cooldown 1h |
| sem cobertura | `NO_COVERAGE` | não | fallback; não conta como falha |
| resposta vazia/incompleta | `INCOMPLETE` | não | fallback |

`RetryPolicy.max_attempts` é **sempre finito** (padrão 2). Nunca há retry
infinito.

Estados de saúde (`datalayer.health.ProviderStatus`):

`HEALTHY`, `DEGRADED` (já falhou), `UNAVAILABLE` (falhou além do limiar),
`STALE` (só há dado antigo), `NO_COVERAGE` (não cobre aquele dado),
`UNKNOWN`.

## 3. Envelope e proveniência

Todo resultado é um `DataEnvelope`:

```
status:     OK | STALE | DEGRADED | NO_COVERAGE | MISSING | ERROR
value:      lista de registros (vazia ≠ None)
provenance: source, fetched_at, source_timestamp, data_age_seconds,
            from_cache, quality, coverage, note
errors:     motivos das fontes que falharam
alternatives: proveniência das fontes que falharam (auditoria)
```

Dado stale nunca é apresentado como atual: `status=STALE` e
`data_age_seconds`/`data_age_human` sempre preenchidos (ou explicitamente
`idade desconhecida` quando a fonte não fornece carimbo).

### Ponte canônica

`datalayer.canonical_bridge` converte os registros das fontes em
`canonical.CanonicalMatch`/`CanonicalOdds`/`CanonicalXG`, preservando
proveniência. Odds sem carimbo de observação ficam com `timestamp=""`
(nunca o kickoff), para não serem tratadas como disponíveis
point-in-time.

## 4. Cache e TTL

Cache em disco via `betgsn.cache.DiskCache` (JSON por namespace), com TTL
por tipo de dado (`datalayer.source.TTL_BY_KIND`):

| Tipo | TTL |
|---|---|
| `fixtures` | 6h |
| `results` | infinito |
| `stats` | 12h |
| `xg` | infinito (histórico não muda) |
| `odds` | 15min |
| `injuries` | 6h |
| `lineups` | 30min |
| `h2h` | 24h |

Ordem de resolução: **cache fresco → fontes → cache stale explícito →
`MISSING`**. Um TTL pode ser sobrescrito por registro (`ttl=`) ou por
chamada (`fetch(..., ttl=...)`).

## 5. Quota e rate limit

- `betgsn.quota.QuotaManager.try_consume` reserva a requisição de forma
  atômica; quota esgotada degrada para a próxima fonte em vez de insistir.
- `datalayer.ratelimit.RateLimiter` impõe espaçamento mínimo e teto por
  minuto, complementando a quota.
- `sleep` e relógio são injetáveis: os testes nunca dormem de verdade.

## 6. Entity matching

`datalayer.entity.EntityRegistry` normaliza times/competições/países e
resolve por **chave exata** (nome canônico ou alias). Regras:

- mesma chave para duas entidades → `AMBIGUOUS` (nunca escolhe no chute);
- sem chave → `UNKNOWN` (nunca inventa correspondência);
- `resolve_fuzzy` é opt-in, com limiar alto (0.92) e exige vencedor
  **único**; empate continua `AMBIGUOUS`;
- IDs externos (`api_football`, `football_data_org`, ...) são mapeados
  explicitamente.

A normalização reutiliza `backtest_sources.normalize_team` — uma única
regra de nomes no projeto.

## 7. Point-in-time

`datalayer.pointintime` garante que o backtest nunca receba informação
futura:

- `available_at(record, kind="result")` usa o carimbo de publicação ou o
  embargo conservador de 48h (`temporal.result_time`);
- `filter_available_before` exclui tudo posterior ao corte;
- `assert_no_future` levanta `PointInTimeError` em vazamento;
- `guard_envelope` recusa envelope cuja fonte seja posterior ao corte.

## 8. Cobertura

`datalayer.coverage.build_coverage` responde "quem fornece o quê" e
"quais buracos ficam sem fonte". `NO_COVERAGE` é um fato de capacidade —
não entra no histórico de falhas.

## 9. Configuração e credenciais

As chaves vivem **somente** no `.env` da raiz (ou no ambiente do sistema),
que tem precedência. Nunca entram no Git (`output/` e `.env` são
ignorados). `.env.example` documenta as variáveis com valores vazios.

`betgsn/envconfig.py` localiza o `.env` também quando o código roda de um
**linked worktree** (ex.: `BETGSN-data`): procura no worktree atual, nos
diretórios pais e, por último, na raiz do worktree principal do Git
(descoberta pelo ponteiro `.git` + `commondir`). Isso evita duplicar
credenciais entre worktrees. Nenhuma chave é copiada, impressa ou gravada.

Variáveis relevantes para esta camada:

```
BETGSN_APIFOOTBALL_KEY     # API-Football
BETGSN_FOOTBALLDATA_KEY    # Football-Data.org
BETGSN_ENV_FILE            # opcional: caminho explícito do .env
```

## 10. Uso

```python
from betgsn.datalayer import (
    MultiSourceLayer, FootballDataUkSource, ApiFootballSource,
    FootballDataOrgSource, Capabilities,
)

layer = MultiSourceLayer()
layer.register(FootballDataUkSource(), priority=1, supports_cache=False)
layer.register(ApiFootballSource.from_env(), priority=2)
layer.register(FootballDataOrgSource.from_env(), priority=3)

env = layer.fetch(Capabilities.FIXTURES)
if env.available:
    for fixture in env.value:
        ...
print(env.status.value, env.provenance.source, env.provenance.age_human)

report = layer.coverage_report()
print(report.summary())
print(layer.health_snapshot())
```

## 11. Limitações conhecidas (documentadas, não mascaradas)

- **API-Football** não fornece timestamp de publicação por observação; a
  idade é a do `fetched_at`. Isso está explícito na proveniência.
- **Football-Data.org** não tem odds, xG, cantos ou cartões — coberto
  apenas como fallback de fixtures/resultados.
- **football-data.co.uk** não publica xG; o campo fica ausente.
- O cache serializa registros em JSON. Fontes que devolvem dataclasses são
  registradas com `supports_cache=False` (o CSV já tem cache próprio).
- O `MultiSourceLayer` não decide política de negócio (qual mercado, qual
  EV); ele entrega dados com proveniência. A decisão é do consumidor.

## 12. Testes

`tests/test_data_layer.py` cobre: classificação de erros (401/403/404/
429/5xx/timeout/conexão/quota), fallback, retry limitado, resposta
incompleta, indisponibilidade, quota, rate limit, cache/TTL/stale,
health, entity matching (ambíguo/desconhecido/fuzzy), point-in-time,
ausência de xG, cobertura, qualidade e resolução do `.env` em worktree.
Nenhum teste toca a rede.
