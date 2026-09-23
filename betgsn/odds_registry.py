"""BETGSN :: odds_registry — registro de providers de odds (FASE B).

POR QUE este modulo existe
--------------------------
A ordem de preferencia entre providers de odds estava "costurada" no
codigo (`providers.ODDS_PROVIDER_PRIORITY`). A partir daqui ela vive em
especificacoes registraveis (`ProviderSpec`): um provider novo de odds
entra registrando UMA spec no `default_odds_registry()` — sem editar o
core nem os callers (`odds_service`, API).

Regras que evitam erro silencioso
---------------------------------
  - factory que devolve None significa "nao configurado": o provider
    simplesmente NAO aparece em `available_providers()` e `lookup`
    devolve None. Ausencia de chave nao e erro, e sim exclusao —
    nenhum provider e obrigatorio para o funcionamento global;
  - nome duplicado e `ValueError` no registro (falhar rapido): dois
    providers com o mesmo label trocariam dados sem ninguem notar;
  - nome desconhecido e `KeyError` em `metadata`/`lookup`: nunca
    devolver um provider "parecido" no lugar do pedido;
  - `lookup` cacheia a construcao por instancia: a factory e chamada
    UMA vez por nome. Registry novo = leitura de ambiente nova.

Import tardio de proposito: `providers` importa este modulo no topo, e
`default_odds_registry()` importa `providers` DENTRO da funcao. Sem
isso, o ciclo providers -> odds_registry -> providers estouraria na
carga do pacote.

O que este modulo deliberadamente NAO faz
-----------------------------------------
Nao consulta ambiente (as factories leem), nao mede saude/creditos
(`odds_health`), nao busca odds (`odds_service`) e nao conhece decisao
de negocio. E só o catalogo: quem existe, em que ordem e como se
constroi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ProviderSpec:
    """Especificacao de um provider de odds para o registry.

    `name` e o label estavel usado pelo sistema (ex.: "The Odds API").
    `factory` constroi o provider a partir do ambiente OU devolve None
    quando ele nao esta configurado — o registry nao le variaveis de
    ambiente, essa responsabilidade e da factory. `priority` menor =
    preferido primeiro. `features` e metadado declarativo para a API
    (ex.: ("odds", "live")); nao influencia nenhuma decisao aqui.
    """

    name: str
    factory: Callable[[], object]
    priority: int = 100
    features: tuple[str, ...] = ()


class OddsProviderRegistry:
    """Registro de providers de odds por nome. Sem logica de coleta.

    Responsabilidades: registrar, recuperar por nome, impedir duplicata
    e listar em ordem de prioridade. Nada mais — o registry nao busca
    odds, nao mede saude e nao decide quem atende (isso e do
    `odds_service`, que consume a lista ordenada).
    """

    def __init__(self) -> None:
        self._specs: dict[str, ProviderSpec] = {}
        self._built: dict[str, object] = {}

    def register(self, spec: ProviderSpec) -> None:
        """Registra uma spec; recusa nome vazio ou duplicado.

        A checagem acontece no registro (falhar rapido): um label
        duplicado faria duas fontes diferentes responderem pelo mesmo
        nome, e um label vazio quebraria todo lookup por nome.
        """
        name = spec.name
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                "provider precisa de 'name' (string nao vazia), "
                f"recebi {name!r}"
            )
        if name in self._specs:
            raise ValueError(f"provider ja registrado: {name!r}")
        self._specs[name] = spec

    def specs(self) -> list[ProviderSpec]:
        """Specs registradas, ordenadas por (priority, name).

        O desempate por nome torna a ordem deterministica quando duas
        specs dividem a prioridade.
        """
        return sorted(self._specs.values(), key=lambda s: (s.priority, s.name))

    def names(self) -> list[str]:
        """Nomes registrados, na mesma ordem de `specs()`."""
        return [spec.name for spec in self.specs()]

    def metadata(self, name: str) -> ProviderSpec:
        """Spec registrada sob `name`; desconhecido e erro."""
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(
                f"provider nao registrado: {name!r} "
                f"(registrados: {', '.join(self.names()) or 'nenhum'})"
            ) from None

    def lookup(self, name: str):
        """Provider construido pela factory, com cache por nome.

        Devolve None quando a factory devolveu None ("nao configurado").
        Nome desconhecido e `KeyError` — nunca um substituto silencioso.
        """
        self.metadata(name)
        if name not in self._built:
            self._built[name] = self._specs[name].factory()
        return self._built[name]

    def available_providers(self) -> list[tuple[str, object]]:
        """[(name, provider)] dos providers CONFIGURADOS, por prioridade.

        Providers cuja factory devolveu None ficam de fora: ausencia de
        chave nao e erro e nunca gera entrada "fantasma" na lista.
        """
        return [
            (spec.name, provider)
            for spec in self.specs()
            if (provider := self.lookup(spec.name)) is not None
        ]


def default_odds_registry() -> OddsProviderRegistry:
    """Registry padrao: os CINCO providers de odds, por prioridade.

    Factories `*.from_env` devolvem None sem chave — provider opcional
    fica registrado como spec mas NAO entra em `available_providers()`
    (ausencia de chave e exclusao, nao erro). Um registry NOVO por
    chamada: cada uso le o ambiente na hora, como
    `providers.configured_odds_providers` sempre fez — sem estado
    global escondido entre chamadas.

    Ordem (priority menor = preferido no fallback): The Odds API (1),
    ParlayAPI (2), OddsPapi (3), Odds-API.io (4), OpticOdds (5).
    Multi-provider AGREGACAO (OddsService.fetch_aggregated) consulta
    TODOS os configurados; o priority so decide dono de chave fisica em
    empate de timestamp e a ordem do fallback.

    Import de `providers` acontece AQUI dentro (tardio): `providers`
    importa este modulo no topo, e o import no nivel de modulo criaria
    o ciclo providers -> odds_registry -> providers.
    """
    from .providers import (
        OddsApiProvider,
        OddsApiIoProvider,
        OddsPapiProvider,
        OpticOddsProvider,
        ParlayApiProvider,
    )

    registry = OddsProviderRegistry()
    registry.register(
        ProviderSpec(
            name="The Odds API",
            factory=OddsApiProvider.from_env,
            priority=1,
            features=("odds",),
        )
    )
    registry.register(
        ProviderSpec(
            name="ParlayAPI",
            factory=ParlayApiProvider.from_env,
            priority=2,
            # ("odds",) e o valor historico servido pela API antes das
            # specs carregarem features — preservado de proposito para
            # nao mudar a resposta existente deste provider.
            features=("odds",),
        )
    )
    registry.register(
        ProviderSpec(
            name="OddsPapi",
            factory=OddsPapiProvider.from_env,
            priority=3,
            features=("odds", "live"),
        )
    )
    registry.register(
        ProviderSpec(
            name="Odds-API.io",
            factory=OddsApiIoProvider.from_env,
            priority=4,
            features=("odds", "live"),
        )
    )
    registry.register(
        ProviderSpec(
            name="OpticOdds",
            factory=OpticOddsProvider.from_env,
            priority=5,
            features=("odds", "live"),
        )
    )
    return registry
