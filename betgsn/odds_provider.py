"""BETGSN :: odds_provider — contrato provider-agnostic de odds.

POR QUE este modulo existe
--------------------------
A FASE B pluga novos providers de odds (The Odds API ao vivo, historico
para backtest, futuros) sem tocar o core. O contrato aqui e a UNICA
fronteira entre "de onde vem a observacao" e "o que o sistema faz com
ela": o chamador descreve o escopo canonico (divisoes, mercados) e
recebe quotes JA normalizadas (`odds_normalize.NormalizedQuote`) ou uma
falha explicita (`providers.ProviderError`).

Decisoes que evitam erro silencioso
-----------------------------------
  - disponibilidade e explicita: `available()` False significa "nao
    chame"; se chamarem mesmo assim, `fetch_odds` precisa falhar ALTO
    (ProviderError) — nunca retornar vazio que parece dado;
  - ausencia de cobertura e sinalizada (`no_coverage`), nunca fingida
    com quotes sinteticas nem disfarcada em `errors` vazios;
  - creditos nao informados sao None, nunca zero: zero teria aparencia
    de medicao;
  - `fetched_at` e injetado pelo chamador — o contrato NAO consulta
    relogio, o que mantem o fetch reproduzivel e testavel;
  - o adapter NUNCA fabrica kickoff: evento sem horario publico nao
    gera quote (regra ja aplicada por `odds_normalize.normalize_event`).

O que este contrato deliberadamente NAO carrega
-----------------------------------------------
staking, strategy, promotion, CLV, portfolio, quant. Nada de decidir
aposta, avaliar modelo ou montar carteira aqui: quem busca odds nao
conhece a decisao; quem decide nao conhece o transporte. Isso mantem
novos providers triviais de escrever e o core estavel enquanto a fonte
de dados evolui (migracao strangler da FASE B).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from .providers import ProviderError  # noqa: F401 — excecao do contrato


@dataclass(frozen=True)
class OddsFetchRequest:
    """Pedido provider-agnostic de odds. Escopo canônico por divisões.

    `divisions` e `markets` usam os rotulos internos do BETGSN (ex.:
    "E0", "Resultado Final (1X2)") — traduzir sport key/mercado de
    provider para canonico e responsabilidade do adapter. `regions` e
    conceito OPCIONAL: adapter que nao usa ignora. `fetched_at` e o
    instante da observacao, injetado pelo chamador — regra de
    reproducao: o contrato nao consulta relogio.
    """

    divisions: tuple[str, ...] = ()
    markets: tuple[str, ...] = ()
    regions: Optional[str] = None
    fetched_at: str = ""


@dataclass(frozen=True)
class CreditUpdate:
    """Creditos observados em headers do provider. None = desconhecido.

    O provider nem sempre expoe creditos: ausencia e None, nunca 0 —
    zero seria um numero inventado com cara de medicao.
    """

    last: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None


@dataclass(frozen=True)
class OddsProviderFetch:
    """Resultado de um fetch. Quotes ja normalizadas (NormalizedQuote).

    `quotes` ja passaram pelo parser canonico (`odds_normalize`) — aqui
    nao existe payload cru com contrato de shape. `raw_events` e opaco
    ao core: dicts crus preservados para snapshot de backtest.
    `snapshot_provider` e o label para `OddsSnapshot.provider` (vazio =
    este fetch nao gera snapshot). `no_coverage` True e a resposta
    EXPLICITA "sem cobertura para o escopo pedido" — o oposto de dado
    sintetico. `errors` carrega falhas nao fatais por linha.
    """

    quotes: tuple = ()
    raw_events: tuple = ()
    snapshot_provider: str = ""
    credits: Optional[CreditUpdate] = None
    no_coverage: bool = False
    errors: tuple[str, ...] = ()


@runtime_checkable
class OddsProvider(Protocol):
    """Contrato estrutural de um provider de odds.

    Superficie minima: identificacao (`name`), disponibilidade
    (`available`) e busca (`fetch_odds`). `available()` False pede "nao
    chame"; `fetch_odds` chamado num provider indisponivel levanta
    `ProviderError` — falha alta, nunca retorno vazio silencioso.
    """

    name: str

    def available(self) -> bool: ...

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch: ...


def estimated_cost(provider, request: OddsFetchRequest) -> int:
    """Custo estimado em creditos. Default 1; provider pode expor
    `estimated_cost(request)` proprio. Nunca inventa credito real."""
    fn = getattr(provider, "estimated_cost", None)
    if callable(fn):
        try:
            return max(1, int(fn(request)))
        except Exception:
            return 1
    return 1


def divisions_for(provider, scope: str) -> tuple[str, ...]:
    """Divisoes canonicas cobertas por um escopo (ex.: sport key).
    Responsabilidade do adapter; () = nao sabe (chamador decide o que fazer)."""
    fn = getattr(provider, "divisions_for", None)
    if callable(fn):
        return tuple(fn(scope))
    return ()
