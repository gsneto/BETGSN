"""Testes de `betgsn.timeutil` — normalizacao de kickoff.

O ponto central: a chave canonica UTC precisa ordenar exatamente na mesma
ordem que o tempo real. Se isso falhar, o corte point-in-time do backtest
passa a vazar informacao sem que nada acuse erro.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from betgsn.timeutil import (
    KickoffError,
    UTC_FORMAT,
    day_of,
    is_valid_kickoff,
    parse_kickoff,
    to_display,
    utc_key,
)


# --------------------------------------------------------------------------
# Formatos aceitos
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "tz_name", "expected"),
    [
        ("2025-05-05 16:00", "", "2025-05-05T16:00:00Z"),
        ("2025-05-05T16:00:00Z", "", "2025-05-05T16:00:00Z"),
        ("2025-05-05T16:00:00", "", "2025-05-05T16:00:00Z"),
        ("2025-05-05T13:00:00-03:00", "", "2025-05-05T16:00:00Z"),
        ("2025-05-05T18:00:00+02:00", "", "2025-05-05T16:00:00Z"),
        ("2025-05-05", "", "2025-05-05T00:00:00Z"),
        # nome IANA so e usado quando a string nao traz fuso
        ("2025-05-05 13:00", "America/Sao_Paulo", "2025-05-05T16:00:00Z"),
        # com offset na string, o nome IANA e ignorado
        ("2025-05-05T13:00:00-03:00", "Europe/London", "2025-05-05T16:00:00Z"),
        ("2025-05-05 16:00", "UTC", "2025-05-05T16:00:00Z"),
    ],
)
def test_utc_key_normalizes_all_supported_formats(value, tz_name, expected):
    assert utc_key(value, tz_name) == expected


def test_parse_returns_aware_datetime_in_utc():
    parsed = parse_kickoff("2025-05-05 16:00")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.year == 2025 and parsed.hour == 16


# --------------------------------------------------------------------------
# Ordenacao: a propriedade que sustenta o corte temporal
# --------------------------------------------------------------------------


def test_utc_keys_sort_chronologically_across_timezones():
    """Misturar fusos nao pode quebrar a ordem cronologica.

    Caso adversarial: a string MAIOR representa o instante MENOR. Ordenar
    as strings cruas daria a ordem errada; a chave UTC tem que dar a certa.
    """
    earlier_utc = "2025-05-05T23:00:00+09:00"   # 14:00Z (string maior)
    later_utc = "2025-05-05T15:00:00-03:00"     # 18:00Z (string menor)
    assert utc_key(earlier_utc) < utc_key(later_utc)
    # ordenar pelas strings cruas inverteria a ordem real
    assert sorted([earlier_utc, later_utc]) == [later_utc, earlier_utc]

    cases = [
        "2025-05-05T09:00:00-03:00",   # 12:00Z
        "2025-05-05T14:00:00+01:00",   # 13:00Z
        "2025-05-05T14:00:00Z",        # 14:00Z
        "2025-05-06T00:00:00+09:00",   # 15:00Z
    ]
    keys = [utc_key(c) for c in cases]
    assert keys == sorted(keys)


def test_utc_key_round_trip_is_stable():
    key = utc_key("2025-05-05T13:00:00-03:00")
    assert utc_key(key) == key
    assert datetime.strptime(key, UTC_FORMAT)


def test_day_of_uses_utc():
    # 22:00 em Sao Paulo (-03:00) = 01:00 UTC do dia seguinte
    assert day_of("2025-05-05T22:00:00-03:00") == "2025-05-06"
    assert day_of("2025-05-05 22:00") == "2025-05-05"


# --------------------------------------------------------------------------
# Erros explicitos
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "amanha", "2025-13-45 99:99"])
def test_invalid_kickoff_raises(value):
    with pytest.raises(KickoffError):
        parse_kickoff(value)


def test_invalid_kickoff_never_returns_approximate_time():
    """Nunca devolver um horario 'aproximado': erro aqui contamina tudo."""
    with pytest.raises(KickoffError):
        utc_key("data invalida")


def test_unknown_timezone_raises_with_clear_message():
    with pytest.raises(KickoffError, match="fuso horario desconhecido"):
        parse_kickoff("2025-05-05 16:00", "Fuso/Inventado")


def test_is_valid_kickoff():
    assert is_valid_kickoff("2025-05-05 16:00")
    assert is_valid_kickoff("2025-05-05T16:00:00Z")
    assert not is_valid_kickoff("")
    assert not is_valid_kickoff("nao e data")


# --------------------------------------------------------------------------
# Exibicao
# --------------------------------------------------------------------------


def test_to_display_uses_local_timezone():
    # mesmo instante, exibido no fuso da partida
    assert to_display("2025-05-05T16:00:00Z", "America/Sao_Paulo") == "2025-05-05 13:00"
    assert to_display("2025-05-05 16:00") == "2025-05-05 16:00"


def test_dst_is_respected():
    """Fusos com horario de verao: o offset muda ao longo do ano.

    Sao Paulo nao tem DST desde 2019, mas Londres tem — e um fuso que muda
    de offset e o caso que quebra calculos ingenuos de offset fixo. Como
    `parse_kickoff` normaliza para UTC, a prova e o instante UTC mudar:
    12:00 em Londres e 12:00Z no inverno e 11:00Z no verao.
    """
    winter = parse_kickoff("2025-01-15 12:00", "Europe/London")
    summer = parse_kickoff("2025-07-15 12:00", "Europe/London")
    assert winter.hour == 12          # GMT
    assert summer.hour == 11          # BST (UTC+1)
    assert winter.tzinfo is not None and summer.tzinfo is not None
