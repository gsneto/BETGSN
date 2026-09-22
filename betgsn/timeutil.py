"""BETGSN :: timeutil — normalizacao de horarios de kickoff.

Por que este modulo existe
--------------------------
O backtest point-in-time decide o que "existia antes" comparando instantes.
Comparar strings de formatos diferentes e uma fonte silenciosa de erro: se
um importador gravar "2025-05-05T22:00:00Z" e o dataset local gravar
"2025-05-05 19:00", uma comparacao textual coloca o jogo na ordem errada —
e o corte temporal passa a vazar informacao.

Aqui todo kickoff e reduzido a UMA chave canonica em UTC:

    "YYYY-MM-DDTHH:MM:SSZ"

Strings ISO em UTC ordenam lexicograficamente na mesma ordem que o tempo,
entao a chave serve tanto para `sorted()` quanto para `bisect`.

Formatos aceitos na entrada
---------------------------
    "2025-05-05 16:00"            dataset local (sem fuso -> assume informado)
    "2025-05-05T16:00:00Z"        ISO 8601 UTC
    "2025-05-05T13:00:00-03:00"   ISO 8601 com offset (fuso real da partida)
    "2025-05-05T16:00:00"         ISO 8601 sem fuso
    "2025-05-05"                  somente data (00:00)

O campo `timezone` (IANA, ex.: "America/Sao_Paulo") so e usado quando a
string NAO traz fuso explicito. Se nem a string nem o campo informam fuso,
assume-se UTC — decisao explicita, documentada, para nao introduzir um
deslocamento invisivel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

#: Formato canonico de saida.
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class KickoffError(ValueError):
    """Kickoff ausente ou em formato irreconhecivel."""


def _tzinfo(name: str) -> timezone | ZoneInfo:
    """Resolve um nome IANA. Vazio -> UTC. Desconhecido -> erro explicito.

    No Windows o banco IANA nao vem no sistema: `zoneinfo` precisa do
    pacote `tzdata` (pip). Quando ele falta, a mensagem diz exatamente
    isso, em vez de acusar o fuso de inexistente.

    Isso nao bloqueia dados reais: The Odds API e API-Football entregam
    horarios ISO 8601 COM offset, e nesse caso nenhum nome de fuso e
    consultado.
    """
    if not name:
        return UTC
    if name.upper() in {"UTC", "Z", "GMT"}:
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        if not _has_tz_database():
            raise KickoffError(
                "banco de fusos horarios (IANA) indisponivel neste sistema. "
                "Instale com `pip install tzdata` ou informe o horario com "
                "offset explicito (ex.: '2025-05-05T16:00:00-03:00')."
            ) from exc
        raise KickoffError(
            f"fuso horario desconhecido: {name!r}. Use um nome IANA "
            f"(ex.: 'America/Sao_Paulo') ou deixe vazio para UTC."
        ) from exc
    except ValueError as exc:
        raise KickoffError(
            f"fuso horario invalido: {name!r}."
        ) from exc


def _has_tz_database() -> bool:
    """True se existe algum banco IANA utilizavel."""
    try:
        ZoneInfo("UTC")
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def parse_kickoff(value: str, tz_name: str = "") -> datetime:
    """Converte um kickoff para datetime com fuso (sempre aware).

    Levanta `KickoffError` se o valor estiver vazio ou nao for parseavel —
    nunca devolve um horario "aproximado", porque um horario errado aqui
    contamina todas as metricas do backtest.
    """
    raw = (value or "").strip()
    if not raw:
        raise KickoffError("kickoff vazio: sem horario nao existe corte temporal")

    # `fromisoformat` cobre os formatos com 'T' e tambem "YYYY-MM-DD HH:MM".
    # Ele tambem reconhece offset e 'Z' (Python 3.11+).
    candidate = raw.replace(" ", "T", 1) if " " in raw else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise KickoffError(
            f"kickoff irreconhecivel: {value!r}. Formatos aceitos: "
            f"'YYYY-MM-DD HH:MM', 'YYYY-MM-DDTHH:MM:SSZ' ou ISO 8601 com offset."
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_tzinfo(tz_name))
    return parsed.astimezone(UTC)


def utc_key(value: str, tz_name: str = "") -> str:
    """Chave canonica UTC usada para ordenar e cortar o historico."""
    return parse_kickoff(value, tz_name).strftime(UTC_FORMAT)


def now_utc() -> str:
    """O instante atual, ja na chave canonica UTC (UTC_FORMAT).

    "Agora" e um instante como qualquer outro: quando vira string, tem
    que ser a MESMA chave canonica usada para kickoffs e carimbos. Chamar
    `datetime.now().strftime(...)` sem fuso produz hora local sem offset
    que, lida depois como UTC, desloca o instante — exatamente o erro que
    este modulo existe para impedir.
    """
    return datetime.now(UTC).strftime(UTC_FORMAT)


def is_valid_kickoff(value: str, tz_name: str = "") -> bool:
    try:
        parse_kickoff(value, tz_name)
        return True
    except KickoffError:
        return False


def day_of(value: str, tz_name: str = "") -> str:
    """Dia (YYYY-MM-DD) em UTC — usado para agrupar por janela temporal."""
    return parse_kickoff(value, tz_name).strftime("%Y-%m-%d")


def to_display(value: str, tz_name: str = "") -> str:
    """Horario local do kickoff, no formato "YYYY-MM-DD HH:MM".

    Usa o fuso informado (o da partida). Serve para exibicao; a comparacao
    entre instantes continua sendo feita em UTC.
    """
    parsed = parse_kickoff(value, tz_name)
    local = parsed.astimezone(_tzinfo(tz_name)) if tz_name else parsed
    return local.strftime("%Y-%m-%d %H:%M")
