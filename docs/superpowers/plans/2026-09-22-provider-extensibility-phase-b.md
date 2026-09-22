# FASE B — Provider/Odds Extensibility: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a camada de providers/odds extensível (contrato + registry + adapters + integração operacional) sem quebrar nenhum contrato existente, seguindo strangler migration.

**Architecture:** Novo contrato `OddsProvider` (`betgsn/odds_provider.py`) com `fetch_odds(OddsFetchRequest) -> OddsProviderFetch` produzindo `list[NormalizedQuote]`. Registry por factory (`betgsn/odds_registry.py`). Adapters existentes (`OddsApiProvider`, `ParlayApiProvider`) implementam o contrato mantendo o caminho duck legado (`live_odds_with_meta`). Captura operacional aceita múltiplos providers com colisões físicas contabilizadas explicitamente no `CaptureReport` (schema do banco NÃO muda).

**Tech Stack:** Python 3.10+ stdlib (dataclasses, typing.Protocol, sqlite3), pytest. Sem novas dependências.

**Spec:** Plano arquitetural da Fase B (entregue na sessão de planejamento, embutido abaixo) + prompt de execução do usuário (decisões já tomadas listadas em Global Constraints).

## Global Constraints (binding — violar qualquer uma reprova a fase)

Copiadas do spec do usuário — valores exatos:

1. NÃO criar schema v4 do banco. `UNIQUE(match_key, market, outcome, bookmaker, timestamp)` permanece SEM provider. Colisões físicas cross-provider devem ser contabilizadas explicitamente no CaptureReport com: provider, bookmaker, match, market, outcome, timestamp, action ∈ {"ignored", "deduped", "collision"}. Não mascarar.
2. `OddsSnapshotStore` (betgsn/odds_snapshots.py): NÃO alterar API nem schema. `NormalizedQuote` (betgsn/odds_normalize.py): NÃO alterar contrato (campo `sport_key` permanece).
3. NÃO alterar semântica de: movement, CLV, odds_math, line_shopping, BetDecision, strategy_runner, promotion, robustness, portfolio, staking, strategy.
4. NÃO instanciar dados: sem odds/timestamps/créditos/cobertura/eventos inventados. Ausência = `None`/lista vazia/estado explícito (NO_COVERAGE), nunca valor conveniente. Créditos desconhecidos = `None`.
5. Preservar: NO BET; provider names/labels existentes ("The Odds API", "ParlayAPI", "the-odds-api", "the-odds-api-live", `LIVE_ODDS_PROVIDER`); ordem de fallback ("The Odds API" antes de "ParlayAPI"); `available_providers()`; CLI flags; API schemas; vocabulário de status (HEALTHY/DEGRADED/UNAVAILABLE/STALE/NO_COVERAGE; NO_DATA/INSUFFICIENT_DATA/OK; OK/NO_CLOSING_ODDS/CLOSING_BEFORE_ENTRY).
6. NÃO criar SportRegistry, abstração de e-sports, sistema genérico de capabilities, nem migrar o datalayer (permanece orphaned).
7. NÃO corrigir código morto/bugs não relacionados (ODDS_API_MARKET_MAP morto, FixtureMatchIndex.to_dict quebrado, roundtrip de créditos daily_limit/exhausted) — registrar como follow-up no relatório.
8. FixtureMatchIndex continua o único responsável pela identidade provider→canônica. Sem fuzzy matching. Ambiguidade rejeitada.
9. Testes existentes NÃO devem ser editados para caber na refatoração. Fakes legado (`live_odds_with_meta`) devem continuar funcionando (strangler: caminho legado coexiste).
10. Commits apenas na branch `agent/provider-extensibility`. Sem merge/rebase/push.
11. Interprete de teste: `.\.venv\Scripts\python.exe -m pytest` (venv do projeto). Frontend: `npm test` (= `vitest run`) e `npm run build` (`tsc -b && vite build`) em `web/`.
12. Idioma do código: comentários/docstrings em português (padrão do repositório), sem emojis, sem comentários desnecessários. Estilo: seguir o arquivo onde se edita.
13. Providers/quotes usam labels de mercado INTERNOS ("Resultado Final (1X2)", "Total de Gols", "Ambas Marcam", "Handicap Asiatico" — ver `MARKET_MAP`/`CANONICAL_MARKETS` em odds_normalize.py:36-44). O contrato request/response usa labels internos; a tradução para chaves do provider ("h2h" etc.) é responsabilidade do adapter.
14. Zero quotes + estado explícito = cobertura ausente. Um provider sem determinado mercado NÃO gera dados falsos.

## Review Focus (modos de falência a cobrir com testes, mais prováveis primeiro)

1. **Créditos `None` vs 0:** provider sem headers de crédito deve resultar `remaining=None`, nunca 0 (0 significaria "esgotado"). Teste: fake sem headers → credits ausentes no fetch e `None` no relatório/health.
2. **Timestamp inventado:** provider que retorna evento sem `commence_time`/kickoff → zero quotes (nunca kickoff fabricado); `fetched_at` do request é o único timestamp permitido como fallback de observação (comportamento atual de `normalize_event`). Teste: evento sem kickoff → 0 quotes.
3. **Colisão não detectada:** dois providers, mesma (match_key, market, outcome, bookmaker, timestamp) → segunda ocorrência classificada "collision" com kept_by, e `observations_saved` refletindo apenas a primeira. Teste: A+B com quote idêntica → report tem 1 collision, store tem 1 linha.
4. **Falha de A contamina B:** provider A levanta `ProviderError` durante captura multi-provider → B ainda persiste 100% das observações; health de A = UNAVAILABLE/DEGRADED, health de B = HEALTHY; store intacto para A (zero observações de A).
5. **Ordem de fallback quebrada:** registry deve produzir exatamente ("The Odds API", "ParlayAPI") quando ambos configurados, e apenas um quando só um tem chave. Teste de compatibilidade de ordem pinado.

