"""BETGSN :: providers — camada de fontes de dados reais (opcional).

O app funciona offline com data.py. Este modulo permite plugar fontes
reais quando houver chave. Cada provider implementa a mesma interface e
falha de forma explicita (ProviderError) — nunca silenciosa.

Fontes suportadas:
  - The Odds API  (the-odds-api.com)   -> odds de varias casas, futebol
  - ParlayAPI                          -> odds multi-casa (endpoint configuravel)
  - API-Football  (api-football.com)   -> historico, estatisticas, xG
  - Football-Data (football-data.org)  -> resultados e tabelas

Chaves via variavel de ambiente OU arquivo .env na raiz do projeto:
  BETGSN_ODDS_API_KEY
  BETGSN_PARLAY_API_KEY   (+ BETGSN_PARLAY_API_BASE, sem ela o adapter fica inerte)
  BETGSN_APIFOOTBALL_KEY
  BETGSN_FOOTBALLDATA_KEY

Worktrees de agente: o `.env` fica no worktree principal do repositorio
(ex.: BETGSN) e nao e copiado para os worktrees (ex.: BETGSN-odds). Por
isso `env_file_candidates()` procura o `.env` local e, se este checkout
for um git worktree, tambem o `.env` do worktree principal.

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
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .envconfig import env_file_candidates, resolve_env_file
from .odds_registry import default_odds_registry

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

#: Variaveis lidas do ambiente ou do .env. Usado para documentar e validar.
ENV_KEYS = (
    "BETGSN_ODDS_API_KEY",
    "BETGSN_PARLAY_API_KEY",
    "BETGSN_APIFOOTBALL_KEY",
    "BETGSN_FOOTBALLDATA_KEY",
)

# --------------------------------------------------------------------------
# Classificacao de falhas — a base do fallback e do health check
# --------------------------------------------------------------------------

FAILURE_AUTH = "AUTH"
FAILURE_FORBIDDEN = "FORBIDDEN"
FAILURE_RATE_LIMIT = "RATE_LIMIT"
FAILURE_TIMEOUT = "TIMEOUT"
FAILURE_CONNECTION = "CONNECTION"
FAILURE_SERVER = "SERVER"
FAILURE_NO_CREDITS = "NO_CREDITS"
FAILURE_NO_COVERAGE = "NO_COVERAGE"
FAILURE_BAD_RESPONSE = "BAD_RESPONSE"
FAILURE_UNKNOWN = "UNKNOWN"

#: Falhas que nunca adiantam tentar de novo sem intervencao humana.
_HARD_FAILURES = frozenset({FAILURE_AUTH, FAILURE_FORBIDDEN, FAILURE_NO_CREDITS})


def classify_status(status: int) -> tuple[str, bool]:
    """(kind, retryable) para um status HTTP."""
    if status == 401:
        return FAILURE_AUTH, False
    if status == 403:
        return FAILURE_FORBIDDEN, False
    if status == 402:
        return FAILURE_NO_CREDITS, False
    if status == 429:
        return FAILURE_RATE_LIMIT, True
    if 500 <= status <= 599:
        return FAILURE_SERVER, True
    if 400 <= status <= 499:
        return FAILURE_BAD_RESPONSE, False
    return FAILURE_UNKNOWN, False


class ProviderError(RuntimeError):
    """Falha de provider. Sempre explicita: o app mostra, nao esconde.

    Carrega a classificacao da falha (`kind`, `retryable`, `status`,
    `retry_after`) para que a camada de fallback decida se tenta outro
    provider ou se para. A mensagem nunca contem credencial.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        kind: str = FAILURE_UNKNOWN,
        retryable: bool = False,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def hard(self) -> bool:
        return self.kind in _HARD_FAILURES


