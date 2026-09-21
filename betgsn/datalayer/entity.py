"""BETGSN :: datalayer.entity — normalização e casamento de entidades.

Fontes diferentes chamam o mesmo time de formas diferentes ("São Paulo",
"Sao Paulo FC", "CR Flamengo", "Flamengo"). Sem normalização, o histórico
de um time vira dois e o modelo aprende de dados partidos.

Duas regras inegociáveis:

1. **Nada de fuzzy matching perigoso.** O casamento padrão é por chave
   normalizada exata (nome canônico ou alias). Se a mesma chave aponta para
   mais de uma entidade, o resultado é AMBIGUOUS — nunca se escolhe uma no
   chute.
2. **Nunca inventar correspondência.** Sem chave exata, o resultado é
   UNKNOWN. O modo fuzzy (opcional, opt-in) exige limiar alto E vencedor
   único; empate continua sendo AMBIGUOUS.

A normalização reutiliza `backtest_sources.normalize_team` para existir UMA
regra de nomes no projeto, não duas que divergem.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from ..backtest_sources import normalize_team

__all__ = [
    "EntityKind",
    "MatchStatus",
    "Entity",
    "EntityMatch",
    "EntityRegistry",
    "normalize_name",
]


class EntityKind(str, Enum):
    TEAM = "team"
    COMPETITION = "competition"
    COUNTRY = "country"


class MatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


def normalize_name(name: str) -> str:
    """Chave canônica de nome (delega a regra única do projeto)."""
    return normalize_team(name)


@dataclass(frozen=True)
class Entity:
    """Entidade canônica com IDs externos e aliases conhecidos."""

    key: str
    name: str
    kind: EntityKind = EntityKind.TEAM
    country: str = ""
    league: str = ""
    aliases: tuple[str, ...] = ()
    external_ids: tuple[tuple[str, str], ...] = ()

    def external_id(self, source: str) -> str | None:
        for src, value in self.external_ids:
            if src == source:
                return value
        return None

    @property
    def normalized(self) -> str:
        return normalize_name(self.name)


@dataclass(frozen=True)
class EntityMatch:
    """Resultado de um casamento: nunca uma correspondência inventada."""

    query: str
    normalized: str
    status: MatchStatus
    entity: Entity | None = None
    candidates: tuple[Entity, ...] = ()
    score: float | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == MatchStatus.MATCHED and self.entity is not None

    @property
    def ambiguous(self) -> bool:
        return self.status == MatchStatus.AMBIGUOUS


class EntityRegistry:
    """Índice de entidades por chave normalizada e por ID externo."""

    def __init__(self) -> None:
        self._by_key: dict[str, Entity] = {}
        self._by_norm: dict[tuple[EntityKind, str], set[str]] = {}
        self._by_external: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------ cadastro

    def add(self, entity: Entity) -> Entity:
        self._by_key[entity.key] = entity
        self._index(entity, normalize_name(entity.name))
        for alias in entity.aliases:
            self._index(entity, normalize_name(alias))
        for source, value in entity.external_ids:
            self._by_external[(source, str(value))] = entity.key
        return entity

    def _index(self, entity: Entity, normalized: str) -> None:
        if not normalized:
            return
        self._by_norm.setdefault((entity.kind, normalized), set()).add(entity.key)

    def add_alias(self, key: str, alias: str) -> None:
        entity = self._by_key.get(key)
        if entity is None:
            raise KeyError(f"entidade desconhecida: {key!r}")
        self._index(entity, normalize_name(alias))

    # ------------------------------------------------------------ consulta

    def get(self, key: str) -> Entity | None:
        return self._by_key.get(key)

    def __len__(self) -> int:
        return len(self._by_key)

    def keys(self) -> list[str]:
        return sorted(self._by_key)

    def by_external(self, source: str, external_id: str) -> Entity | None:
        key = self._by_external.get((source, str(external_id)))
        return self._by_key.get(key) if key else None

    def resolve(
        self,
        name: str,
        *,
        kind: EntityKind | None = None,
        country: str | None = None,
        league: str | None = None,
    ) -> EntityMatch:
        """Casa por chave normalizada EXATA. Ambiguidade é explícita."""
        normalized = normalize_name(name)
        return self._resolve_normalized(
            query=name, normalized=normalized, kind=kind,
            country=country, league=league, fuzzy=False,
        )

    def resolve_fuzzy(
        self,
        name: str,
        *,
        kind: EntityKind | None = None,
        country: str | None = None,
        league: str | None = None,
        threshold: float = 0.92,
    ) -> EntityMatch:
        """Casamento aproximado, conservador.

        Só devolve MATCHED se houver UM vencedor acima do limiar e ele for
        estritamente melhor que o segundo. Empate ou margem insuficiente
        vira AMBIGUOUS. Limiar alto por padrão justamente para não virar
        "adivinhação".
        """
        normalized = normalize_name(name)
        return self._resolve_normalized(
            query=name, normalized=normalized, kind=kind,
            country=country, league=league, fuzzy=True, threshold=threshold,
        )

    def _resolve_normalized(
        self,
        *,
        query: str,
        normalized: str,
        kind: EntityKind | None,
        country: str | None,
        league: str | None,
        fuzzy: bool,
        threshold: float = 0.92,
    ) -> EntityMatch:
        if not normalized:
            return EntityMatch(query, normalized, MatchStatus.UNKNOWN,
                               note="nome vazio")

        keys: set[str] = set()
        if kind is None:
            for (candidate_kind, key_norm), bucket in self._by_norm.items():
                if key_norm == normalized:
                    keys |= bucket
        else:
            keys = set(self._by_norm.get((kind, normalized), set()))

        candidates = [self._by_key[k] for k in sorted(keys)]
        candidates = _filter(candidates, country=country, league=league)

        if len(candidates) == 1:
            return EntityMatch(query, normalized, MatchStatus.MATCHED, entity=candidates[0])
        if len(candidates) > 1:
            return EntityMatch(query, normalized, MatchStatus.AMBIGUOUS,
                               candidates=tuple(candidates),
                               note="mais de uma entidade com a mesma chave")

        if not fuzzy:
            return EntityMatch(query, normalized, MatchStatus.UNKNOWN)

        return self._fuzzy(query, normalized, kind, country, league, threshold)

    def _fuzzy(
        self,
        query: str,
        normalized: str,
        kind: EntityKind | None,
        country: str | None,
        league: str | None,
        threshold: float,
    ) -> EntityMatch:
        pool = [
            e for e in self._by_key.values()
            if kind is None or e.kind == kind
        ]
        pool = _filter(pool, country=country, league=league)
        scored: list[tuple[float, Entity]] = []
        for entity in pool:
            names = (normalize_name(entity.name),) + tuple(
                normalize_name(a) for a in entity.aliases
            )
            best = max(
                (SequenceMatcher(None, normalized, n).ratio() for n in names if n),
                default=0.0,
            )
            if best >= threshold:
                scored.append((best, entity))
        scored.sort(key=lambda pair: (-pair[0], pair[1].key))
        if not scored:
            return EntityMatch(query, normalized, MatchStatus.UNKNOWN,
                               score=0.0)
        top = scored[0]
        if len(scored) > 1 and scored[1][0] == top[0]:
            return EntityMatch(query, normalized, MatchStatus.AMBIGUOUS,
                               candidates=tuple(e for _, e in scored),
                               score=top[0],
                               note="empate no casamento aproximado")
        return EntityMatch(query, normalized, MatchStatus.MATCHED,
                           entity=top[1], score=top[0])


def _filter(entities: list[Entity], *, country: str | None, league: str | None) -> list[Entity]:
    out = entities
    if country:
        wanted = normalize_name(country)
        out = [e for e in out if normalize_name(e.country) == wanted]
    if league:
        wanted = normalize_name(league)
        out = [e for e in out if normalize_name(e.league) == wanted]
    return out
