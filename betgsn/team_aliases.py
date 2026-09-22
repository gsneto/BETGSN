"""BETGSN :: team_aliases — aliases de nomes de times entre fontes.

O football-data.co.uk e a The Odds API publicam nomes de times diferentes
para o MESMO clube (ex.: ``Atletico-MG`` x ``Atletico Mineiro``). A
normalizacao generica (`backtest_sources.normalize_team`) nao resolve
abreviacoes estaduais nem nomes curto/longo — e nao deve: apelidar time e
decisao de dados, nao de codigo.

Este modulo carrega a tabela versionada ``team_aliases.json``. Regras de
contrato:

  - cada entrada TEM provenance (de onde veio a equivalencia); sem ela o
    carregador recusa o arquivo inteiro — alias sem origem e alias que
    ninguem pode auditar;
  - o mapa e {(divisao_fduk, nome_no_provider) -> nome_fduk}: o escopo por
    divisao evita que um apelido de um pais casa um time de outro;
  - arquivo ausente devolve ``{}`` (zero aliases): o matching fica
    estritamente por normalizacao — deterministico, nunca fuzzy.

Nao existe fuzzy matching aqui. Ausencia de alias significa NO MATCH, e
NO MATCH e explicito no report da captura.
"""
from __future__ import annotations

import json
from pathlib import Path

TEAM_ALIASES_PATH = Path(__file__).resolve().parent / "team_aliases.json"

#: Alias sem provenancia e erro de contrato, nao warning silencioso.
_REQUIRED_FIELDS = ("division", "provider", "fduk", "provenance")


class TeamAliasError(RuntimeError):
    """Tabela de aliases invalida — o arquivo inteiro e recusado."""


def load_team_aliases(
    path: Path | str | None = None,
) -> dict[tuple[str, str], str]:
    """Carrega {(divisao_fduk, nome_no_provider) -> nome_fduk}.

    Arquivo ausente devolve ``{}``: sem aliases declarados, o matching e
    somente por normalizacao exata. Arquivo INVALIDO (entrada sem
    provenance, sem divisao, duplicada) levanta ``TeamAliasError`` — um
    alias ambiguo e pior que nenhum.
    """
    target = Path(path) if path is not None else TEAM_ALIASES_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        raise TeamAliasError(f"team_aliases ilegivel: {target} ({exc})") from None

    aliases: dict[tuple[str, str], str] = {}
    for i, entry in enumerate(payload.get("aliases", [])):
        if not isinstance(entry, dict):
            raise TeamAliasError(f"entrada {i} nao e objeto: {entry!r}")
        missing = [f for f in _REQUIRED_FIELDS if not str(entry.get(f) or "").strip()]
        if missing:
            raise TeamAliasError(
                f"entrada {i} sem campo(s) obrigatorios {missing}: {entry!r}"
            )
        key = (str(entry["division"]).strip(), str(entry["provider"]).strip())
        value = str(entry["fduk"]).strip()
        if key in aliases and aliases[key] != value:
            raise TeamAliasError(
                f"alias duplicado com valores distintos para {key}: "
                f"{aliases[key]!r} x {value!r}"
            )
        aliases[key] = value
    return aliases