def _git_common_root() -> Optional[Path]:
    """Raiz do worktree principal quando este repo e um git worktree.

    Cada agente trabalha em um worktree proprio (ex.: BETGSN-odds) e as
    credenciais ficam centralizadas no `.env` do worktree principal
    (BETGSN). Sem isso, rodar a partir do worktree nao enxerga a chave.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(ENV_FILE.parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = (ENV_FILE.parent / common).resolve()
    if common.name == ".git":
        return common.parent
    return None


def _apply_env_file(target: Path, override: bool) -> int:
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


def load_env_file(path: Path | None = None, override: bool = False) -> int:
    """Carrega pares KEY=VALUE de um ou mais arquivos .env para os.environ.

    Parser minimo da biblioteca padrao: o nucleo do BETGSN nao tem
    dependencia externa e nao vale adicionar uma so para ler poucas linhas.

    Regras:
      - linhas em branco e comecando com '#' sao ignoradas;
      - `export KEY=VALUE` tambem e aceito;
      - aspas simples ou duplas em volta do valor sao removidas;
      - por padrao NAO sobrescreve variavel ja definida no ambiente real
        (o ambiente ganha do arquivo, que e o comportamento esperado).

    Sem `path`, carrega todos os candidatos de `env_file_candidates()` na
    ordem de prioridade. Devolve quantas variaveis foram definidas.
    """
    targets = [Path(path)] if path else env_file_candidates()
    applied = 0
    for target in targets:
        if target.exists():
            applied += _apply_env_file(target, override)
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
        kind, retryable = classify_status(e.code)
        raise ProviderError(
            f"HTTP {e.code} em {safe_url}: {body}",
            status=e.code,
            kind=kind,
            retryable=retryable,
            retry_after=_retry_after_seconds(getattr(e, "headers", None)),
        ) from e
    except urllib.error.URLError as e:
        reason = e.reason
        timeout_like = isinstance(reason, (TimeoutError, socket.timeout))
        kind = FAILURE_TIMEOUT if timeout_like else FAILURE_CONNECTION
        raise ProviderError(
            f"rede falhou em {safe_url}: {reason}",
            kind=kind,
            retryable=True,
        ) from e
    except (TimeoutError, socket.timeout) as e:
        raise ProviderError(
            f"timeout em {safe_url}: {e}",
            kind=FAILURE_TIMEOUT,
            retryable=True,
        ) from e
    except json.JSONDecodeError as e:
        raise ProviderError(
            f"resposta nao-JSON de {safe_url}: {e}",
            kind=FAILURE_BAD_RESPONSE,
            retryable=False,
        ) from e


def _retry_after_seconds(headers: Mapping[str, str] | None) -> Optional[float]:
    """Le Retry-After (segundos). Formato HTTP-date e ignorado com seguranca."""
    if not headers:
        return None
    for key, value in headers.items():
        if str(key).lower() == "retry-after":
            try:
                return max(0.0, float(str(value).strip()))
            except (TypeError, ValueError):
                return None
    return None


#: Teto de espera entre tentativas. Sem isso, um Retry-After enorme
#: travaria a coleta inteira.
MAX_RETRY_DELAY_SECONDS = 30.0


def request_json_with_retry(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    max_attempts: int = 2,
    backoff_seconds: float = 1.0,
    sleep=time.sleep,
) -> tuple[dict | list, dict[str, str]]:
    """GET com retry LIMITADO para falhas transitorias (429/5xx/rede).

    Nunca faz retry infinito: `max_attempts` e o teto absoluto. Falhas
    duras (401/403/402) sobem na hora — nao ha o que tentar de novo.
    """
    attempts = max(1, int(max_attempts))
    last: Optional[ProviderError] = None
    for attempt in range(1, attempts + 1):
        try:
            return _get_with_headers(url, headers, timeout)
        except ProviderError as exc:
            last = exc
            if not exc.retryable or attempt >= attempts:
                raise
            delay = exc.retry_after
            if delay is None:
                delay = backoff_seconds * (2 ** (attempt - 1))
            sleep(min(float(delay), MAX_RETRY_DELAY_SECONDS))
    assert last is not None
    raise last


def _get(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> dict | list:
    body, _ = _get_with_headers(url, headers, timeout)
    return body


def parse_credit_headers(headers: Mapping[str, str]) -> dict[str, int]:
    """Extrai a quota informada pela The Odds API (headers x-requests-*).

    Devolve so as chaves presentes e numericas. Nunca inventa saldo.
    """
    out: dict[str, int] = {}
    for name in ("x-requests-last", "x-requests-used", "x-requests-remaining"):
        value = None
        for key, raw in headers.items():
            if str(key).lower() == name:
                value = raw
                break
        if value is None:
            continue
        try:
            out[name.replace("x-requests-", "")] = int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return out


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

#: Divisoes do football-data.co.uk -> sport key da The Odds API.
#:
#: PROVENIENCIA: chaves verificadas contra `GET /v4/sports` da The Odds
#: API em 2026-09-21 (endpoint gratuito, sem custo de quota). Divisoes
#: cujo torneio NAO existe na lista do provider ficam FORA do mapa: sao
#: reportadas pela captura como nao-capturaveis — nunca adivisadas.
#: Revalidar apos mudancas de catalogo do provider (ex.: a 2.Bundesliga
#: e a Conference Nacional nao constavam da lista verificada).
DIVISION_TO_SPORT_KEY: dict[str, str] = {
    # main — Inglaterra
    "E0": "soccer_epl",
    "E1": "soccer_efl_champ",
    "E2": "soccer_england_league1",
    "E3": "soccer_england_league2",
    # main — Escocia (so a divisao principal tem sport key)
    "SC0": "soccer_spl",
    # main — resto da Europa
    "D1": "soccer_germany_bundesliga",
    "I1": "soccer_italy_serie_a",
    "I2": "soccer_italy_serie_b",
    "SP1": "soccer_spain_la_liga",
    "SP2": "soccer_spain_segunda_division",
    "F1": "soccer_france_ligue_one",
    "N1": "soccer_netherlands_eredivisie",
    "G1": "soccer_greece_super_league",
    # extras
    "BRA": "soccer_brazil_campeonato",
    "ARG": "soccer_argentina_primera_division",
    "AUT": "soccer_austria_bundesliga",
    "DNK": "soccer_denmark_superliga",
    "IRL": "soccer_league_of_ireland",
    "MEX": "soccer_mexico_ligamx",
    "NOR": "soccer_norway_eliteserien",
    "USA": "soccer_usa_mls",
}

#: Mapa reverso: sport key -> divisoes FDUK que ela cobre.
SPORT_KEY_TO_DIVISIONS: dict[str, list[str]] = {}
for _div, _sport in DIVISION_TO_SPORT_KEY.items():
    SPORT_KEY_TO_DIVISIONS.setdefault(_sport, []).append(_div)


def sport_keys_for_divisions(
    divisions: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Traduz divisoes FDUK para sport keys da The Odds API.

    Devolve `(sport_keys, divisoes_sem_mapeamento)`. Divisao sem sport key
    verificada NAO e inventada: entra na segunda lista para o chamador
    reportar. A ordem dos sport keys preserva a ordem de primeira
    aparicao das divisoes (deterministica para a mesma entrada).
    """
    sports: list[str] = []
    unmapped: list[str] = []
    for div in divisions:
        key = DIVISION_TO_SPORT_KEY.get(div)
        if key is None:
            if div not in unmapped:
                unmapped.append(div)
        elif key not in sports:
            sports.append(key)
    return sports, unmapped


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
    divergem com o tempo. Delega para `odds_normalize.grouped_from_event`.

    Devolve {mercado: {casa: {resultado: odd}}}, com os rotulos de mercado
    e resultado do BETGSN.
    """
    from .odds_normalize import grouped_from_event

    return grouped_from_event(event)


# --------------------------------------------------------------------------
# ParlayAPI (adapter configuravel)
# --------------------------------------------------------------------------

#: Base do endpoint de odds. NAO tem default: sem BETGSN_PARLAY_API_BASE o
#: provider fica inerte, porque inventar um dominio seria pior que nao ter
#: provider. A forma exata do payload deve ser confirmada na conta.
PARLAY_BASE_ENV = "BETGSN_PARLAY_API_BASE"
PARLAY_PATH_ENV = "BETGSN_PARLAY_ODDS_PATH"
PARLAY_DEFAULT_PATH = "/odds"


def _parlay_events(body: dict | list) -> list[dict]:
    """Extrai a lista de eventos de um payload ParlayAPI.

    Aceita uma lista direta ou os invólucros mais comuns (`data`, `events`,
    `odds`, `results`). Formato desconhecido vira erro explicito — o
    adapter nunca adivinha o significado dos dados.
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "events", "odds", "results"):
            value = body.get(key)
            if isinstance(value, list):
                return value
    raise ProviderError(
        "ParlayAPI devolveu um formato desconhecido; confirme o endpoint e "
        "o schema antes de usar.",
        kind=FAILURE_BAD_RESPONSE,
        retryable=False,
    )