---

## Contexto arquitetural (verificado — referências exatas)

Fluxo operacional atual: `OddsApiProvider` (providers.py:462, duck `live_odds_with_meta(sport_key, regions, markets) -> (events, headers)` :484-504) → `LiveOddsCapture.capture` (backtest_sources.py:825; writer operacional, tipado `provider: OddsApiProvider` :795-797) → `normalize_events` (odds_normalize.py:340) → `list[NormalizedQuote]` → `dedupe_quotes` (:408) → `FixtureMatchIndex.resolve` com scope `SPORT_KEY_TO_DIVISIONS` (backtest_sources.py:974) → `observations_from_quotes` (odds_snapshots.py:79) → `OddsSnapshotStore.add` (odds_snapshots.py:351, SQLite append-only `INSERT OR IGNORE`, UNIQUE(match_key, market, outcome, bookmaker, timestamp) :299) → movement (:651) / CLV (:871, :904) → API → quant.

Pontos-chave:
- `OddsService` (odds_service.py:116): fallback multi-provider com gates HealthTracker/CreditController (:181-190), cache degradado (:282-308), `_call` duck-typed (:258-270 com fallback TypeError). Em produção só serve health/credits (api/service.py:242-247); dados reais vão direto ao `OddsApiProvider`.
- `configured_odds_providers()` (providers.py:752) ordena por `ODDS_PROVIDER_PRIORITY = ("The Odds API", "ParlayAPI")` (:749). `available_providers()` (:737) = dict de 4 nomes fixos.
- `ParlayApiProvider` (providers.py:596) espelha o contrato Odds API ("no mesmo contrato da The Odds API" :635).
- Vazamentos a corrigir (dentro do escopo): parser Odds API `iter_event_quotes` (odds_normalize.py:284-319) — permanece, mas vira detalhe interno da família de adapters Odds API; headers `x-requests-*` lidos fora do adapter (backtest_sources.py:881-883 — será removido na Task 5; value_strategy.py:539-540 — Task 4); retry regex de mercados (backtest_sources.py:1062-1072 `_UNSUPPORTED_RE` — move para o adapter na Task 5); custo genérico `estimated_request_cost` (odds_health.py:304-310 — delega ao provider na Task 4); scanner instancia `OddsApiProvider` e parseia eventos crus (value_strategy.py:451-458, 513-543 — Task 4); `_PROVIDER_FEATURES` hardcoded (api/service.py:111-116 — Task 4).
- `_apply_credits` (odds_service.py:272-280): header presente → `update_from_headers`; senão `record_spend(cost)`.
- `LiveOddsCapture._persist_observations` (backtest_sources.py:931-997): normalize → dedupe → resolve → `observations_from_quotes(match_keys=...)` → `store.add`.
- `CaptureReport` (backtest_sources.py:745-772): campos atuais preserved; aditivo apenas.
- `LIVE_ODDS_PROVIDER = "The Odds API"` (backtest_sources.py:60); snapshot label `"the-odds-api-live"` (:909, pinado por test_backtest_sources.py:797).
- Market map: `MARKET_MAP` odds_normalize.py:36-41 (`h2h→Resultado Final (1X2)`, `totals→Total de Gols`, `btts→Ambas Marcam`, `spreads→Handicap Asiatico`).
- Catálogo divisões: `SPORT_KEY_TO_DIVISIONS` / `DIVISION_TO_SPORT_KEY` (providers.py:406-459) — conhecimento da família Odds API, deve residir no adapter (providers.py), não no core de captura.
- `FixtureMatchIndex` (odds_normalize.py:143-231): resolve por divisão+alias+nomes normalizados+kickoff UTC exato; MATCHED/AMBIGUOUS/UNKNOWN.
- Store: `add` retorna int (linhas efetivamente inseridas); `all_observations(match_key)` (:518) permite pré-checar existência sem mudar a API.
- Testes que protegem os contratos: test_odds_layer.py (67), test_odds_snapshots.py (48), test_odds_movement.py (47), test_odds_identity_integration.py (9), test_operational_pipeline.py (25), test_provider_health_api.py (16), test_odds_status_contracts.py (8), test_backtest_sources.py (42), test_clv_prospective_real.py (22), test_coverage_semantics.py (14), test_validation_cache.py (scanner), test_strategy_extensibility.py (padrão FASE A a seguir).

---

### Task 1: B.1 — Provider Contract (`betgsn/odds_provider.py`)

**Files:**
- Create: `betgsn/odds_provider.py`
- Test: `tests/test_odds_provider_contract.py`

**Interfaces (produz — Tasks 2-5 dependem disto, verbatim):**

