"""BETGSN :: realtime.events — barramento interno de eventos em tempo real.

O engine roda numa thread propria; a API publica por SSE. Este modulo e
a ponta thread-safe entre os dois: cada assinante tem uma fila limitada
e o publish nunca bloqueia o capturador.

Regras de honestidade
--------------------
- todo evento tem `event_id` (deterministico por conteudo) e
  `event_timestamp` (o instante da observacao que o gerou, quando
  existir; senao o instante da publicacao);
- eventos duplicados (mesmo `event_id`) NAO sao reenfileirados — o
  consumidor nunca ve o mesmo fato duas vezes;
- fila cheia descarta o evento mais antigo e marca `dropped`: prefiro
  perder um update a mentir que ele chegou ou travar o capturador.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Full, Queue
from typing import Any, Callable

#: Tipos canonicos do stream interno.
EVENT_ODDS_UPDATE = "ODDS_UPDATE"
EVENT_MOVEMENT = "MOVEMENT"
EVENT_SIGNAL_CREATED = "SIGNAL_CREATED"
EVENT_SIGNAL_UPDATED = "SIGNAL_UPDATED"
EVENT_SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
EVENT_PROVIDER_STATUS = "PROVIDER_STATUS"
EVENT_MATCH_STATUS = "MATCH_STATUS"
EVENT_HEALTH_UPDATE = "HEALTH_UPDATE"
EVENT_DATA_QUALITY = "DATA_QUALITY"

#: Capacidade da fila por assinante.
SUBSCRIBER_QUEUE_SIZE = 512

#: Quantos eventos recentes ficam retidos para REPLAY por Last-Event-ID.
#: Um cliente que reconecta depois de uma queda recebe os eventos perdidos
#: (dentro da janela) em vez de perder o intervalo silenciosamente.
REPLAY_BUFFER_SIZE = 512


@dataclass(frozen=True)
class RealtimeEvent:
    """Um fato observado, pronto para broadcast."""

    type: str
    payload: dict[str, Any]
    event_id: str
    event_timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.type,
            "event_timestamp": self.event_timestamp,
            "payload": self.payload,
        }


def _now_stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_event(
    event_type: str,
    payload: dict[str, Any],
    observed_at: str = "",
) -> RealtimeEvent:
    """Evento com identidade deterministica (hash do conteudo)."""
    identity = f"{event_type}|{sorted(payload.items())!r}|{observed_at}"
    return RealtimeEvent(
        type=event_type,
        payload=payload,
        event_id=uuid.uuid5(uuid.NAMESPACE_URL, identity).hex,
        event_timestamp=observed_at or _now_stamp(),
    )


class EventBus:
    """Publicacao thread-safe para N assinantes com filas independentes."""

    def __init__(self, queue_size: int = SUBSCRIBER_QUEUE_SIZE) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, Queue] = {}
        self._next_id = 1
        self._seen: set[str] = set()
        self._seen_order: list[str] = []
        #: ring buffer dos ultimos eventos, para replay por Last-Event-ID
        self._recent: deque[RealtimeEvent] = deque(maxlen=REPLAY_BUFFER_SIZE)
        self._queue_size = queue_size
        self.published_count = 0
        self.dropped_count = 0
        self.deduped_count = 0
        self.last_event: RealtimeEvent | None = None

    def subscribe(self) -> tuple[int, Queue]:
        """Registra um assinante; devolve (handle, fila)."""
        with self._lock:
            handle = self._next_id
            self._next_id += 1
            queue: Queue = Queue(maxsize=self._queue_size)
            self._subscribers[handle] = queue
            return handle, queue

    def unsubscribe(self, handle: int) -> None:
        with self._lock:
            self._subscribers.pop(handle, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: RealtimeEvent) -> bool:
        """Entrega a todos; False quando o evento ja tinha sido publicado."""
        with self._lock:
            if event.event_id in self._seen:
                self.deduped_count += 1
                return False
            self._seen.add(event.event_id)
            self._seen_order.append(event.event_id)
            if len(self._seen_order) > 10_000:
                stale = self._seen_order[:5_000]
                self._seen_order = self._seen_order[5_000:]
                for event_id in stale:
                    self._seen.discard(event_id)
            subscribers = list(self._subscribers.values())
            self.published_count += 1
            self.last_event = event
            self._recent.append(event)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except Full:
                try:
                    queue.get_nowait()
                except Exception:  # noqa: BLE001 - fila esvaziada a forca
                    pass
                try:
                    queue.put_nowait(event)
                except Full:
                    self.dropped_count += 1
        return True

    def replay_after(self, event_id: str) -> list[RealtimeEvent]:
        """Eventos retidos APOS `event_id` (replay de reconexao SSE).

        - `event_id` vazio: nada a reenviar (conexao nova).
        - `event_id` conhecido: tudo depois dele, na ordem original.
        - `event_id` fora da janela retida: devolve TODO o buffer — o
          cliente recebe o que ainda temos, em vez de perder o intervalo
          sem aviso (a janela e limitada, nao infinita).
        """
        if not event_id:
            return []
        with self._lock:
            recent = list(self._recent)
        ids = [e.event_id for e in recent]
        if event_id in ids:
            return recent[ids.index(event_id) + 1:]
        return recent

    def drain(self, queue: Queue, limit: int = 256) -> list[RealtimeEvent]:
        """Remove ate `limit` eventos da fila (usado em testes/SSE)."""
        drained: list[RealtimeEvent] = []
        while len(drained) < limit:
            try:
                drained.append(queue.get_nowait())
            except Exception:  # noqa: BLE001 - fila vazia
                break
        return drained
