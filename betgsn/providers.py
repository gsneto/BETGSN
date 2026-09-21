"""BETGSN :: providers — camada de fontes de dados reais (opcional).

O app funciona offline com data.py. Este modulo permite plugar fontes
reais quando houver chave. Cada provider implementa a mesma interface e
falha de forma explicita (ProviderError) — nunca silenciosa.

Fontes suportadas:
  - The Odds API  (the-odds-api.com)   -> odds de varias casas, futebol
  - API-Football  (api-football.com)   -> historico, estatisticas, xG
  - Football-Data (football-data.org)  -> resultados e tabelas

Chaves via variavel de ambiente OU arquivo .env na raiz do projeto:
  BETGSN_ODDS_API_KEY
  BETGSN_APIFOOTBALL_KEY
  BETGSN_FOOTBALLDATA_KEY

Segredo nunca entra no codigo nem no Git: `.env` esta no .gitignore e so
`.env.example` (com valores vazios) e versionado.

Localizacao do `.env`: `envconfig.resolve_env_file()` procura no worktree
atual, nos diretorios pais e na raiz do worktree PRINCIPAL do Git. Assim o
mesmo `.env` da raiz serve para os worktrees de desenvolvimento paralelo,
sem duplicar chaves.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .envconfig import env_file_candidates, resolve_env_file

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

#: Variaveis lidas do ambiente ou do .env. Usado para documentar e validar.
ENV_KEYS = (
    "BETGSN_ODDS_API_KEY",
    "BETGSN_APIFOOTBALL_KEY",
    "BETGSN_FOOTBALLDATA_KEY",
)


class ProviderError(RuntimeError):
    """Falha de provider. Sempre explicita: o app mostra, nao esconde."""


def load_env_file(path: Path | None = None, override: bool = False) -> int:
    """Carrega pares KEY=VALUE de um arquivo .env para os.environ.

    Parser minimo da biblioteca padrao: o nucleo do BETGSN nao tem
    dependencia externa e nao vale adicionar uma so para ler 3 linhas.

    Regras:
      - linhas em branco e comecando com '#' sao ignoradas;
      - `export KEY=VALUE` tambem e aceito;
      - aspas simples ou duplas em volta do valor sao removidas;
      - por padrao NAO sobrescreve variavel ja definida no ambiente real
        (o ambiente ganha do arquivo, que e o comportamento esperado).

    Sem `path`, o arquivo e localizado por `envconfig.resolve_env_file`:
    isso faz o `.env` do worktree principal ser encontrado mesmo quando o
    codigo roda de um linked worktree (ex.: `BETGSN-data`), sem duplicar
    credenciais. Veja `envconfig` para a ordem de busca.

    Devolve quantas variaveis foram efetivamente definidas.
    """
    if path is not None:
        target = Path(path)
    else:
        target = resolve_env_file() or ENV_FILE
    if not target.exists():
        return 0
    applied = 0
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
        applied += 1
    return applied


def env_status() -> dict[str, bool]:
    """Quais chaves estao presentes (nunca expoe o valor)."""
    return {key: bool(os.environ.get(key, "").strip()) for key in ENV_KEYS}


def env_file_location() -> Path | None:
    """Caminho do `.env` que sera carregado, sem ler o conteudo.

    Util para diagnostico (`--providers`) e testes: mostra ONDE o sistema
    procura credenciais sem jamais imprimir uma chave.
    """
    return resolve_env_file()


def env_file_search_paths() -> list[Path]:
    """Todos os caminhos candidatos de `.env`, na ordem de busca."""
    return env_file_candidates()


# Carrega o .env na importacao: qualquer entrypoint (CLI, API, testes)
# passa a enxergar as chaves sem precisar exportar manualmente.
load_env_file()



#: Parametros de query que carregam credencial. Nunca aparecem em log/erro.
_SECRET_QUERY_KEYS = frozenset({"apikey", "api_key", "key", "token", "access_token"})


def redact_url(url: str) -> str:
    """Remove credenciais de uma URL para uso em mensagens de erro e logs.

    Sem isso, um erro de rede imprime a chave de API inteira no terminal,
    no log do servidor e na resposta HTTP para o navegador. Segredo nunca
    pode sair do processo.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<url invalida>"
    if not parsed.query:
        return url
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe = [
        # "REDACTED" (e nao "***") porque o urlencode codificaria os
        # asteriscos e a mensagem ficaria ilegivel
        (k, "REDACTED" if k.lower() in _SECRET_QUERY_KEYS else v)
        for k, v in pairs
    ]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(safe)))