```python
# betgsn/odds_provider.py
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence, runtime_checkable

@dataclass(frozen=True)
class OddsFetchRequest:
    """Pedido provider-agnostic de odds. Escopo canônico por divisões."""
    divisions: tuple[str, ...] = ()          # códigos canônicos de divisão (ex.: "E0")
    markets: tuple[str, ...] = ()            # labels internos ("Resultado Final (1X2)", ...)
    regions: Optional[str] = None            # conceito opcional; adapter ignora se não usa
    fetched_at: str = ""                     # instante da observação (relógio injetado pelo chamador)

@dataclass(frozen=True)
class CreditUpdate:
    """Créditos observados em headers do provider. None = desconhecido."""
    last: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None

@dataclass(frozen=True)
class OddsProviderFetch:
    """Resultado de um fetch. Quotes já normalizadas (NormalizedQuote)."""
    quotes: tuple = ()                        # tuple[NormalizedQuote, ...]
    raw_events: tuple = ()                    # opcional; dicts crus p/ snapshot de backtest (shape opaco ao core)
    snapshot_provider: str = ""               # label p/ OddsSnapshot.provider (ex.: "the-odds-api-live"); vazio = não salva snapshot
    credits: Optional[CreditUpdate] = None
    no_coverage: bool = False                 # True = provider respondeu sem cobertura p/ o escopo
    errors: tuple[str, ...] = ()

@runtime_checkable
class OddsProvider(Protocol):
    name: str
    def available(self) -> bool: ...
    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch: ...

def estimated_cost(provider, request: OddsFetchRequest) -> int:
    """Custo estimado em créditos. Default 1; provider pode expor
    `estimated_cost(request)` próprio. Nunca inventa crédito real."""
    fn = getattr(provider, "estimated_cost", None)
    if callable(fn):
        try:
            return max(1, int(fn(request)))
        except Exception:
            return 1
    return 1

def divisions_for(provider, scope: str) -> tuple[str, ...]:
    """Divisões canônicas cobertas por um escopo (ex.: sport key).
    Responsabilidade do adapter; () = não sabe (chamador decide o que fazer)."""
    fn = getattr(provider, "divisions_for", None)
    if callable(fn):
        return tuple(fn(scope))
    return ()
```

`ProviderError` existente (providers.py:95) é a exceção do contrato — NÃO redefinir, importar de `.providers` (ou evitar import circular duplicando a referência via TYPE_CHECKING + import tardio; verifique: `providers.py` não importa `odds_provider`, então `from .providers import ProviderError` no topo é seguro).

**Passos:**

- [ ] **Step 1: Escrever os testes de contrato** em `tests/test_odds_provider_contract.py`. Suíte de conformance REUTILIZÁVEL `run_contract_conformance(provider, *, expect_name, ...)` + testes com fakes locais (dataclasses simples no arquivo de teste). Casos obrigatórios:
  1. Provider válido: `available()` True; `fetch_odds` retorna `OddsProviderFetch`; toda quote é `NormalizedQuote`; `quote.provider == provider.name`; `quote.market` ∈ `CANONICAL_MARKETS` (import de odds_normalize); `quote.price > 1.0`; `quote.timestamp` não vazio; `quote.kickoff` não vazio.
  2. Provider indisponível: `available()` False; `fetch_odds` levanta `ProviderError` (contrato: chamador não deve chamar, mas se chubar deve falhar alto, nunca retornar vazio silencioso).
  3. Zero coverage: fetch retorna `quotes == ()` e `no_coverage is True` — nunca dados sintéticos; sem `errors` vazios disfarçando (no_coverage explícito).
  4. Provider error: `fetch_odds` levanta `ProviderError` (status/kind preservados).
  5. Créditos desconhecidos: `credits is None` quando provider não informa — nunca `CreditUpdate(0,0,0)`.
  6. `fetched_at` injetado: quotes sem timestamp próprio do evento recebem `timestamp == request.fetched_at` (comportamento de `normalize_event`); nenhuma quote com timestamp fora de {timestamp do provider, fetched_at}.
  7. Sem fabricação de timestamp: evento sem kickoff → zero quotes (conformance de um fake que descarta evento sem kickoff; no contrato: adapter NUNCA fabrica kickoff — testado via fake que espelha a regra).
  8. `estimated_cost` helper: default 1; provider com método próprio retorna o seu; método que explode → 1 (nunca propaga).
  9. `divisions_for` helper: default (); provider com método retorna tupla.
  10. `OddsProvider` é `runtime_checkable`: fake com name/available/fetch_odds passa em `isinstance`; objeto sem `fetch_odds` não passa.
- [ ] **Step 2: Rodar** `.\.venv\Scripts\python.exe -m pytest tests/test_odds_provider_contract.py -q` — esperar FAIL (módulo não existe).
- [ ] **Step 3: Implementar** `betgsn/odds_provider.py` exatamente conforme Interfaces acima (docstrings em português explicando responsabilidade e o que NÃO carrega: staking/strategy/promotion/CLV/portfolio/quant).
- [ ] **Step 4: Rodar** `.\.venv\Scripts\python.exe -m pytest tests/test_odds_provider_contract.py -q` — PASS.
- [ ] **Step 5: Suíte backend completa:** `.\.venv\Scripts\python.exe -m pytest -q` — 0 falhas (nenhum arquivo existente tocado; se algo falhar, é regressão environmental ou problema real — investigar com systematic-debugging, não ajustar teste).
- [ ] **Step 6: Self-review do diff** (`git diff` + `git status`) — apenas 2 arquivos novos.
- [ ] **Step 7: Commit:** `git add betgsn/odds_provider.py tests/test_odds_provider_contract.py && git commit -m "feat: add provider odds contract"`

