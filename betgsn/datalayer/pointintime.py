"""BETGSN :: datalayer.pointintime — garantia de não-vazamento de futuro.

O backtest só pode ver o que existia no instante do corte. Este módulo
fornece os guardas que impedem:

  - resultado de partida futura entrando como histórico;
  - xG publicado depois do corte sendo tratado como disponível;
  - envelope cuja fonte é mais recente que o instante analisado.

A regra de disponibilidade de resultado é a mesma de `temporal.result_time`
(embargo conservador de 48h quando não há carimbo explícito de publicação).
Nada aqui "arredonda" para permitir um dado: na dúvida, o dado fica fora.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from ..model import HistoricalMatch
from ..temporal import result_time
from ..timeutil import KickoffError, utc_key
from .envelope import DataEnvelope, DataStatus
from .times import is_after

__all__ = [
    "PointInTimeError",
    "available_at",
    "filter_available_before",
    "assert_no_future",
    "guard_envelope",
]


class PointInTimeError(ValueError):
    """Um dado posterior ao corte tentou entrar no conjunto analisado."""


def _dict_get(record: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(record, dict) and record.get(key) is not None:
            return record[key]
    return None


def _kickoff_of(record: Any) -> str:
    if isinstance(record, dict):
        value = _dict_get(record, "kickoff", "date", "kickoff_utc")
        if value:
            return str(value)
        fixture = record.get("fixture")
        if isinstance(fixture, dict) and fixture.get("date"):
            return str(fixture["date"])
        raise PointInTimeError("registro sem kickoff")
    kickoff = getattr(record, "kickoff", None)
    if kickoff:
        return str(kickoff)
    raise PointInTimeError(f"registro sem kickoff: {type(record).__name__}")


def _tz_of(record: Any) -> str:
    return str(getattr(record, "timezone", "") or "") if not isinstance(record, dict) else ""


def _result_time_from_kickoff(kickoff: str, tz: str = "") -> str:
    """Mesma política de `temporal.result_time`, sem exigir um HistoricalMatch."""
    placeholder = HistoricalMatch(
        home="", away="", home_goals=0, away_goals=0, kickoff=kickoff, timezone=tz
    )
    return result_time(placeholder)


def available_at(record: Any, *, kind: str = "result") -> str:
    """Instante UTC em que o registro passou a estar disponível.

    `kind="result"` usa a publicação do resultado (carimbo explícito ou
    embargo de 48h). `kind="fixture"` usa o kickoff: odds/escalação de um
    jogo futuro existem até o apito.
    """
    if kind == "fixture":
        return utc_key(_kickoff_of(record), _tz_of(record))

    explicit = _dict_get(record, "result_available_at")
    if explicit:
        return utc_key(str(explicit))

    if isinstance(record, dict):
        return _result_time_from_kickoff(_kickoff_of(record))

    if isinstance(record, HistoricalMatch) or hasattr(record, "result_available_at"):
        return result_time(record)

    to_historical = getattr(record, "to_historical", None)
    if callable(to_historical):
        return result_time(to_historical())

    raise PointInTimeError(f"não sei a disponibilidade de {type(record).__name__}")


def filter_available_before(
    records: Iterable[Any],
    as_of: str,
    *,
    kind: str = "result",
    timestamp_of: Callable[[Any], str] | None = None,
    allow_equal: bool = False,
) -> list[Any]:
    """Registros disponíveis ANTES (ou no) corte, em ordem original.

    Carimbo ausente/inválido nunca é considerado disponível: sem prova de
    publicação, o dado fica de fora.
    """
    try:
        cutoff = utc_key(as_of)
    except KickoffError as exc:
        raise PointInTimeError(f"corte inválido: {exc}") from exc

    getter = timestamp_of or (lambda r: available_at(r, kind=kind))
    out: list[Any] = []
    for record in records:
        try:
            moment = getter(record)
        except PointInTimeError:
            continue
        if not moment:
            continue
        if moment < cutoff or (allow_equal and moment == cutoff):
            out.append(record)
    return out


def assert_no_future(
    records: Sequence[Any],
    as_of: str,
    *,
    kind: str = "result",
    timestamp_of: Callable[[Any], str] | None = None,
    label: str = "dados",
) -> None:
    """Levanta `PointInTimeError` se algum registro for posterior ao corte."""
    getter = timestamp_of or (lambda r: available_at(r, kind=kind))
    offenders: list[str] = []
    for record in records:
        try:
            moment = getter(record)
        except PointInTimeError:
            continue
        if moment and is_after(moment, as_of):
            offenders.append(moment)
    if offenders:
        raise PointInTimeError(
            f"{label}: {len(offenders)} registro(s) posteriores ao corte "
            f"{as_of} (ex.: {sorted(offenders)[0]}) — vazamento de futuro"
        )


def guard_envelope(envelope: DataEnvelope, as_of: str) -> DataEnvelope:
    """Recusa um envelope cuja fonte seja posterior ao corte.

    Sem carimbo de fonte não há como provar disponibilidade anterior, mas
    também não há evidência de vazamento: o envelope passa com a idade
    marcada como desconhecida (comportamento já explícito na proveniência).
    """
    provenance = envelope.provenance
    if provenance is None or not provenance.source_timestamp:
        return envelope
    if is_after(provenance.source_timestamp, as_of):
        return DataEnvelope(
            value=None,
            provenance=provenance,
            status=DataStatus.MISSING,
            kind=envelope.kind,
            errors=envelope.errors,
            note=(
                f"dado de {provenance.source} é posterior ao corte {as_of} "
                f"({provenance.source_timestamp}); recusado"
            ),
        )
    return envelope