def _get_with_headers(
    url: str, headers: dict[str, str] | None = None, timeout: int = 20
) -> tuple[dict | list, dict[str, str]]:
    """GET que devolve (corpo, headers).

    A The Odds API informa o custo e a quota restante em headers
    (x-requests-last / x-requests-used / x-requests-remaining). Sem eles
    nao da para planejar uma importacao historica sem estourar a cota.

    A URL nas mensagens de erro passa por `redact_url`: chave nunca vaza.
    """
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "BETGSN/1.0"})
    safe_url = redact_url(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body, {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise ProviderError(f"HTTP {e.code} em {safe_url}: {body}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"rede falhou em {safe_url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise ProviderError(f"resposta nao-JSON de {safe_url}: {e}") from e


def _get(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> dict | list:
    body, _ = _get_with_headers(url, headers, timeout)
    return body


# --------------------------------------------------------------------------
# The Odds API
# --------------------------------------------------------------------------

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
# chaves de esporte de futebol relevantes
SOCCER_KEYS = {
    "Serie A (BR)": "soccer_brazil_campeonato",
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Serie A (IT)": "soccer_italy_serie_a",
    "Bundesliga": "soccer_germany_bundesliga",
    "Ligue 1": "soccer_france_ligue_one",
    "Champions League": "soccer_uefa_champs_league",
    "Libertadores": "soccer_conmebol_copa_libertadores",
}


@dataclass
class OddsApiProvider:
    api_key: str
    regions: str = "eu,uk"        # eu inclui Pinnacle; uk inclui casas gordas
    markets: str = "h2h,totals,btts"
    odds_format: str = "decimal"

    @classmethod
    def from_env(cls) -> "OddsApiProvider | None":
        key = os.environ.get("BETGSN_ODDS_API_KEY", "").strip()
        return cls(key) if key else None

    def sports(self) -> list[dict]:
        return _get(f"{ODDS_API_BASE}/sports?apiKey={self.api_key}")

    def events(self, sport_key: str) -> list[dict]:
        q = urllib.parse.urlencode({
            "apiKey": self.api_key, "regions": self.regions,
            "markets": self.markets, "oddsFormat": self.odds_format,
        })
        return _get(f"{ODDS_API_BASE}/sports/{sport_key}/odds?{q}")

    def live_odds_with_meta(
        self,
        sport_key: str,
        regions: str | None = None,
        markets: str | None = None,
    ) -> tuple[list[dict], dict[str, str]]:
        """Odds dos jogos futuros + headers de quota.

        Este e o endpoint que o plano gratuito permite. Ele alimenta a
        captura periodica: cada chamada vira um snapshot com timestamp e,
        quando a partida acontecer, aquele snapshot passa a ser odds
        historica real.
        """
        q = urllib.parse.urlencode({
            "apiKey": self.api_key,
            "regions": regions or self.regions,
            "markets": markets or self.markets,
            "oddsFormat": self.odds_format,
        })
        body, headers = _get_with_headers(f"{ODDS_API_BASE}/sports/{sport_key}/odds?{q}")
        return (body if isinstance(body, list) else []), headers

    def scores(self, sport_key: str, days_from: int = 3) -> list[dict]:
        q = urllib.parse.urlencode({"apiKey": self.api_key, "daysFrom": days_from})
        return _get(f"{ODDS_API_BASE}/sports/{sport_key}/scores?{q}")

    def historical_odds(
        self,
        sport_key: str,
        date_iso: str,
        regions: str | None = None,
        markets: str | None = None,
    ) -> dict:
        """Snapshot historico de odds no instante pedido.

        GET /v4/historical/sports/{sport}/odds?date=...

        A resposta traz `{"timestamp": ..., "data": [...]}`. O `timestamp`
        e o instante real da captura e e ele — nao a data pedida — que
        decide se a odd estava disponivel antes do kickoff.

        Custo: a The Odds API cobra por mercado/regiao retornados. Use
        `markets` para limitar.
        """
        q = urllib.parse.urlencode({
            "apiKey": self.api_key,
            "regions": regions or self.regions,
            "markets": markets or self.markets,
            "oddsFormat": self.odds_format,
            "date": date_iso,
        })
        return _get(f"{ODDS_API_BASE}/historical/sports/{sport_key}/odds?{q}")

    def historical_events(self, sport_key: str, date_iso: str) -> dict:
        """Eventos historicos (sem odds) — barato e util para planejar janelas."""
        q = urllib.parse.urlencode({"apiKey": self.api_key, "date": date_iso})
        return _get(f"{ODDS_API_BASE}/historical/sports/{sport_key}/events?{q}")

    def to_odds_by_book(self, event: dict) -> dict[str, dict[str, dict[str, float]]]:
        """Converte um evento da API no formato interno do BETGSN."""
        return odds_event_to_internal(event)


def odds_event_to_internal(event: dict) -> dict[str, dict[str, dict[str, float]]]:
    """Converte um evento da The Odds API no formato interno do BETGSN.

    Funcao pura (nao usa chave nem rede) para ser reutilizada pelo Scanner
    e pelo importador de backtest — assim existe UM parser, nao dois que
    divergem com o tempo.

    Devolve {mercado: {casa: {resultado: odd}}}, com os rotulos de mercado
    e resultado do BETGSN.
    """
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    out: dict[str, dict[str, dict[str, float]]] = {}

    for book in event.get("bookmakers", []):
        book_name = book.get("title", book.get("key", "?"))
        for market in book.get("markets", []):
            mkey = market.get("key")
            if mkey == "h2h":
                target = out.setdefault("Resultado Final (1X2)", {})
                slot = target.setdefault(book_name, {})
                for oc in market.get("outcomes", []):
                    name = oc.get("name")
                    label = "1" if name == home else ("2" if name == away else "X")
                    slot[label] = float(oc["price"])
            elif mkey == "totals":
                target = out.setdefault("Total de Gols", {})
                slot = target.setdefault(book_name, {})
                for oc in market.get("outcomes", []):
                    point = oc.get("point")
                    if point is None:
                        continue
                    side = "Over" if oc.get("name") == "Over" else "Under"
                    slot[f"{side} {point}"] = float(oc["price"])
            elif mkey == "btts":
                target = out.setdefault("Ambas Marcam", {})
                slot = target.setdefault(book_name, {})
                for oc in market.get("outcomes", []):
                    yes = oc.get("name", "").lower() == "yes"
                    slot["BTTS Sim" if yes else "BTTS Nao"] = float(oc["price"])
    return out


# --------------------------------------------------------------------------
# API-Football
# --------------------------------------------------------------------------

APIFOOTBALL_BASE = "https://v3.football.api-sports.io"


@dataclass
class ApiFootballProvider:
    api_key: str

    @classmethod
    def from_env(cls) -> "ApiFootballProvider | None":
        key = os.environ.get("BETGSN_APIFOOTBALL_KEY", "").strip()
        return cls(key) if key else None

    def _headers(self) -> dict[str, str]:
        return {"x-apisports-key": self.api_key, "User-Agent": "BETGSN/1.0"}

    def fixtures(self, league: int, season: int, next_n: int = 20) -> list[dict]:
        q = urllib.parse.urlencode({"league": league, "season": season, "next": next_n})
        data = _get(f"{APIFOOTBALL_BASE}/fixtures?{q}", self._headers())
        return data.get("response", [])

    def fixtures_by_season(self, league: int, season: int) -> list[dict]:
        """Todas as partidas de uma temporada (passado + futuro).

        GET /fixtures?league=&season= — usado pelo importador de backtest.
        Cada item traz `fixture.date` em ISO 8601 COM offset, o que
        preserva o fuso verdadeiro da partida.
        """
        q = urllib.parse.urlencode({"league": league, "season": season})
        data = _get(f"{APIFOOTBALL_BASE}/fixtures?{q}", self._headers())
        return data.get("response", [])

    def fixture_statistics(self, fixture_id: int) -> list[dict]:
        """Estatisticas de uma partida: cantos, cartoes, chutes e xG.

        GET /fixtures/statistics?fixture= — devolve dois itens (um por
        time). Nem toda liga/temporada tem `expected_goals`; quando falta,
        o campo fica None em vez de virar zero.
        """
        q = urllib.parse.urlencode({"fixture": fixture_id})
        data = _get(f"{APIFOOTBALL_BASE}/fixtures/statistics?{q}", self._headers())
        return data.get("response", [])

    def team_statistics(self, league: int, season: int, team: int) -> dict:
        q = urllib.parse.urlencode({"league": league, "season": season, "team": team})
        data = _get(f"{APIFOOTBALL_BASE}/teams/statistics?{q}", self._headers())
        return data.get("response", {})

    def account_status(self) -> dict:
        """Plano, quota e validade da chave. NAO consome cota.

        GET /status — usado para conferir a configuracao antes de importar.
        A resposta traz `account`, `subscription` e `requests`.
        """
        return _get(f"{APIFOOTBALL_BASE}/status", self._headers())


# --------------------------------------------------------------------------
# Football-Data.org
# --------------------------------------------------------------------------

FOOTBALLDATA_BASE = "https://api.football-data.org/v4"


@dataclass
class FootballDataProvider:
    api_key: str

    @classmethod
    def from_env(cls) -> "FootballDataProvider | None":
        key = os.environ.get("BETGSN_FOOTBALLDATA_KEY", "").strip()
        return cls(key) if key else None

    def _headers(self) -> dict[str, str]:
        return {"X-Auth-Token": self.api_key, "User-Agent": "BETGSN/1.0"}

    def matches(self, competition: str = "BSA", status: str = "SCHEDULED") -> list[dict]:
        q = urllib.parse.urlencode({"status": status})
        data = _get(f"{FOOTBALLDATA_BASE}/competitions/{competition}/matches?{q}", self._headers())
        return data.get("matches", [])


# --------------------------------------------------------------------------
# Registro de providers disponiveis
# --------------------------------------------------------------------------


def available_providers() -> dict[str, bool]:
    """Quais providers tem chave configurada no ambiente ou no .env."""
    return {
        "The Odds API": OddsApiProvider.from_env() is not None,
        "API-Football": ApiFootballProvider.from_env() is not None,
        "Football-Data.org": FootballDataProvider.from_env() is not None,
    }