---

### Task 2: B.2 — Provider Registry (`betgsn/odds_registry.py`)

**Files:**
- Create: `betgsn/odds_registry.py`
- Modify: `betgsn/providers.py` (apenas `configured_odds_providers` :752-766 e o uso de `ODDS_PROVIDER_PRIORITY` :749 — mantenha a constante exportada se testes/código a referenciam; verifique com `rg "ODDS_PROVIDER_PRIORITY"`)
- Modify: `betgsn/odds_service.py` (apenas `from_env` :143-148)
- Test: `tests/test_odds_registry.py`

**Interfaces (consome da Task 1: `OddsProvider` de odds_provider.py; produz para Tasks 3-5):**

```python
# betgsn/odds_registry.py
@dataclass(frozen=True)
class ProviderSpec:
    name: str                    # label estável ("The Odds API", "ParlayAPI")
    factory: Callable[[], object]  # retorna provider OU None quando não configurado
    priority: int = 100          # menor = primeiro
    features: tuple[str, ...] = ()  # metadata declarativa p/ API (ex.: ("odds", "live"))

class OddsProviderRegistry:
    def register(self, spec: ProviderSpec) -> None: ...      # ValueError se nome duplicado
    def specs(self) -> list[ProviderSpec]: ...                # ordenado por (priority, nome)
    def names(self) -> list[str]: ...
    def metadata(self, name: str) -> ProviderSpec: ...        # KeyError se ausente
    def lookup(self, name: str): ...                          # provider construído via factory (cache por nome); None se factory retornou None; KeyError se nome desconhecido
    def available_providers(self) -> list[tuple[str, object]]: ...  # [(name, provider)] só configurados, em ordem de prioridade

def default_odds_registry() -> OddsProviderRegistry:
    """Registry padrão: The Odds API (priority 1), ParlayAPI (priority 2).
    Factories: OddsApiProvider.from_env / ParlayApiProvider.from_env (retornam None sem chave)."""
```

Cache de `lookup`: construir uma vez por nome por instância de registry (factory pode ser chamada N vezes senão). Registry novo por teste (padrão `_fresh_registry` da FASE A, tests/test_strategy_extensibility.py:112-115).

**Mudanças em providers.py:** `configured_odds_providers()` passa a delegar ao `default_odds_registry().available_providers()`, preservando EXATAMENTE: nomes ("The Odds API", "ParlayAPI"), ordem, e o retorno `list[tuple[str, object]]`. Import de odds_registry no topo de providers.py — verifique ciclo: odds_registry importa providers APENAS dentro de `default_odds_registry()` (import tardio) para evitar circularidade (providers → odds_registry → providers). Se `ODDS_PROVIDER_PRIORITY` for referenciado por testes, mantenha-o definido em providers.py como referência/compat.

**Mudança em odds_service.py:** `from_env` (:143-148) passa a montar providers via `default_odds_registry().available_providers()` (equivalente funcional ao `configured_odds_providers()` atual — pode simplesmente continuar chamando `configured_odds_providers`, que agora delega; escolha a forma que toque menos código).

**Passos:**

- [ ] **Step 1: Testes** `tests/test_odds_registry.py`: registry vazio (`names() == []`, `available_providers() == []`); registro válido + `specs()` ordenado por prioridade; duplicate name → `ValueError`; factory retornando None → provider ausente de `available_providers()` mas `lookup(name) is None`; `lookup` constrói e cacheia (factory chamada 1x); `metadata`/`lookup` de nome desconhecido → `KeyError`; `available_providers` preserva ordem de prioridade; teste de compatibilidade de ordem: com ambas as env keys setadas (monkeypatch env `BETGSN_ODDS_API_KEY` e `BETGSN_PARLAY_API_KEY`+`BETGSN_PARLAY_API_BASE`), `default_odds_registry().available_providers()` retorna `[("The Odds API", <OddsApiProvider>), ("ParlayAPI", <ParlayApiProvider>)]` nessa ordem, e `configured_odds_providers()` (import de providers) retorna a mesma lista — pin de compatibilidade.
- [ ] **Step 2: Rodar** — FAIL.
- [ ] **Step 3: Implementar** odds_registry.py + modificar providers.py/odds_service.py conforme acima.
- [ ] **Step 4: Rodar** testes novos — PASS.
- [ ] **Step 5: Suíte completa** `.\.venv\Scripts\python.exe -m pytest -q` — 0 falhas (test_odds_layer.py:381-387 e demais pinam o comportamento atual).
- [ ] **Step 6: Diff review + commit:** `git commit -m "feat: add odds provider registry"`

---

### Task 3: B.3 — Adapters dos providers existentes

**Files:**
- Modify: `betgsn/providers.py` — `OddsApiProvider` e `ParlayApiProvider` ganham `fetch_odds`, `available`, `estimated_cost`, `divisions_for`; lógica de mercado não suportado (retry) entra no adapter
- Modify: `betgsn/odds_service.py` — `_call`/`fetch` usam `fetch_odds` quando disponível (hasattr), mantendo caminho legado duck para fakes
- Test: `tests/test_odds_provider_adapters.py`

