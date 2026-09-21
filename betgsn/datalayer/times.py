"""BETGSN :: datalayer.times — tempo canônico da camada de dados.

Toda comparação temporal da camada de dados passa por aqui, sempre em UTC.
Nunca há horário "aproximado": um carimbo inválido é tratado como ausente.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..timeutil import UTC_FORMAT, utc_key

__all__ = [
    "utc_now",
    "utc_stamp",
    "canonical_key",
    "parse_stamp",
    "age_seconds",
    "humanize_age",
    "is_after",
]


def utc_now() -> datetime:
    """Agora, sempre aware em UTC."""
    return datetime.now(timezone.utc)


def utc_stamp(moment: datetime | None = None) -> str:
    """Carimbo UTC canônico `YYYY-MM-DDTHH:MM:SSZ`."""
    value = moment if moment is not None else utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(UTC_FORMAT)


def canonical_key(value: str, tz_name: str = "") -> str:
    """Chave UTC canônica de um kickoff/timestamp (delega a `timeutil`)."""
    return utc_key(value, tz_name)


def parse_stamp(value: str) -> datetime | None:
    """Interpreta um carimbo UTC. Devolve None em vez de levantar erro.

    Aceita o formato canônico e qualquer ISO 8601 entendido por `timeutil`.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, UTC_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace(" ", "T", 1)).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def age_seconds(source_timestamp: str, now: str | None = None) -> float | None:
    """Idade do dado em segundos, ou None quando não há carimbo confiável."""
    then = parse_stamp(source_timestamp)
    if then is None:
        return None
    reference = parse_stamp(now) if now else utc_now()
    if reference is None:
        reference = utc_now()
    return (reference - then).total_seconds()


def humanize_age(seconds: float | None) -> str:
    """Idade legível. Ausência de carimbo é dita explicitamente."""
    if seconds is None:
        return "idade desconhecida"
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{int(minutes)}min"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def is_after(candidate: str, reference: str) -> bool:
    """True se `candidate` é estritamente posterior a `reference`.

    Carimbo ausente/ inválido é tratado como NÃO posterior — a camada nunca
    assume que um dado sem timestamp é recente.
    """
    a, b = parse_stamp(candidate), parse_stamp(reference)
    if a is None or b is None:
        return False
    return a > b
