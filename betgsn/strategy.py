"""BETGSN :: strategy — contrato e registro de estrategias (FASE A).

O que este modulo e
-------------------
O ponto de entrada de uma estrategia quantitativa NO pipeline de
decisao. Adicionar uma estrategia nova passa a ser:

    modulo da estrategia  +  registro no StrategyRegistry

sem modificar o core quantitativo (staking, portfolio, promotion) para
cada estrategia.

O contrato e MINIMO de proposito
--------------------------------
`Strategy` cobre somente o que o caminho de DECISAO consome:

    name        identificacao unica no registry;
    markets     mercados em que a regra foi validada;
    description metadado/provenance legivel;
    evidence()  os parametros MEDIDOS que alimentam o decision gate.

Previsao NAO faz parte do contrato — e uma decisao de projeto, nao uma
omissao: o decision gate (`staking.decide_bet`) consome evidencia
VALIDADA, nunca previsao. Uma estrategia com previsao positiva nao
produz BET por isso (regra da FASE A); as previsoes/sinais sao produzidos
pelo modulo da estrategia (ex.: `value_strategy.scan_events`) e entram
no pipeline de sinais existente. Se uma interface de previsao se tornar
necessaria ao caminho de decisao, entra aqui por decisao de contrato
explicita — nunca por acoplamento silencioso.

O que este modulo NAO faz
-------------------------
- NAO decide nada: quem decide e `staking.decide_bet`, o unico produtor
  de `BetDecision` (ver `strategy_runner` para o encadeamento).
- NAO conhece estrategias no import: `default_registry()` registra as
  estrategias do repositorio de forma LAZY, para nao criar ciclo de
  import nem instancia na carga do modulo.
- NAO e um framework: sem plugin system, sem import dinamico, sem
  reflection alem da checagem minima de contrato no registro.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "Strategy",
    "StrategyEvidence",
    "StrategyRegistry",
    "default_registry",
]


@dataclass(frozen=True)
class StrategyEvidence:
    """Parametros MEDIDOS que a estrategia oferece ao decision gate.

    Sao exatamente os numeros que `staking.decide_bet` consome: a
    vantagem medida (ROI por aposta), o erro-padrao dessa medicao, a
    odd media da regra e o tamanho da amostra. Nada aqui e previsto ou
    estimado pelo proprio modulo: e resultado de validacao historica da
    estrategia (ex.: `value_strategy.StrategyValidation`).

    `n_bets=None` significa "amostra nao informada" — o gate trata isso
    como verificacao nao avaliavel, nunca como amostra grande.
    """

    roi: float
    roi_se: float
    odd: float
    n_bets: int | None = None


@runtime_checkable
class Strategy(Protocol):
    """Contrato minimo de uma estrategia para o pipeline de decisao.

    Quem implementa NAO produz `BetDecision` e NAO decide: fornece
    identificacao, mercados e a evidencia validada. O caminho ate o
    decision gate — PIT, validacao, robustness, CLV, promotion e
    `staking.decide_bet` — e do runner (`strategy_runner`), e nenhum
    estagio pode ser contornado pela estrategia.
    """

    #: Identificacao unica no registry (string nao vazia).
    name: str
    #: Mercados em que a regra foi validada.
    markets: tuple[str, ...]
    #: Metadado legivel: o que a estrategia faz, em uma linha.
    description: str

    def evidence(self) -> StrategyEvidence:
        """Parametros validados da estrategia para o decision gate.

        Deve ser BARATO (chamado no caminho de request): validacoes
        pesadas pertencem a um processo offline com cache versionado,
        nunca a esta chamada.
        """
        ...


class StrategyRegistry:
    """Registro de estrategias por nome. Sem logica quantitativa.

    Responsabilidades: registrar, recuperar por nome, impedir duplicata
    e listar. Nada mais — o registry nao avalia, nao promove e nao
    decide nada sobre as estrategias que guarda.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> Strategy:
        """Registra uma estrategia; recusa contrato incompleto/duplicata.

        A checagem e minima e acontece no registro (falhar rapido):
        nome nao vazio, `evidence()` implementado e mercados declarados.
        """
        name = getattr(strategy, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                "estrategia precisa de 'name' (string nao vazia), "
                f"recebi {name!r}"
            )
        if not callable(getattr(strategy, "evidence", None)):
            raise ValueError(
                f"estrategia {name!r} precisa implementar evidence()"
            )
        markets = getattr(strategy, "markets", None)
        if (
            not isinstance(markets, (tuple, list))
            or not markets
            or not all(isinstance(m, str) and m for m in markets)
        ):
            raise ValueError(
                f"estrategia {name!r} precisa declarar 'markets' "
                "(sequencia nao vazia de strings)"
            )
        if name in self._strategies:
            raise ValueError(f"estrategia ja registrada: {name!r}")
        self._strategies[name] = strategy
        return strategy

    def get(self, name: str) -> Strategy:
        """Recupera a estrategia pelo nome; desconhecida e erro."""
        try:
            return self._strategies[name]
        except KeyError:
            raise KeyError(
                f"estrategia nao registrada: {name!r} "
                f"(registradas: {', '.join(self.names()) or 'nenhuma'})"
            ) from None

    def names(self) -> tuple[str, ...]:
        """Nomes registrados, em ordem alfabetica (deterministico)."""
        return tuple(sorted(self._strategies))

    def __contains__(self, name: object) -> bool:
        return name in self._strategies

    def __len__(self) -> int:
        return len(self._strategies)


#: Registry padrao do processo (lazy — nasce na primeira chamada).
_DEFAULT_REGISTRY: StrategyRegistry | None = None


def default_registry() -> StrategyRegistry:
    """O registry padrao, com as estrategias do repositorio registradas.

    Lazy de proposito: importar `betgsn.strategy` nao importa nenhuma
    estrategia (nem cria estado global na carga do modulo). Hoje o
    registro contem a estrategia validada (`value_strategy`); uma
    estrategia nova entra adicionando UMA linha aqui — e nenhum arquivo
    do core quantitativo muda.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        registry = StrategyRegistry()
        from .value_strategy import value_strategy

        registry.register(value_strategy)
        _DEFAULT_REGISTRY = registry
    return _DEFAULT_REGISTRY