**Interfaces (consome: OddsFetchRequest/OddsProviderFetch/CreditUpdate de odds_provider.py; NormalizedQuote de odds_normalize.py):**

`OddsApiProvider.fetch_odds(request) -> OddsProviderFetch`:
- `request.divisions` → sport keys via `DIVISION_TO_SPORT_KEY` (providers.py:406+); divisão sem sport key → ignorada (sem erro); sem divisões mapeadas → `OddsProviderFetch(no_coverage=True)`... CUIDADO: se request.divisions vazio, sem informação — trate como no_coverage.
- `request.markets` (labels internos) → chaves Odds API via reverse de `MARKET_MAP` (odds_normalize.py:36-41: "Resultado Final (1X2)"→"h2h", "Total de Gols"→"totals", "Ambas Marcam"→"btts", "Handicap Asiatico"→"spreads"); label sem mapeamento → ignorada. Se `request.markets` vazio → default do provider (`h2h,totals,btts` interno-equivalente).
- Para cada sport key: `self.live_odds_with_meta(sport_key, regions=request.regions or self.regions, markets=",".join(api_keys))`; merge dos eventos; normalize via `odds_normalize.normalize_events(events, self.name, request.fetched_at, sport_key=<do evento>, league=None)` — verifique a assinatura exata em odds_normalize.py:334 (`normalize_event(event, provider, fetched_at, sport_key, league)`; `normalize_events` :340). `quote.provider` DEVE ser `self.name` ("The Odds API").
- `snapshot_provider = "the-odds-api-live"`, `raw_events = tuple(events)`.
- Créditos: `parse_credit_headers(headers)` (providers.py:360-378) → `CreditUpdate` (None se vazio).
- Retry de mercados não suportados: mover a lógica de `LiveOddsCapture._fetch_with_fallback`/`_UNSUPPORTED_RE` (backtest_sources.py:999-1035, 1062-1072) para DENTRO de `fetch_odds` (método privado `_fetch_dropping_unsupported`). A captura legada atual continua funcionando na Task 5 (não remova ainda nada lá — Task 5 decide). NÃO remova `_fetch_with_fallback` de backtest_sources nesta task (Task 5 cuida); apenas adicione a capacidade no adapter (duplicação temporária aceitável no strangler — ou extraia helper compartilhado em providers.py e faça backtest_sources delegar; prefira helper compartilhado em providers.py se não quebrar imports).
- `estimated_cost(request)`: modelo atual `estimated_request_cost(n_markets, n_regions)` (odds_health.py:304-310) = nº de mercados × nº de regiões (verifique implementação exata) por sport key; total = soma. Delegar o cálculo a partir dos sport keys resolvidos.
- `divisions_for(scope)`: `SPORT_KEY_TO_DIVISIONS.get(scope, ())`.
- `available()`: `True` (instância só existe se `from_env` retornou algo com chave; verifique se faz sentido também aceitar instância construída manualmente — sempre True).

`ParlayApiProvider.fetch_odds`: mesma família de formato — implementar por composição/delegação à mesma lógica (helper compartilhado `_fetch_odds_api_format(provider, request, name, snapshot_label)` em providers.py), com `snapshot_provider = "parlayapi-live"`... VERIFIQUE: não existe hoje snapshot de Parlay; label novo livre, mas `quote.provider == "ParlayAPI"`. `divisions_for`: mesmo catálogo (ParlayAPI usa sport keys no mesmo formato — verifique `_url`/docstring providers.py:596-641).

`odds_service.py` (`fetch` :169-254, `_call` :258-270): se `hasattr(provider, "fetch_odds")`: chamar `provider.fetch_odds(OddsFetchRequest(divisions=..., markets=<labels internos>, regions=regions, fetched_at=fetched_at))`. PROBLEMA: `OddsService.fetch(sport_key, markets="h2h", regions)` recebe sport_key e markets em formato Odds API — parâmetros públicos que DEVEM permanecer compatíveis (invariantes). Solução strangler: `fetch` mantém a assinatura atual; internamente, para providers com `fetch_odds`: `divisions_for(provider, sport_key)` → `OddsFetchRequest(divisions=divs, markets=<labels internos traduzidos de markets via MARKET_MAP>, regions=regions, fetched_at=fetched_at)`; usa `result.quotes` diretamente (pula normalize_events) e aplica créditos de `result.credits` quando presente (novo método aditivo `CreditController.apply_update(name, update)` em odds_health.py — apenas se update não-None). Providers legado (sem fetch_odds): caminho atual intocado. `OddsFetch.quotes` e todo o shape de resultado permanecem idênticos.

**Passos:**