@dataclass
class ParlayApiProvider:
    """Adapter ParlayAPI: odds multi-casa por esporte.

    Diferente da The Odds API, o contrato deste provider nao e publico e
    verificado. Por isso a base e o caminho sao configuraveis e o adapter
    so existe quando ambos a chave e a base estao definidos.
    """

    api_key: str
    base_url: str
    odds_path: str = PARLAY_DEFAULT_PATH

    name: str = "ParlayAPI"

    @classmethod
    def from_env(cls) -> "ParlayApiProvider | None":
        key = os.environ.get("BETGSN_PARLAY_API_KEY", "").strip()
        base = os.environ.get(PARLAY_BASE_ENV, "").strip()
        if not key or not base:
            return None
        path = os.environ.get(PARLAY_PATH_ENV, "").strip() or PARLAY_DEFAULT_PATH
        return cls(api_key=key, base_url=base.rstrip("/"), odds_path=path)

    def _url(self, sport_key: str, regions: str | None, markets: str | None) -> str:
        q: dict[str, str] = {"apiKey": self.api_key, "sport": sport_key}
        if markets:
            q["markets"] = markets
        if regions:
            q["regions"] = regions
        return f"{self.base_url}{self.odds_path}?{urllib.parse.urlencode(q)}"

    def live_odds_with_meta(
        self,
        sport_key: str,
        regions: str | None = None,
        markets: str | None = None,
        max_attempts: int = 2,
    ) -> tuple[list[dict], dict[str, str]]:
        """Eventos + headers de quota, no mesmo contrato da The Odds API."""
        body, headers = request_json_with_retry(
            self._url(sport_key, regions, markets),
            {"User-Agent": "BETGSN/1.0", "Accept": "application/json"},
            max_attempts=max_attempts,
        )
        return _parlay_events(body), headers

    def to_odds_by_book(self, event: dict) -> dict[str, dict[str, dict[str, float]]]:
        return odds_event_to_internal(event)


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
        "ParlayAPI": ParlayApiProvider.from_env() is not None,
        "API-Football": ApiFootballProvider.from_env() is not None,
        "Football-Data.org": FootballDataProvider.from_env() is not None,
    }


def configured_odds_providers() -> list[tuple[str, object]]:
    """Providers de odds configurados, na ordem de prioridade.

    Nunca levanta erro por falta de chave: devolve so o que esta pronto.
    Nenhum provider e obrigatorio para o funcionamento global.

    Delega ao registry padrao (`odds_registry`): a ordem e os labels
    continuam exatamente os de sempre ("The Odds API", "ParlayAPI") —
    este ponto e so a costura da migracao strangler da FASE B.
    """
    return default_odds_registry().available_providers()
