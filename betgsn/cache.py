"""BETGSN :: cache — Cache persistente com TTL configurável.

Armazena respostas de APIs em disco (JSON) para evitar chamadas repetidas.
Cada entrada tem timestamp de criação e TTL em segundos.
"""
from __future__ import annotations
import json
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

@dataclass
class CacheEntry:
    data: Any
    created_at: float
    ttl_seconds: float
    source: str = ""
    
    @property
    def expired(self) -> bool:
        if self.ttl_seconds <= 0:  # infinite TTL
            return False
        return time.time() - self.created_at > self.ttl_seconds

    def age_seconds(self, now: Optional[float] = None) -> float:
        """Idade da entrada em segundos. Nunca negativa."""
        return max(0.0, (now if now is not None else time.time()) - self.created_at)

    def age_seconds_at(self, now: float) -> float:
        return self.age_seconds(now)

    def expired_at(self, now: float) -> bool:
        """`expired`, mas com um instante injetado (testavel e deterministico)."""
        if self.ttl_seconds <= 0:
            return False
        return now - self.created_at > self.ttl_seconds

# Default TTLs in seconds
TTL_INFINITE = 0
TTL_HISTORICAL = 0        # never expires
TTL_FIXTURES = 6 * 3600   # 6h
TTL_H2H = 24 * 3600       # 24h
TTL_TEAM_STATS = 12 * 3600  # 12h
TTL_INJURIES = 6 * 3600   # 6h
TTL_LINEUPS = 30 * 60     # 30min
TTL_ODDS = 15 * 60        # 15min (configurable)
TTL_LIVE = 2 * 60         # 2min

class DiskCache:
    """JSON-based disk cache with TTL support."""
    
    def __init__(self, root: Path, default_ttl: float = TTL_FIXTURES):
        self.root = root
        self.default_ttl = default_ttl
        self.root.mkdir(parents=True, exist_ok=True)
        self._stats = {"hits": 0, "misses": 0, "writes": 0, "evictions": 0}
    
    def _key_path(self, namespace: str, key: str) -> Path:
        safe = hashlib.sha256(key.encode()).hexdigest()[:16]
        d = self.root / namespace
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe}.json"
    
    def get(self, namespace: str, key: str) -> Optional[Any]:
        """Returns cached data or None if missing/expired."""
        path = self._key_path(namespace, key)
        if not path.exists():
            self._stats["misses"] += 1
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entry = CacheEntry(**raw)
            if entry.expired:
                self._stats["evictions"] += 1
                path.unlink(missing_ok=True)
                return None
            self._stats["hits"] += 1
            return entry.data
        except (json.JSONDecodeError, TypeError, KeyError):
            self._stats["misses"] += 1
            return None
    
    def put(self, namespace: str, key: str, data: Any, 
            ttl: Optional[float] = None, source: str = "",
            created_at: Optional[float] = None) -> None:
        """Stores data with optional TTL override.

        `created_at` permite injetar o instante de gravação (epoch). Sem ele
        usa o relógio real. A camada de dados passa o instante da consulta
        para que idade/staleness sejam determinísticos e coerentes com a
        proveniência do dado.
        """
        entry = CacheEntry(
            data=data,
            created_at=created_at if created_at is not None else time.time(),
            ttl_seconds=ttl if ttl is not None else self.default_ttl,
            source=source,
        )
        path = self._key_path(namespace, key)
        path.write_text(
            json.dumps({"data": entry.data, "created_at": entry.created_at,
                        "ttl_seconds": entry.ttl_seconds, "source": entry.source}),
            encoding="utf-8",
        )
        self._stats["writes"] += 1

    def get_entry(self, namespace: str, key: str) -> Optional[CacheEntry]:
        """Devolve a entrada crua (mesmo expirada) ou None.

        Diferente de `get`, NAO aplica TTL nem apaga o arquivo. Existe para
        quem precisa da idade real do dado: a camada de dados distingue
        "fresco" de "stale" e nunca apresenta um dado velho como atual sem
        informar ha quanto tempo foi gravado.
        """
        path = self._key_path(namespace, key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return CacheEntry(**raw)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def get_with_meta(
        self,
        namespace: str,
        key: str,
        *,
        allow_stale: bool = False,
        max_stale_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Optional[tuple[Any, dict[str, Any]]]:
        """Devolve (dado, metadados) respeitando TTL e, opcionalmente, staleness.

        Metadados: `created_at`, `ttl_seconds`, `age_seconds`, `source`,
        `expired`, `stale`.

        Sem `allow_stale`, comportamento equivalente a `get`. Com
        `allow_stale=True`, uma entrada expirada ainda e devolvida desde que
        `age_seconds <= ttl_seconds + max_stale_seconds` (sem limite quando
        `max_stale_seconds` e None) — e marcada como `stale=True`, para o
        chamador NUNCA confundir com dado atual.
        """
        entry = self.get_entry(namespace, key)
        if entry is None:
            self._stats["misses"] += 1
            return None
        moment = now if now is not None else time.time()
        age = entry.age_seconds(moment)
        expired = entry.expired_at(moment)
        if expired:
            limit = None if max_stale_seconds is None else entry.ttl_seconds + max_stale_seconds
            if not allow_stale or (limit is not None and age > limit):
                self._stats["evictions"] += 1
                return None
        self._stats["hits"] += 1
        return entry.data, {
            "created_at": entry.created_at,
            "ttl_seconds": entry.ttl_seconds,
            "age_seconds": age,
            "source": entry.source,
            "expired": expired,
            "stale": expired,
        }
    
    def invalidate(self, namespace: str, key: str) -> bool:
        path = self._key_path(namespace, key)
        if path.exists():
            path.unlink()
            return True
        return False
    
    def clear_namespace(self, namespace: str) -> int:
        d = self.root / namespace
        if not d.exists():
            return 0
        count = 0
        for p in d.glob("*.json"):
            p.unlink()
            count += 1
        return count
    
    def stats(self) -> dict:
        return dict(self._stats)
    
    def inventory(self) -> dict[str, int]:
        """Count entries per namespace."""
        result = {}
        for d in sorted(self.root.iterdir()):
            if d.is_dir():
                result[d.name] = len(list(d.glob("*.json")))
        return result