- [ ] **Step 1: Testes** `tests/test_odds_provider_adapters.py`:
  1. `OddsApiProvider.fetch_odds` com monkeypatch de `live_odds_with_meta` retornando evento Odds API real-shape (copia fixture de tests/test_odds_identity_integration.py:48-60): retorna quotes NormalizedQuote com `provider == "The Odds API"`, `snapshot_provider == "the-odds-api-live"`, `raw_events` == eventos; quotes idênticas às de `normalize_events` (paridade).
  2. Divisões → sport keys: request com divisão mapeada chama `live_odds_with_meta` com o sport key certo; divisão não mapeada não gera chamada nem erro.
  3. Markets: request com labels internos converte para chaves Odds API no parâmetro `markets`.
  4. Créditos: headers com `x-requests-remaining` → `CreditUpdate`; sem headers → `credits is None`.
  5. `estimated_cost`/`divisions_for` do adapter.
  6. `ParlayApiProvider.fetch_odds`: mesmo conformance (provider "ParlayAPI").
  7. Conformance da Task 1 (`run_contract_conformance`) rodando contra os DOIS adapters reais (com monkeypatch do HTTP).
  8. Compatibilidade: `OddsService.fetch` com provider novo (fetch_odds) retorna `OddsFetch` com quotes e `ok=True`; com fake legado (`live_odds_with_meta`) — caminho atual, teste existente já cobre; adicione um teste comparando paridade de quotes entre os dois caminhos.
  9. Unsupported-market retry: `live_odds_with_meta` que levanta ProviderError com mensagem "Markets not supported by this endpoint: totals" → `fetch_odds` tenta sem "totals" e retorna quotes do mercado suportado.
- [ ] **Step 2: Rodar** — FAIL.
- [ ] **Step 3: Implementar** conforme acima.
- [ ] **Step 4: Testes novos** — PASS.
- [ ] **Step 5: Suíte completa** — 0 falhas.
- [ ] **Step 6: Diff review + commit:** `git commit -m "refactor: adapt odds providers to provider contract"`

---

### Task 4: B.4 — Health / Quota / Fallback / Scanner / API

**Files:**
- Modify: `betgsn/odds_health.py` — NOVO método aditivo `CreditController.apply_update(name, update)` (aplica CreditUpdate; None = no-op); `estimated_request_cost` permanece (compat) mas odds_service passa a preferir `odds_provider.estimated_cost(provider, request)`
- Modify: `betgsn/odds_service.py` — créditos via fetch result quando disponível (Task 3 já introduziu parte; completar aqui se faltou: `estimated_cost` delegado)
- Modify: `betgsn/value_strategy.py` — `scan_live` (:513-543) deixa de instanciar `OddsApiProvider` e de ler headers; usa providers do `default_odds_registry()`; consome `fetch_odds` → quotes; `scan_events` (:449-496) permanece para uso legado interno MAS `scan_live` não pode mais parsear eventos crus — construir opportunities a partir das quotes (ver abaixo)
- Modify: `betgsn/api/service.py` — `_PROVIDER_FEATURES` (:111-116) substituído por metadata do registry (features de ProviderSpec); endpoint `/api/providers` e DTO de api/schemas.py permanecem com o MESMO schema — nenhuma mudança de schema de API; nomes/estados idênticos
- Test: `tests/test_odds_provider_fallback.py` (novo) + ajustes de cobertura em arquivos existentes APENAS aditivos

**Detalhes:**

- `scan_live` hoje: `OddsApiProvider.from_env()` (:519-521), `live_odds_with_meta(sport, markets="h2h")` (:532), `headers["x-requests-remaining"]` (:539), `scan_events(events)` (:543). Novo: para cada provider do registry (available_providers), `fetch_odds(OddsFetchRequest(divisions=divisions_for(provider, sport), markets=("Resultado Final (1X2)",), fetched_at=<now injetado — verifique como scan_live obtém tempo hoje>))`; das quotes, montar opportunities: agrupar por evento (usar `group_by_event`/`group_by_market` de odds_normalize.py:436/:445 ou construir diretamente); `Opportunity` (value_strategy.py:~400-420) precisa: home_team, away_team, commence_time (kickoff da quote), league, odds `{mercado: {casa: {resultado: odd}}}` — construir via `group_by_market(quotes)` por evento. Se `fetch.no_coverage` ou erro → registrar e seguir (comportamento atual de falha deve ser preservado: verificar o que scan_live faz hoje em falha — provavelmente raise/return; PRESERVE o comportamento externo observável). Sem timestamps inventados: kickoff vem da quote. Headers/credits: ignorar no scanner (ele só logava remaining — manter log se houver, a partir de `fetch.credits.remaining` quando não-None).
  - VERIFICAR antes: quem chama `scan_live`/`scan_events` (rg no repo: betgsn.py CLI, api?). O CLI `--scan` e endpoints que dependem disso devem continuar funcionando com o MESMO output para o mesmo input. Se `scan_events` é usado por outros caminhos com eventos já existentes, deixe-o (legado), mas scan_live não o chama mais.
- `/api/providers` (api/service.py:928-972): hoje faz merge de HealthTracker/CreditController in-process com store persistido. Features vêm de `_PROVIDER_FEATURES` hardcoded por nome. Novo: features de `default_odds_registry().metadata(name).features` com fallback para o dict hardcoded para nomes não registrados (compat: "API-Football", "Football-Data.org" não são odds providers do registry — mantenha-os no fallback). NENHUMA mudança no DTO/schema/estados.
- `estimated_request_cost`: odds_service.fetch passa a usar `odds_provider.estimated_cost(provider, request)` para o custo do gate `can_spend` (quando provider novo); legado usa cost param atual. Nunca inventar crédito.

**Passos:**

- [ ] **Step 1: Testes** `tests/test_odds_provider_fallback.py`:
  1. `CreditController.apply_update`: None → no-op (nunca inventa); update com remaining → reflete no snapshot; `known_remaining` continua header-first.
  2. OddsService fallback com providers NOVOS (fetch_odds): A falha (ProviderError) → health.record_failure A; B responde → quotes de B, `fallback_used=True`, provider="B name"; créditos de B aplicados quando presentes, `None` quando ausentes.
  3. Fallback ordem: registry order preservada (compat pin — A antes de B).
  4. Scanner: fake registry/provider (monkeypatch `default_odds_registry` em value_strategy — verifique como importar p/ monkeypatch) → scan_live retorna opportunities com odds construídas das quotes; sem odds sintéticas quando no_coverage (comportamento de falha atual preservado).
  5. API providers: features vêm do registry; nomes "The Odds API"/"ParlayAPI" preservados; health states idênticos (usar TestClient como test_provider_health_api.py).
  6. AST/static: `value_strategy.py` não contém "x-requests" nem "OddsApiProvider.from_env" (rg-based check no teste, seguindo padrão AST de test_strategy_extensibility.py:170).
- [ ] **Step 2: Rodar** — FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Testes novos** — PASS. **Step 5: Suíte completa** — 0 falhas (atenção especial: test_odds_layer.py (fallback/credits), test_provider_health_api.py (API), test_validation_cache.py (scanner), test_api.py).
- [ ] **Step 6: Diff review + commit:** `git commit -m "refactor: route odds health and fallback through providers"`

---

### Task 5: B.5 — Integração operacional (LiveOddsCapture multi-provider + colisões)

**Files:**
- Modify: `betgsn/backtest_sources.py` — `LiveOddsCapture` aceita `Sequence[OddsProvider]` (compat: objeto único legado também aceito); caminho contrato novo; contabilização de colisões; remoção da leitura direta de `x-requests-*` (:881-883) no caminho novo (legado preserva); `CaptureReport` ganha campos aditivos
- Modify: `betgsn/betgsn.py` — `--capture-odds` monta providers via `default_odds_registry().available_providers()` (flags CLI idênticas)
- Test: `tests/test_provider_extensibility.py`

**Interfaces (consome Tasks 1-4):**

```python
@dataclass(frozen=True)
class CaptureAccounting:
    """Contabilização explícita de chaves físicas duplicadas na captura."""
    provider: str
    match_key: str
    market: str
    outcome: str
    bookmaker: str
    timestamp: str
    action: str        # "ignored" (já existia no store) | "deduped" (mesmo provider) | "collision" (outro provider)
    kept_by: str = ""  # provider que ficou dono da linha persistida
```

`CaptureReport` ganha (aditivo, defaults): `accounting: tuple[CaptureAccounting, ...] = ()`, `providers_used: list[str] = field(default_factory=list)`, `per_provider: dict[str, dict[str, int]] = field(default_factory=dict)` (counts: quotes, observations_saved, events_matched/unmatched/ambiguous por provider). `to_json` (asdict) serializa naturalmente.

**LiveOddsCapture:**
- `__init__(providers, ...)` onde `providers: Sequence | OddsApiProvider` — se não for Sequence, vira `[provider]` (compat total com testes existentes que passam `provider=fake`). Manter aceitar keyword `provider=`? Os testes existentes usam `provider=` posicional/keyword — VERIFIQUE (rg "LiveOddsCapture(" tests/): preservar o nome do parâmetro `provider` aceitando um OU muitos (Union ou detecção por hasattr) é a opção de menor risco; alternativamente manter `provider` como alias que popula `self.providers`. Escolha o que tocar menos os testes existentes — eles NÃO devem ser editados.
- Caminho legado (provider sem `fetch_odds`): código atual preservado verbatim (normalize no nível da captura, headers `x-requests-*`, snapshot "the-odds-api-live", `_fetch_with_fallback`). Toda a contabilização de colisões também se aplica ao legado (single provider: apenas "ignored"/"deduped").
- Caminho contrato (provider com `fetch_odds`): para cada sport_key do request de captura: para cada provider (ordem registry/injeção): `divisions = divisions_for(provider, sport)` → `fetch_odds(OddsFetchRequest(divisions=divisions, markets=<labels internos — captura hoje pede h2h,totals,btts → labels internos equivalentes>, regions=self.regions, fetched_at=stamp))`. Latência real medida por chamada (perf injetado). Health/credits no HealthTracker/CreditController LOCAL por provider name (como hoje, generalizado). `record_success`/`record_failure`/`record_no_coverage` por provider. Snapshot: se `raw_events` e `snapshot_provider` → salvar `OddsSnapshot(..., provider=result.snapshot_provider)`; se provider não fornece raw_events, sem snapshot (não inventar).
- Persistência: quotes de todos os providers → `dedupe_quotes` POR PROVIDER (como hoje) → `FixtureMatchIndex.resolve` com `divisions` do provider (NÃO importar `SPORT_KEY_TO_DIVISIONS` no caminho novo — divisões vêm do adapter; legado mantém o import atual) → `observations_from_quotes(..., match_keys=...)` → **contabilização** → `store.add`.
- **Contabilização de colisões (núcleo da task):** antes do `store.add`, construir chave física `(match_key, market, outcome, bookmaker, utc_key(timestamp))` para cada observação (usar `timeutil.utc_key` — o store canonicaliza no `add` :358; comparar no mesmo espaço canônico). Sequência:
  1. Pré-checar existência: para cada match_key do lote, `store.all_observations(match_key)` → conjunto de chaves existentes. Chave existente → action "ignored" (não reenviar ao add; kept_by = provider original persistido se disponível no campo provider da observação existente, senão vazio).
  2. Dentro do lote: chave repetida — mesmo provider → "deduped"; provider diferente → "collision" com `kept_by` = provider da primeira ocorrência. A primeira ocorrência vai ao `store.add`.
  3. Pós `add`: `added = store.add(...)`; assert contábil: `added == len(enviadas)` (se divergir sem explicação, registre erro no report — nunca mascare).
  4. Preencher `report.accounting` com TODAS as entradas não mantidas ("ignored"/"deduped"/"collision"); `observations_saved` continua contando linhas efetivamente gravadas (comportamento atual) — adicionar `observations_received` (brutas) e `observations_dropped` (= len(accounting)) para auditoria.
- **Falha de um provider não contamina o outro**: try/except por provider; erro → `report.errors` + health record_failure; continue para o próximo. Observações válidas de outros providers persistem integralmente.
- `betgsn.py --capture-odds`: hoje instancia `OddsApiProvider` (verificar :343) → passa a usar `default_odds_registry().available_providers()`; se registry vazio → mensagem de erro atual preservada (verificar o que CLI faz hoje sem chave). Flags idênticas.

**Passos:**

- [ ] **Step 1: Testes** `tests/test_provider_extensibility.py` (padrão FASE A — test_strategy_extensibility.py como molde; fakes com contrato mínimo; registry fresco; store SQLite tmp):
  1-13: os 13 cenários mínimos do spec (A válido; B válido; A falha → B funciona; A sem cobertura; B sem cobertura; A+B quotes distintas; A+B mesma chave física → collision reportada; sem dados sintéticos; falha não apaga observações anteriores; timestamps corretos; provider name preservado nas observações/health).
  - FakeProviderA/FakeProviderB implementando OddsProvider (name/available/fetch_odds) com events/quotes reais em formato NormalizedQuote (construir quotes reais com kickoff futuro, bookmakers distintos).
  - Colisão: A e B com quote idêntica (mesmo match_key pós-resolução, market, outcome, bookmaker, timestamp) → `report.accounting` tem 1 entry action="collision", kept_by=A, provider=B; `store` tem exatamente 1 linha; `observations_saved == 1`.
  - "ignored": mesma captura rodada 2x (mesmo timestamp) → segunda rodada reporta "ignored" (linha já existia).
  - AST/static checks (padrão FASE A): `betgsn/backtest_sources.py` caminho novo não referencia "x-requests" (legado pode); core (`staking.py`, `strategy.py`, `strategy_runner.py`, `models/promotion.py`, `portfolio/`, `odds_snapshots.py`, `features/movement.py`) não importa `providers`/`odds_registry`/`odds_provider` — espelhar test_strategy_extensibility.py:170 com os módulos da FASE B.
  - Conformance Task 1 contra FakeProviderA/B.
  - Compat: `LiveOddsCapture(provider=FakeLiveProvider())` (legado, formato odds_event_to_internal) continua funcionando — teste de compatibilidade espelhando test_operational_pipeline.py:125.
- [ ] **Step 2: Rodar** — FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Testes novos** — PASS.
- [ ] **Step 5: Suíte backend COMPLETA** `.\.venv\Scripts\python.exe -m pytest -q` — 0 falhas. Criticamente: test_operational_pipeline.py (T-1..T-10), test_odds_identity_integration.py, test_backtest_sources.py (42), test_odds_snapshots.py, test_clv_prospective_real.py.
- [ ] **Step 6: Frontend:** `cd web; npm install` (se node_modules ausente); `npm test`; `npm run build` — ambos devem passar (nada no frontend deve mudar).
- [ ] **Step 7: Revisão de escopo:** `git status` (sem arquivos fora do escopo); `git diff --check`; confirmar que NENHUM destes foi alterado: odds_snapshots.py, odds_math.py, line_shopping.py, markets.py, staking.py, strategy.py, strategy_runner.py, models/, portfolio/, evaluation.py, features/movement.py, engine.py, datalayer/, canonical.py; sem migração de schema (rg "SCHEMA_VERSION" — deve seguir 3).
- [ ] **Step 8: Commit:** `git commit -m "feat: complete provider extensibility integration"`

---

## Estratégia de migração sem big-bang

Strangler: Task 1 (contrato aditivo puro) → Task 2 (registry delega ao código atual) → Task 3 (adapters envolvem as classes atuais; `live_odds_with_meta` permanece) → Task 4 (call-sites um a um; legado coexiste) → Task 5 (captura dual-path; legado = código atual verbatim). Em nenhum momento dois sistemas operam: o registry sempre resolve para as classes atuais. Rollback: cada task = commits isolados revertíveis com `git revert`.

## Follow-ups conhecidos (NÃO corrigir; registrar no relatório final)

- `ODDS_API_MARKET_MAP` morto (backtest_sources.py:63-67) divergente do MARKET_MAP.
- `FixtureMatchIndex.to_dict` quebrado (odds_normalize.py:233-248, AttributeError latente).
- Roundtrip de créditos perde daily_limit/exhausted (odds_snapshots.py:487-497).
- `NormalizedQuote.sport_key` carrega sport key Odds API (mantido por decisão).
- Coexistência física de quotes cross-provider no mesmo bookmaker/timestamp (schema sem provider) — contabilizada, não resolvida.
- 4 dataclasses `ProviderHealth` paralelas; 2 sistemas de health/quota; datalayer orphaned.
