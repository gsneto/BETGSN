"""BETGSN :: providers — camada de fontes de dados reais (opcional).

O app funciona offline com data.py. Este modulo permite plugar fontes
reais quando houver chave. Cada provider implementa a mesma interface e
falha de forma explicita (ProviderError) — nunca silenciosa.

Fontes suportadas:
  - The Odds API  (the-odds-api.com)   -> odds de varias casas, futebol
  - ParlayAPI    (parlay-api.com)      -> odds multi-casa (GET /v1/sports/{key}/odds)
  - OddsPapi    (oddspapi.io)          -> odds multi-casa (API v4, REST)
  - Odds-API.io (api.odds-api.io)      -> odds de 365+ casas (API v3, REST)
  - OpticOdds   (api.opticodds.com)    -> odds multi-casa (API v3, REST)
  - API-Football  (api-football.com)   -> historico, estatisticas, xG
  - Football-Data (football-data.org)  -> resultados e tabelas

Chaves via variavel de ambiente OU arquivo .env na raiz do projeto:
  BETGSN_ODDS_API_KEY
  BETGSN_PARLAY_API_KEY   (+ BETGSN_PARLAY_API_BASE, sem ela o adapter fica inerte)
  BETGSN_ODDSPAPI_API_KEY    (ou ODDSPAPI_API_KEY)
  BETGSN_ODDS_API_IO_KEY     (ou ODDS_API_IO_KEY)
  BETGSN_OPTICODDS_API_KEY   (ou OPTICODDS_API_KEY)
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
import re
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
    "BETGSN_ODDSPAPI_API_KEY",
    "BETGSN_ODDS_API_IO_KEY",
    "BETGSN_OPTICODDS_API_KEY",
)


def _first_env(*names: str) -> str:
    """Primeira variavel de ambiente nao vazia entre `names` (nunca imprime).

    Providers novos aceitam o nome com prefixo BETGSN_ (padrao do projeto)
    E o nome sem prefixo usado pela documentacao do provider — a ordem
    define precedencia, e o valor nunca e exposto em mensagem de erro.
    """
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _epoch_to_utc(value: object) -> str:
    """Epoch (segundos, float aceito) -> chave canonica UTC `...Z`.

    Providers que carimbam odds em epoch (ex.: OpticOdds) precisam do
    mesmo formato string das demais fontes para as comparacoes temporais
    (pre_kickoff, dedupe). Valor nao numerico devolve "" — nunca 1970.
    """
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    from datetime import datetime, timezone

    try:
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_json(
    base_url: str,
    path: str,
    params: Mapping[str, object],
    headers: dict[str, str] | None = None,
    max_attempts: int = 2,
) -> tuple[dict | list, dict[str, str]]:
    """GET JSON com querystring (params repetiveis via listas) e retry limitado.

    Monta a URL com `urlencode(doseq=True)` — providers como OpticOdds
    exigem parametros repetidos (`sportsbook=A&sportsbook=B`). A chave
    nunca vaza: `redact_url` limpa na origem (query) e headers segredos
    nao entram em mensagem de erro.
    """
    query = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None and v != ""},  # type: ignore[arg-type]
        doseq=True,
    )
    url = f"{base_url}{path}" + (f"?{query}" if query else "")
    return request_json_with_retry(
        url, headers or {"User-Agent": "BETGSN/1.0"}, max_attempts=max_attempts
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


# --------------------------------------------------------------------------
# Contrato OddsProvider (FASE B): helpers compartilhados pelos adapters
# --------------------------------------------------------------------------

#: Mercados pedidos ao provider quando o pedido nao especifica nenhum.
#: E o default historico de `OddsApiProvider.markets`.
DEFAULT_ODDS_API_MARKETS = "h2h,totals,btts"

#: Rejeicao de mercado pela The Odds API. Duplicado de
#: `backtest_sources._UNSUPPORTED_RE` de proposito: a Task 5 decide se a
#: captura legada delega aqui (nao se pode tocar backtest_sources agora).
_UNSUPPORTED_MARKETS_RE = re.compile(
    r"Markets not supported by this endpoint:\s*([a-zA-Z0-9_,\s]+)"
)


def _unsupported_markets(message: str) -> list[str]:
    """Extrai os mercados rejeitados da mensagem de erro da API."""
    match = _UNSUPPORTED_MARKETS_RE.search(message)
    if not match:
        return []
    return [m.strip() for m in match.group(1).split(",") if m.strip()]


def _api_market_keys(markets: Iterable[str]) -> list[str]:
    """Rotulos internos -> chaves de mercado da Odds API (reverse MARKET_MAP).

    Rotulo sem mapeamento e ignorado: chutar uma chave parecida seria pior
    que pedir a menos.
    """
    from .odds_normalize import MARKET_MAP

    reverse = {label: key for key, label in MARKET_MAP.items()}
    keys: list[str] = []
    for label in markets:
        key = reverse.get(str(label).strip())
        if key is not None and key not in keys:
            keys.append(key)
    return keys


def _default_api_market_keys(provider) -> list[str]:
    """Mercados default: os do provider, ou o da familia de formato."""
    default = getattr(provider, "markets", None) or DEFAULT_ODDS_API_MARKETS
    return [m.strip() for m in default.split(",") if m.strip()]


def _fetch_dropping_unsupported(
    provider,
    sport_key: str,
    regions: Optional[str],
    api_keys: list[str],
) -> tuple[Optional[list[dict]], dict[str, str], list[str], Optional[ProviderError]]:
    """Busca odds removendo mercados nao suportados, um a um.

    Espelha `LiveOddsCapture._fetch_with_fallback` (backtest_sources):
    mesma regra, agora dentro do adapter do contrato. Devolve
    `(events, headers, errors, erro)` — `erro` None quando a busca deu
    certo; `events` None quando falhou (o erro e a falha real).
    """
    markets = [m.strip() for m in api_keys if m.strip()]
    errors: list[str] = []
    last_error: Optional[ProviderError] = None
    for _ in range(len(markets) + 1):
        if not markets:
            errors.append(f"{sport_key}: nenhum mercado valido restou")
            return None, {}, errors, last_error
        try:
            events, headers = provider.live_odds_with_meta(
                sport_key, regions=regions, markets=",".join(markets)
            )
            return events, headers, errors, None
        except ProviderError as exc:
            last_error = exc
            unsupported = _unsupported_markets(str(exc))
            if not unsupported:
                return None, {}, errors, exc
            for bad in unsupported:
                if bad in markets:
                    markets.remove(bad)
                    errors.append(
                        f"{sport_key}: mercado '{bad}' nao suportado neste "
                        f"endpoint — removido e tentando de novo"
                    )
    return None, {}, errors, last_error


def _fetch_odds_api_format(
    provider,
    request,
    name: str,
    snapshot_label: str,
):
    """`fetch_odds` compartilhado pela familia de formato Odds API.

    OddsApiProvider e ParlayApiProvider falam o mesmo formato (sport key
    + eventos no shape The Odds API + headers de quota), entao existem
    UM parser e UM fluxo de contrato — o que muda e o label do snapshot.

    Sem fabricacao: divisao sem sport key e ignorada; escopo sem
    divisao mapeada e `no_coverage` explicito; creditos ausentes ficam
    None; evento sem kickoff nao gera quote (regra do parser canonico).
    """
    from .odds_normalize import normalize_events
    from .odds_provider import OddsProviderFetch

    sport_keys, _unmapped = sport_keys_for_divisions(request.divisions)
    if not sport_keys:
        return OddsProviderFetch(no_coverage=True)

    api_keys = _api_market_keys(request.markets) or _default_api_market_keys(
        provider
    )
    regions = request.regions or getattr(provider, "regions", None)

    quotes: list = []
    raw_events: list[dict] = []
    errors: list[str] = []
    last_credits: dict[str, int] = {}
    for sport_key in sport_keys:
        events, headers, sport_errors, error = _fetch_dropping_unsupported(
            provider, sport_key, regions, api_keys
        )
        if error is not None:
            raise error  # falha alta: quem chama decide o fallback
        errors.extend(sport_errors)
        raw_events.extend(events)
        quotes.extend(
            normalize_events(events, name, request.fetched_at, sport_key=sport_key)
        )
        parsed = parse_credit_headers(headers)
        if parsed:
            last_credits = parsed  # fica com a medicao mais recente

    credits = None
    if last_credits:
        from .odds_provider import CreditUpdate

        credits = CreditUpdate(
            last=last_credits.get("last"),
            used=last_credits.get("used"),
            remaining=last_credits.get("remaining"),
        )
    return OddsProviderFetch(
        quotes=tuple(quotes),
        raw_events=tuple(raw_events),
        snapshot_provider=snapshot_label,
        credits=credits,
        errors=tuple(errors),
    )


def _estimated_odds_cost(provider, request) -> int:
    """Custo estimado: mercados x regioes por sport key; total e a soma.

    Espelha `odds_health.estimated_request_cost` (o modelo de orcamento
    local) sem inventar credito real — e estimativa ANTES da chamada.
    """
    from .odds_health import estimated_request_cost

    sport_keys, _unmapped = sport_keys_for_divisions(request.divisions)
    api_keys = _api_market_keys(request.markets) or _default_api_market_keys(
        provider
    )
    regions = request.regions or getattr(provider, "regions", "") or ""
    n_regions = len([r for r in regions.split(",") if r.strip()])
    return sum(
        estimated_request_cost(len(api_keys), n_regions) for _ in sport_keys
    )


@dataclass
class OddsApiProvider:
    api_key: str
    regions: str = "eu,uk"        # eu inclui Pinnacle; uk inclui casas gordas
    markets: str = "h2h,totals,btts"
    odds_format: str = "decimal"
    name: str = "The Odds API"

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

    # ------------------------------------------------ contrato (FASE B)

    def available(self) -> bool:
        """True: a instancia so existe com chave (ou construida a mao)."""
        return True

    def fetch_odds(self, request):
        """Busca odds pelo contrato provider-agnostic (`odds_provider`)."""
        return _fetch_odds_api_format(
            self, request, self.name, "the-odds-api-live"
        )

    def estimated_cost(self, request) -> int:
        return _estimated_odds_cost(self, request)

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return tuple(SPORT_KEY_TO_DIVISIONS.get(scope, ()))


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

#: Base do endpoint. NAO tem default: sem BETGSN_PARLAY_API_BASE o provider
#: fica inerte, porque inventar um dominio seria pior que nao ter provider.
PARLAY_BASE_ENV = "BETGSN_PARLAY_API_BASE"
PARLAY_PATH_ENV = "BETGSN_PARLAY_ODDS_PATH"

#: Rota oficial de odds (OpenAPI v3.2.0, verificado em 2026-09-23):
#: GET /v1/sports/{sport_key}/odds — o sport_key vai no PATH, nao na query.
#: O template permite override via BETGSN_PARLAY_ODDS_PATH sem reescrever
#: o adapter; o default e a rota documentada.
PARLAY_DEFAULT_PATH = "/v1/sports/{sport_key}/odds"


def _parlay_regions(regions: object) -> str:
    """Regions em formato canonico da API: string separada por virgula.

    Aceita str ("eu"), tuple (('eu',)) ou list (['eu', 'br']) — a
    serializacao bruta de um tuple pela urlencode produzia o lixo
    `('eu',)` (bug real: regions=%28%27eu%27%2C%29 e HTTP 404). Aqui o
    valor e sempre achatado para "eu,br"; None/string vazia devolve ""
    (parametro omitido, default da API).
    """
    if regions is None:
        return ""
    if isinstance(regions, str):
        return regions.strip()
    if isinstance(regions, (tuple, list)):
        return ",".join(str(r).strip() for r in regions if str(r).strip())
    return str(regions).strip()


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


def _parlay_inject_last_update(events: list[dict]) -> list[dict]:
    """Copia eventos carimbando o `last_update` REAL de cada bookmaker.

    A API (OpenAPI v3.2.0) devolve `last_update` POR BOOKMAKER — o
    instante real da ultima observacao de preco (price-change ou
    heartbeat de verificacao). Ele e propagado para cada outcome daquela
    casa como `timestamp`, porque e o horario DA OBSERVACAO: nunca e
    substituido por fetched_at. Casa sem `last_update` fica sem timestamp
    e segue a semantica do contrato (fallback para fetched_at).
    """
    out: list[dict] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("bookmakers"), list):
            out.append(event)
            continue
        books: list[dict] = []
        for book in event["bookmakers"]:
            if not isinstance(book, dict):
                books.append(book)
                continue
            last_update = str(book.get("last_update") or "").strip()
            if not last_update:
                books.append(book)
                continue
            new_book = dict(book)
            new_markets: list[dict] = []
            for market in book.get("markets") or []:
                if not isinstance(market, dict):
                    new_markets.append(market)
                    continue
                new_market = dict(market)
                new_market["outcomes"] = [
                    {**outcome, "timestamp": last_update}
                    if isinstance(outcome, dict) and not outcome.get("timestamp")
                    else outcome
                    for outcome in market.get("outcomes") or []
                ]
                new_markets.append(new_market)
            new_book["markets"] = new_markets
            books.append(new_book)
        out.append({**event, "bookmakers": books})
    return out


@dataclass
class ParlayApiProvider:
    """Adapter ParlayAPI (parlay-api.com): odds multi-casa por esporte.

    Endpoint oficial (OpenAPI v3.2.0, verificado em 2026-09-23):

        GET /v1/sports/{sport_key}/odds?regions=eu&markets=h2h
        Auth: header X-API-Key (recomendado; ?apiKey= e equivalente)

    O sport_key vai no PATH (nunca na query como `sport=`), as regions
    sao sempre string separada por virgula e o preco e pedido em
    DECIMAL. O `last_update` de cada bookmaker — horario REAL da
    observacao — vira o timestamp das quotes daquela casa (nunca o
    fetched_at).
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

    def _base(self) -> str:
        """Host raiz: tolera base terminando em /v1 sem duplicar o prefixo."""
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base

    def _url(self, sport_key: str, regions: object, markets: str | None) -> str:
        """URL da rota oficial: sport_key no PATH, sem credencial na query."""
        path = self.odds_path.format(
            sport_key=urllib.parse.quote(str(sport_key), safe="")
        )
        q: dict[str, str] = {"oddsFormat": "decimal"}
        if markets:
            q["markets"] = markets
        regions_param = _parlay_regions(regions)
        if regions_param:
            q["regions"] = regions_param
        return f"{self._base()}{path}?{urllib.parse.urlencode(q)}"

    def live_odds_with_meta(
        self,
        sport_key: str,
        regions: str | None = None,
        markets: str | None = None,
        max_attempts: int = 2,
    ) -> tuple[list[dict], dict[str, str]]:
        """Eventos + headers de quota, no mesmo contrato da The Odds API.

        Auth via header X-API-Key: a credencial NUNCA circula na URL
        (logs e mensagens de erro ficam limpos por construcao).
        """
        body, headers = request_json_with_retry(
            self._url(sport_key, regions, markets),
            {
                "User-Agent": "BETGSN/1.0",
                "Accept": "application/json",
                "X-API-Key": self.api_key,
            },
            max_attempts=max_attempts,
        )
        return _parlay_inject_last_update(_parlay_events(body)), headers

    def to_odds_by_book(self, event: dict) -> dict[str, dict[str, dict[str, float]]]:
        return odds_event_to_internal(event)

    # ------------------------------------------------ contrato (FASE B)

    def available(self) -> bool:
        """True: a instancia so existe com chave E base configuradas."""
        return True

    def fetch_odds(self, request):
        """Busca odds pelo contrato provider-agnostic (`odds_provider`).

        Mesma familia de formato da The Odds API (helper compartilhado);
        o label de snapshot e proprio porque ainda nao existe snapshot
        historico de Parlay — label novo, nunca o de outro provider.
        """
        return _fetch_odds_api_format(self, request, self.name, "parlayapi-live")

    def estimated_cost(self, request) -> int:
        return _estimated_odds_cost(self, request)

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return tuple(SPORT_KEY_TO_DIVISIONS.get(scope, ()))


# --------------------------------------------------------------------------
# OddsPapi (oddspapi.io — API v4)
# --------------------------------------------------------------------------

#: Doc oficial consultada em 2026-09-23: https://oddspapi.io/us/docs.
#: Host https://api.oddspapi.io, auth via query param `apiKey`.
ODDSPAPI_BASE = "https://api.oddspapi.io/v4"

#: sportId do futebol na OddsPapi (verificado nos exemplos oficiais da doc:
#: sportId 10 -> sportName "Soccer").
ODDSPAPI_SOCCER_ID = 10

#: marketId -> rotulo interno. PROVENIENCIA: catalogo oficial
#: `GET /v4/markets` da doc — 101 = "Full Time Result" (outcomes 1/X/2),
#: 104 = "Both Teams To Score" (Yes/No). Totais NAO tem id fixo (uma id
#: por linha): sao reconhecidos pelo padrao do `bookmakerOutcomeId`
#: "<linha>/over|under" (verificado no exemplo oficial de
#: /v4/odds-by-tournaments). Qualquer outro mercado e DESCARTADO.
_ODDSPAPI_MARKET_IDS: dict[str, str] = {
    "101": "h2h",
    "104": "btts",
}

#: outcomeId -> lado do 1X2 (catalogo oficial /v4/markets, market 101).
_ODDSPAPI_H2H_OUTCOMES: dict[str, str] = {"101": "home", "102": "draw", "103": "away"}

#: outcomeId -> Yes/No do BTTS (catalogo oficial /v4/markets, market 104).
_ODDSPAPI_BTTS_OUTCOMES: dict[str, str] = {"104": "yes", "105": "no"}

#: Padrao do bookmakerOutcomeId de totais: "<linha>/over|under".
_ODDSPAPI_TOTALS_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*/\s*(over|under)$", re.IGNORECASE)

#: divisao FDUK -> (categorySlug, tournamentSlug) esperados no catalogo
#: `GET /v4/tournaments`. PROVENIENCIA: england/premier-league e
#: spain/laliga verificados nos exemplos oficiais (2026-09-23); os demais
#: seguem a convensao de slug do catalogo e so geram chamada quando o
#: catalogo REAL da conta contem o par EXATO — slug que nao casa e
#: NO_COVERAGE honesto, nunca adivinhado (sem fuzzy).
DIVISION_TO_ODDSPAPI_TOURNAMENT: dict[str, tuple[str, str]] = {
    # main — Inglaterra
    "E0": ("england", "premier-league"),
    "E1": ("england", "championship"),
    "E2": ("england", "league-one"),
    "E3": ("england", "league-two"),
    # main — Escocia
    "SC0": ("scotland", "premiership"),
    # main — resto da Europa
    "D1": ("germany", "bundesliga"),
    "I1": ("italy", "serie-a"),
    "I2": ("italy", "serie-b"),
    "SP1": ("spain", "laliga"),
    "SP2": ("spain", "segunda-division"),
    "F1": ("france", "ligue-1"),
    "N1": ("netherlands", "eredivisie"),
    "G1": ("greece", "super-league"),
    # extras
    "BRA": ("brazil", "serie-a"),
    "ARG": ("argentina", "primera-division"),
}


#: Bookmakers consultados por captura. O endpoint odds-by-tournaments
#: exige EXATAMENTE UM bookmaker por request — comprovado pelo erro real
#: HTTP 400 ("Please provide exactly one bookmaker using the 'bookmaker'
#: query parameter") e pelo exemplo oficial da doc
#: (GET /v4/odds-by-tournaments?bookmaker=pinnacle&tournamentIds=17,8).
#: Multi-bookmaker = multiplas chamadas independentes, agregadas no
#: adapter. Default e o slug do exemplo oficial; override via env.
ODDSPAPI_BOOKMAKERS_ENV = "BETGSN_ODDSPAPI_BOOKMAKERS"
ODDSPAPI_DEFAULT_BOOKMAKERS = ("pinnacle",)


def _oddspapi_selected_bookmakers() -> tuple[str, ...]:
    """Slugs selecionados via env (default: o exemplo oficial da doc).

    Nao valida contra o catalogo (isso exigiria chamada HTTP): e a lista
    CANDIDATA — a validacao acontece no fetch, onde o catalogo ja foi
    lido. Slug inexistente gera erro explicito, nunca chamada inventada.
    """
    raw = os.environ.get(ODDSPAPI_BOOKMAKERS_ENV, "").strip()
    if not raw:
        return ODDSPAPI_DEFAULT_BOOKMAKERS
    slugs = tuple(s.strip() for s in raw.split(",") if s.strip())
    return slugs or ODDSPAPI_DEFAULT_BOOKMAKERS


@dataclass
class OddsPapiProvider:
    """Adapter OddsPapi (api.oddspapi.io/v4): odds multi-casa por liga.

    Fluxo (todos REST, sem WebSocket):
      1. `GET /v4/tournaments?sportId=10` — divisoes FDUK -> tournamentIds
         por slug EXATO (descoberta dinamica; sem casamento, no_coverage);
      2. `GET /v4/bookmakers` — slug -> nome real da casa (o bookmaker
         registrado e o que a API devolve, nunca uma lista fixa);
      3. `GET /v4/fixtures?tournamentId=..&statusId=0` por liga — eventos
         futuros COM nomes dos participantes;
      4. `GET /v4/odds-by-tournaments?bookmaker=<slug>&tournamentIds=..`
         — EXATAMENTE UM bookmaker por request (erro real 400 + doc
         oficial); multi-casa sao chamadas independentes agregadas no
         adapter (slugs de `BETGSN_ODDSPAPI_BOOKMAKERS`, validados no
         catalogo real);
      5. `GET /v4/account` (nao contabilizado) — quota real
         request_count/request_limit, quando a conta informa.

    Timestamp: cada outcome carrega `changedAt` REAL da fonte — e ele quem
    vira o timestamp da quote (nunca o fetched_at). Quota ausente e None.
    """

    api_key: str
    name: str = "OddsPapi"

    @classmethod
    def from_env(cls) -> "OddsPapiProvider | None":
        key = _first_env("BETGSN_ODDSPAPI_API_KEY", "ODDSPAPI_API_KEY")
        return cls(api_key=key) if key else None

    # ------------------------------------------------------------- http

    def _get(self, path: str, params: dict) -> tuple[dict | list, dict[str, str]]:
        query = dict(params)
        query.setdefault("apiKey", self.api_key)
        return _request_json(ODDSPAPI_BASE, path, query)

    # --------------------------------------------------------- descoberta

    def tournaments(self) -> list[dict]:
        body, _ = self._get("/tournaments", {"sportId": ODDSPAPI_SOCCER_ID})
        return body if isinstance(body, list) else []

    def bookmakers(self) -> list[dict]:
        body, _ = self._get("/bookmakers", {})
        return body if isinstance(body, list) else []

    def account(self) -> dict:
        body, _ = self._get("/account", {})
        return body if isinstance(body, dict) else {}

    def fixtures(self, tournament_id: int) -> list[dict]:
        body, _ = self._get(
            "/fixtures", {"tournamentId": tournament_id, "statusId": 0}
        )
        return body if isinstance(body, list) else []

    def odds_by_tournaments(
        self, tournament_ids: list[int], bookmaker: str
    ) -> list[dict]:
        """Odds dos eventos das ligas para UM bookmaker.

        Contrato REAL do endpoint (erro 400 em producao + doc oficial):
        `bookmaker` (singular) recebe EXATAMENTE UM slug por request;
        `tournamentIds` aceita lista separada por virgula. Multi-casa
        sao multiplas chamadas, agregadas pelo chamador do adapter.
        """
        body, _ = self._get(
            "/odds-by-tournaments",
            {
                "tournamentIds": ",".join(str(t) for t in tournament_ids),
                "bookmaker": str(bookmaker),
            },
        )
        return body if isinstance(body, list) else []

    # ------------------------------------------------------------- parse

    def _canonical_events(
        self, fixtures: list[dict], odds_fixtures: list[dict], bookmaker_names: dict[str, str]
    ) -> list[dict]:
        """Fixture + bookmakerOdds -> shape canonico do parser unico.

        Cada outcome nasce com `timestamp` = changedAt da FONTE (horario
        real da observacao); mercado sem mapeamento verificado e
        descartado; bookmaker suspenso/inativo e descartado.
        """
        odds_by_fixture = {
            str(f.get("fixtureId") or ""): f for f in odds_fixtures
        }
        events: list[dict] = []
        for fx in fixtures:
            fixture_id = str(fx.get("fixtureId") or "")
            odds_fx = odds_by_fixture.get(fixture_id)
            if odds_fx is None:
                continue
            home = str(fx.get("participant1Name") or "").strip()
            away = str(fx.get("participant2Name") or "").strip()
            start = str(fx.get("startTime") or "").strip()
            if not home or not away or not start:
                continue
            books: list[dict] = []
            bookmaker_odds = odds_fx.get("bookmakerOdds") or {}
            if not isinstance(bookmaker_odds, dict):
                continue
            for slug, block in sorted(bookmaker_odds.items()):
                if not isinstance(block, dict):
                    continue
                if block.get("bookmakerIsActive") is False or block.get("suspended") is True:
                    continue
                markets = self._canonical_markets(
                    block.get("markets") or {}, home, away
                )
                if not markets:
                    continue
                books.append(
                    {
                        "key": str(slug),
                        "title": bookmaker_names.get(str(slug), str(slug)),
                        "markets": markets,
                    }
                )
            if books:
                events.append(
                    {
                        "id": fixture_id,
                        "home_team": home,
                        "away_team": away,
                        "commence_time": start,
                        "league": str(fx.get("tournamentName") or ""),
                        "timestamp": str(fx.get("updatedAt") or ""),
                        "bookmakers": books,
                    }
                )
        return events

    @staticmethod
    def _canonical_markets(markets: dict, home: str, away: str) -> list[dict]:
        out: list[dict] = []
        for market_key, market in sorted(markets.items()):
            if not isinstance(market, dict):
                continue
            if market.get("marketActive") is False:
                continue
            api_market = _ODDSPAPI_MARKET_IDS.get(str(market_key))
            outcomes: list[dict] = []
            for outcome_key, outcome in sorted((market.get("outcomes") or {}).items()):
                if not isinstance(outcome, dict):
                    continue
                players = outcome.get("players") or {}
                player = players.get("0")
                if not isinstance(player, dict):
                    continue  # player-prop ou sem preco: fora do escopo
                if player.get("playerName"):
                    continue
                if player.get("active") is False:
                    continue
                if player.get("mainLine") is False:
                    continue  # linha alternativa quando a fonte marca a principal
                try:
                    price = float(player["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                changed_at = str(player.get("changedAt") or "").strip()
                outcome_name = OddsPapiProvider._outcome_name(
                    str(market_key),
                    str(outcome_key),
                    str(player.get("bookmakerOutcomeId") or ""),
                    home,
                    away,
                )
                if outcome_name is None:
                    continue
                entry = {"name": outcome_name, "price": price, "timestamp": changed_at}
                line = OddsPapiProvider._totals_line(
                    str(player.get("bookmakerOutcomeId") or "")
                )
                if line is not None:
                    entry["point"] = line
                    # mercado de totais reconhecido ESTRUTURALMENTE (padrao
                    # "<linha>/over|under"): a id varia por linha, o mapa
                    # fixo so cobre 1X2/BTTS
                    api_market = api_market or "totals"
                outcomes.append(entry)
            if api_market and outcomes:
                out.append({"key": api_market, "outcomes": outcomes})
        return out

    @staticmethod
    def _outcome_name(
        market_key: str, outcome_key: str, bookmaker_outcome_id: str, home: str, away: str
    ) -> str | None:
        """Nome canonico do outcome, ou None quando nao ha mapeamento seguro."""
        # 1X2 (market 101): lado pelo outcomeId do catalogo OU pelo
        # bookmakerOutcomeId "home"/"draw"/"away" (verificado na doc).
        if market_key == "101":
            side = _ODDSPAPI_H2H_OUTCOMES.get(outcome_key)
            if side is None:
                side = {
                    "home": "home",
                    "draw": "draw",
                    "away": "away",
                }.get(bookmaker_outcome_id.lower())
            if side == "home":
                return home
            if side == "away":
                return away
            if side == "draw":
                return "Draw"
            return None
        # BTTS (market 104): Yes/No pelo outcomeId do catalogo.
        if market_key == "104":
            side = _ODDSPAPI_BTTS_OUTCOMES.get(outcome_key)
            if side == "yes":
                return "Yes"
            if side == "no":
                return "No"
            lowered = bookmaker_outcome_id.lower()
            if lowered in ("yes", "no"):
                return lowered.capitalize()
            return None
        # Totais: padrao "<linha>/over|under" do bookmakerOutcomeId.
        match = _ODDSPAPI_TOTALS_RE.match(bookmaker_outcome_id)
        if match:
            return match.group(2).capitalize()
        return None

    @staticmethod
    def _totals_line(bookmaker_outcome_id: str) -> float | None:
        match = _ODDSPAPI_TOTALS_RE.match(bookmaker_outcome_id)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _credits_from_account(self) -> "CreditUpdate | None":
        """Quota REAL da conta (/v4/account, nao contabilizado). None se ausente."""
        try:
            account = self.account()
        except ProviderError:
            return None
        subscriptions = account.get("subscriptions")
        if not isinstance(subscriptions, list):
            return None
        from .odds_provider import CreditUpdate

        for sub in subscriptions:
            if not isinstance(sub, dict) or not sub.get("is_active"):
                continue
            try:
                used = int(sub["request_count"])
                limit = int(sub["request_limit"])
            except (KeyError, TypeError, ValueError):
                return None
            return CreditUpdate(used=used, remaining=max(0, limit - used))
        return None

    # ------------------------------------------------ contrato (FASE B)

    def available(self) -> bool:
        """True: a instancia so existe com chave."""
        return True

    def fetch_odds(self, request):
        from .odds_provider import OddsProviderFetch

        specs = [
            (div, DIVISION_TO_ODDSPAPI_TOURNAMENT[div])
            for div in request.divisions
            if div in DIVISION_TO_ODDSPAPI_TOURNAMENT
        ]
        if not specs:
            return OddsProviderFetch(no_coverage=True)

        # descoberta dinamica: divisao -> tournamentId por slug EXATO
        tournaments = self.tournaments()
        by_slug: dict[tuple[str, str], int] = {}
        for tournament in tournaments:
            if not isinstance(tournament, dict):
                continue
            try:
                by_slug[
                    (
                        str(tournament.get("categorySlug") or ""),
                        str(tournament.get("tournamentSlug") or ""),
                    )
                ] = int(tournament["tournamentId"])
            except (KeyError, TypeError, ValueError):
                continue

        errors: list[str] = []
        matched: list[tuple[str, int]] = []
        for division, spec in specs:
            tournament_id = by_slug.get(spec)
            if tournament_id is None:
                errors.append(
                    f"{division}: liga {spec[0]}/{spec[1]} nao encontrada no "
                    "catalogo OddsPapi — sem cobertura declarada"
                )
            else:
                matched.append((division, tournament_id))
        if not matched:
            return OddsProviderFetch(no_coverage=True, errors=tuple(errors))

        # slug -> nome REAL da casa (cobertura e o que a API devolve)
        name_by_slug = {
            str(b.get("slug") or ""): str(b.get("bookmakerName") or "")
            for b in self.bookmakers()
            if isinstance(b, dict)
        }
        name_by_slug = {k: v for k, v in name_by_slug.items() if k and v}

        # Bookmakers consultados: os selecionados (env/default) que EXISTEM
        # no catalogo real. Slug fora do catalogo e erro explicito — a casa
        # nao e inventada nem consultada às cegas.
        selected = _oddspapi_selected_bookmakers()
        for slug in selected:
            if slug not in name_by_slug:
                errors.append(
                    f"bookmaker '{slug}' nao existe no catalogo OddsPapi "
                    "(/v4/bookmakers) — nao consultado"
                )
        bookmakers_to_query = [s for s in selected if s in name_by_slug]
        if not bookmakers_to_query:
            return OddsProviderFetch(no_coverage=True, errors=tuple(errors))

        fixtures_by_division: list[tuple[str, list[dict]]] = []
        tournament_ids: list[int] = []
        for division, tournament_id in matched:
            fixtures_by_division.append((division, self.fixtures(tournament_id)))
            tournament_ids.append(tournament_id)

        # Uma chamada por bookmaker (exigencia do endpoint), agregando os
        # blocos bookmakerOdds por fixtureId DENTRO do adapter: o core
        # continua vendo apenas events multi-casa no shape do parser unico.
        # Falha de UM bookmaker nao derruba os demais — fica em `errors`,
        # nunca silenciosa; falha de TODOS sobe alta.
        odds_by_fixture: dict[str, dict] = {}
        last_error: ProviderError | None = None
        for slug in bookmakers_to_query:
            try:
                book_odds = self.odds_by_tournaments(tournament_ids, slug)
            except ProviderError as exc:
                last_error = exc
                errors.append(f"bookmaker {slug}: {exc}")
                continue
            for odds_fx in book_odds:
                if not isinstance(odds_fx, dict):
                    continue
                fixture_id = str(odds_fx.get("fixtureId") or "")
                if not fixture_id:
                    continue
                target = odds_by_fixture.setdefault(
                    fixture_id, {"fixtureId": fixture_id}
                )
                for key, value in odds_fx.items():
                    if key != "bookmakerOdds":
                        target.setdefault(key, value)
                block = odds_fx.get("bookmakerOdds")
                if isinstance(block, dict):
                    target.setdefault("bookmakerOdds", {}).update(block)
        if not odds_by_fixture and last_error is not None:
            raise last_error
        odds_fixtures = list(odds_by_fixture.values())

        from .odds_normalize import normalize_events

        quotes = []
        raw_events: list[dict] = []
        for division, division_fixtures in fixtures_by_division:
            events = self._canonical_events(
                division_fixtures, odds_fixtures, name_by_slug
            )
            raw_events.extend(events)
            quotes.extend(
                normalize_events(
                    events, self.name, request.fetched_at, sport_key=division
                )
            )
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(raw_events),
            snapshot_provider="oddspapi-live",
            credits=self._credits_from_account(),
            no_coverage=not quotes,
            errors=tuple(errors),
        )

    def estimated_cost(self, request) -> int:
        """Chamadas contabilizadas: tournaments + bookmakers + fixtures/liga
        + odds-by-tournaments (uma POR BOOKMAKER; account e livre)."""
        n_tournaments = len(
            [d for d in request.divisions if d in DIVISION_TO_ODDSPAPI_TOURNAMENT]
        )
        if n_tournaments == 0:
            return 0
        return 2 + n_tournaments + len(_oddspapi_selected_bookmakers())

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return tuple(
            d
            for d in SPORT_KEY_TO_DIVISIONS.get(scope, ())
            if d in DIVISION_TO_ODDSPAPI_TOURNAMENT
        )


# --------------------------------------------------------------------------
# Odds-API.io (api.odds-api.io — API v3, REST)
# --------------------------------------------------------------------------

#: Doc oficial consultada em 2026-09-23: https://docs.odds-api.io
#: (+ openapi.json). Host https://api.odds-api.io/v3, auth via `apiKey`.
#: WebSocket EXISTE mas nao e usado: REST primeiro, sem necessidade real
#: de stream no fluxo atual de captura pontual.
ODDS_API_IO_BASE = "https://api.odds-api.io/v3"

#: divisao FDUK -> (nome de liga, slug) esperados em `GET /v3/leagues`.
#: PROVENIENCIA: "England - Premier League" / "england-premier-league"
#: verificados no openapi oficial (2026-09-23); os demais seguem a
#: convensao de nome/slug do catalogo e so geram chamada quando o
#: catalogo REAL contem o par EXATO (nome OU slug) — sem fuzzy.
DIVISION_TO_ODDS_API_IO_LEAGUE: dict[str, tuple[str, str]] = {
    "E0": ("England - Premier League", "england-premier-league"),
    "E1": ("England - Championship", "england-championship"),
    "E2": ("England - League One", "england-league-one"),
    "E3": ("England - League Two", "england-league-two"),
    "SC0": ("Scotland - Premiership", "scotland-premiership"),
    "D1": ("Germany - Bundesliga", "germany-bundesliga"),
    "I1": ("Italy - Serie A", "italy-serie-a"),
    "I2": ("Italy - Serie B", "italy-serie-b"),
    "SP1": ("Spain - LaLiga", "spain-laliga"),
    "SP2": ("Spain - Segunda", "spain-segunda"),
    "F1": ("France - Ligue 1", "france-ligue-1"),
    "N1": ("Netherlands - Eredivisie", "netherlands-eredivisie"),
    "G1": ("Greece - Super League", "greece-super-league"),
    "BRA": ("Brazil - Serie A", "brazil-serie-a"),
    "ARG": ("Argentina - Liga Profesional", "argentina-liga-profesional"),
}

#: rotulo interno -> nome EXATO de mercado na Odds-API.io (doc oficial:
#: ML, Totals, Both Teams To Score, Spread — case-insensitive na API,
#: passado como na doc). Rotulo sem mapeamento e ignorado.
_ODDS_API_IO_MARKET_NAMES: dict[str, str] = {
    "h2h": "ML",
    "totals": "Totals",
    "btts": "Both Teams To Score",
    "spreads": "Spread",
}

#: Teto de eventos por liga num fetch: protege quota (odds/multi aceita
#: ate 10 eventos por chamada) sem loop agressivo.
ODDS_API_IO_MAX_EVENTS_PER_LEAGUE = 30


@dataclass
class OddsApiIoProvider:
    """Adapter Odds-API.io (api.odds-api.io/v3): 365+ bookmakers via REST.

    Fluxo:
      1. `GET /v3/leagues?sport=football` — divisao -> slug por par EXATO
         (nome OU slug do catalogo; sem casamento, no_coverage);
      2. casas da CONTA: override `BETGSN_ODDS_API_IO_BOOKMAKERS` ou
         `GET /v3/bookmakers/selected` — o plano define quais casas
         respondem, o adapter nao presume;
      3. `GET /v3/events?sport=football&league=<slug>&status=pending`
         por liga — eventos futuros;
      4. `GET /v3/odds/multi?eventIds=..` em lotes de ate 10 — 1 request
         por lote, qualquer quantidade de eventos.

    Timestamp: cada mercado carrega `updatedAt` REAL por casa — e ele o
    timestamp das quotes daquele bloco (nunca o fetched_at). A API nao
    informa quota na resposta: credits permanece None.
    """

    api_key: str
    bookmakers_env: str = ""
    name: str = "Odds-API.io"

    @classmethod
    def from_env(cls) -> "OddsApiIoProvider | None":
        key = _first_env("BETGSN_ODDS_API_IO_KEY", "ODDS_API_IO_KEY")
        if not key:
            return None
        books = _first_env("BETGSN_ODDS_API_IO_BOOKMAKERS")
        return cls(api_key=key, bookmakers_env=books)

    # ------------------------------------------------------------- http

    def _get(self, path: str, params: dict) -> tuple[dict | list, dict[str, str]]:
        query = dict(params)
        query.setdefault("apiKey", self.api_key)
        return _request_json(ODDS_API_IO_BASE, path, query)

    # --------------------------------------------------------- descoberta

    def leagues(self) -> list[dict]:
        body, _ = self._get("/leagues", {"sport": "football"})
        return body if isinstance(body, list) else []

    def selected_bookmakers(self) -> list[str]:
        """Casas selecionadas na CONTA (shape tolerante: lista de nomes ou
        de objetos com `name`). Nunca inventa casa fora da resposta."""
        if self.bookmakers_env:
            names = [b.strip() for b in self.bookmakers_env.split(",") if b.strip()]
            return names[:30]
        body, _ = self._get("/bookmakers/selected", {})
        if not isinstance(body, list):
            return []
        names: list[str] = []
        for item in body:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
        return names[:30]

    def events(self, league_slug: str) -> list[dict]:
        body, _ = self._get(
            "/events",
            {
                "sport": "football",
                "league": league_slug,
                "status": "pending",
                "limit": ODDS_API_IO_MAX_EVENTS_PER_LEAGUE,
            },
        )
        return body if isinstance(body, list) else []

    def odds_multi(self, event_ids: list[int], bookmakers: list[str], markets: list[str]) -> list[dict]:
        body, _ = self._get(
            "/odds/multi",
            {
                "eventIds": ",".join(str(i) for i in event_ids),
                "bookmakers": ",".join(bookmakers),
                "markets": ",".join(markets),
            },
        )
        return body if isinstance(body, list) else []

    # ------------------------------------------------------------- parse

    def _canonical_events(self, events_odds: list[dict]) -> list[dict]:
        """EventResponse -> shape canonico do parser unico.

        `bookmakers` vem como {casa: [mercado]} com `updatedAt` REAL por
        bloco de mercado — vira timestamp de cada outcome daquele bloco.
        Mercado sem mapeamento verificado e descartado.
        """
        out: list[dict] = []
        for event in events_odds:
            if not isinstance(event, dict):
                continue
            home = str(event.get("home") or "").strip()
            away = str(event.get("away") or "").strip()
            date = str(event.get("date") or "").strip()
            if not home or not away or not date:
                continue
            league = event.get("league") or {}
            books: list[dict] = []
            bookmakers = event.get("bookmakers") or {}
            if not isinstance(bookmakers, dict):
                continue
            for book_name in sorted(bookmakers):
                markets_out: list[dict] = []
                for market in bookmakers[book_name] or []:
                    if not isinstance(market, dict):
                        continue
                    api_market = self._api_market_key(str(market.get("name") or ""))
                    if api_market is None:
                        continue
                    updated_at = str(market.get("updatedAt") or "").strip()
                    outcomes = self._outcomes(
                        api_market, market.get("odds") or [], home, away, updated_at
                    )
                    if outcomes:
                        markets_out.append({"key": api_market, "outcomes": outcomes})
                if markets_out:
                    books.append({"key": book_name, "title": book_name, "markets": markets_out})
            if books:
                out.append(
                    {
                        "id": event.get("id"),
                        "home_team": home,
                        "away_team": away,
                        "commence_time": date,
                        "league": str(league.get("name") or "") if isinstance(league, dict) else "",
                        "bookmakers": books,
                    }
                )
        return out

    @staticmethod
    def _api_market_key(market_name: str) -> str | None:
        lowered = market_name.strip().lower()
        for api_key, name in _ODDS_API_IO_MARKET_NAMES.items():
            if name.lower() == lowered:
                return api_key
        return None

    @staticmethod
    def _outcomes(
        api_market: str, odds: list, home: str, away: str, updated_at: str
    ) -> list[dict]:
        out: list[dict] = []
        for entry in odds:
            if not isinstance(entry, dict):
                continue
            if api_market == "h2h":
                pairs = (
                    (home, entry.get("home")),
                    ("Draw", entry.get("draw")),
                    (away, entry.get("away")),
                )
            elif api_market == "btts":
                pairs = (
                    ("Yes", entry.get("yes")),
                    ("No", entry.get("no")),
                )
            elif api_market == "totals":
                pairs = (
                    ("Over", entry.get("over")),
                    ("Under", entry.get("under")),
                )
            elif api_market == "spreads":
                pairs = (
                    (home, entry.get("home")),
                    (away, entry.get("away")),
                )
            else:
                pairs = ()
            point = entry.get("max") if api_market == "totals" else entry.get("hdp")
            for name, raw_price in pairs:
                try:
                    price = float(raw_price)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                outcome = {"name": name, "price": price}
                if updated_at:
                    outcome["timestamp"] = updated_at
                if api_market in ("totals", "spreads") and point is not None:
                    try:
                        outcome["point"] = float(point)  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        continue
                out.append(outcome)
        return out

    # ------------------------------------------------ contrato (FASE B)

    def available(self) -> bool:
        """True: a instancia so existe com chave."""
        return True

    def fetch_odds(self, request):
        from .odds_normalize import normalize_events
        from .odds_provider import OddsProviderFetch

        specs = [
            (div, DIVISION_TO_ODDS_API_IO_LEAGUE[div])
            for div in request.divisions
            if div in DIVISION_TO_ODDS_API_IO_LEAGUE
        ]
        if not specs:
            return OddsProviderFetch(no_coverage=True)

        leagues = self.leagues()
        by_name: dict[str, str] = {}
        by_slug: dict[str, str] = {}
        for league in leagues:
            if not isinstance(league, dict):
                continue
            slug = str(league.get("slug") or "").strip()
            name = str(league.get("name") or "").strip()
            if slug:
                by_slug[slug] = slug
            if name and slug:
                by_name[name] = slug

        errors: list[str] = []
        matched: list[tuple[str, str]] = []
        for division, (name, slug) in specs:
            found = by_name.get(name) or by_slug.get(slug)
            if found is None:
                errors.append(
                    f"{division}: liga {name!r} ({slug}) nao encontrada no "
                    "catalogo Odds-API.io — sem cobertura declarada"
                )
            else:
                matched.append((division, found))
        if not matched:
            return OddsProviderFetch(no_coverage=True, errors=tuple(errors))

        bookmakers = self.selected_bookmakers()
        if not bookmakers:
            return OddsProviderFetch(
                no_coverage=True,
                errors=(
                    "Odds-API.io: nenhuma casa selecionada na conta "
                    "(override BETGSN_ODDS_API_IO_BOOKMAKERS vazio)",
                ),
            )

        market_names = [
            _ODDS_API_IO_MARKET_NAMES[label]
            for label in request.markets
            if label in _ODDS_API_IO_MARKET_NAMES
        ] or list(_ODDS_API_IO_MARKET_NAMES.values())

        quotes = []
        raw_events: list[dict] = []
        for division, league_slug in matched:
            events = self.events(league_slug)
            event_ids = [e.get("id") for e in events if isinstance(e, dict) and e.get("id")]
            if not event_ids:
                errors.append(f"{division}: nenhum evento futuro na liga")
                continue
            for start in range(0, len(event_ids), 10):
                batch = event_ids[start : start + 10]
                odds_events = self.odds_multi(batch, bookmakers, market_names)
                canonical = self._canonical_events(odds_events)
                raw_events.extend(canonical)
                quotes.extend(
                    normalize_events(
                        canonical, self.name, request.fetched_at, sport_key=division
                    )
                )
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(raw_events),
            snapshot_provider="odds-api-io-live",
            credits=None,  # quota nao vem na resposta: None, nunca inventada
            no_coverage=not quotes,
            errors=tuple(errors),
        )

    def estimated_cost(self, request) -> int:
        """Chamadas contabilizadas: leagues + bookmakers/selected (quando
        sem override) + events/liga + lotes de odds/multi."""
        n_leagues = len(
            [d for d in request.divisions if d in DIVISION_TO_ODDS_API_IO_LEAGUE]
        )
        if n_leagues == 0:
            return 0
        return (1 if not self.bookmakers_env else 0) + 1 + n_leagues + n_leagues

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return tuple(
            d
            for d in SPORT_KEY_TO_DIVISIONS.get(scope, ())
            if d in DIVISION_TO_ODDS_API_IO_LEAGUE
        )


# --------------------------------------------------------------------------
# OpticOdds (api.opticodds.com — API v3)
# --------------------------------------------------------------------------

#: Doc oficial consultada em 2026-09-23:
#: https://developer.opticodds.com (+ openapi das referencias).
#: Host https://api.opticodds.com/api/v3, auth via header X-Api-Key
#: (header, nao query, para o segredo nao circular em URL).
OPTICODDS_BASE = "https://api.opticodds.com/api/v3"

#: divisao FDUK -> nome de liga esperado em `GET /leagues?sport=soccer`.
#: PROVENIENCIA: "England - Premier League" (id england_-_premier_league)
#: verificado no exemplo oficial de /fixtures (2026-09-23); os demais
#: seguem a convensao de nome do catalogo e so geram chamada quando o
#: catalogo REAL contem o nome EXATO — sem fuzzy.
DIVISION_TO_OPTICODDS_LEAGUE: dict[str, str] = {
    "E0": "England - Premier League",
    "E1": "England - Championship",
    "E2": "England - League One",
    "E3": "England - League Two",
    "SC0": "Scotland - Premiership",
    "D1": "Germany - Bundesliga",
    "I1": "Italy - Serie A",
    "I2": "Italy - Serie B",
    "SP1": "Spain - LaLiga",
    "SP2": "Spain - Segunda",
    "F1": "France - Ligue 1",
    "N1": "Netherlands - Eredivisie",
    "G1": "Greece - Super League",
    "BRA": "Brazil - Serie A",
    "ARG": "Argentina - Liga Profesional",
}

#: market_id -> rotulo interno. PROVENIENCIA: "moneyline" verificado no
#: openapi oficial (/sports: market id "moneyline", name "Moneyline");
#: "total_goals"/"both_teams_to_goal" seguem a convensao de id por
#: esporte (total_runs/total_points nos exemplos) e so casam por
#: igualdade EXATA — id diferente e mercado DESCARTADO, nunca renomeado.
_OPTICODDS_MARKET_IDS: dict[str, str] = {
    "moneyline": "h2h",
    "total_goals": "totals",
    "both_teams_to_goal": "btts",
}

#: Cesta default de sportsbooks para PEDIR odds (a API exige 1-5 por
#: chamada). PROVENIENCIA: todos verificados ATIVOS no exemplo oficial de
#: GET /sportsbooks (2026-09-23) — Betano, Betnacional, Superbet,
#: Sportingbet, bet365. E uma cesta de REQUEST, nao alegacao de cobertura:
#: as casas registradas sao SEMPRE as que a resposta real devolver.
#: Override: BETGSN_OPTICODDS_SPORTSBOOKS (lista separada por virgula).
OPTICODDS_DEFAULT_SPORTSBOOKS: tuple[str, ...] = (
    "Betano",
    "Betnacional",
    "Superbet",
    "Sportingbet",
    "bet365",
)

#: /fixtures/odds aceita no maximo 5 fixture_ids e 5 sportsbooks por
#: chamada (limites oficiais da doc).
OPTICODDS_MAX_FIXTURES_PER_REQUEST = 5
OPTICODDS_MAX_SPORTSBOOKS_PER_REQUEST = 5


@dataclass
class OpticOddsProvider:
    """Adapter OpticOdds (api.opticodds.com/api/v3): odds multi-casa.

    Fluxo (REST; SSE streaming existe mas nao e necessario ao fluxo
    atual de captura pontual):
      1. `GET /leagues?sport=soccer` — divisao -> league id por nome
         EXATO (descoberta dinamica; sem casamento, no_coverage);
      2. `GET /fixtures/active?sport=soccer&league=<id>` por liga —
         jogos ativos (nunca completados);
      3. `GET /fixtures/odds?fixture_id=..&sportsbook=..` em lotes de
         ate 5 fixtures x 5 casas, `odds_format=DECIMAL` e `is_main=true`
         (linha principal; alternativas ficam de fora por design).

    Timestamp: cada odd carrega `timestamp` epoch REAL — convertido para
    chave canonica UTC e usado como timestamp da quote (nunca fetched_at).
    A resposta nao informa quota: credits permanece None.
    """

    api_key: str
    sportsbooks_env: str = ""
    name: str = "OpticOdds"

    @classmethod
    def from_env(cls) -> "OpticOddsProvider | None":
        key = _first_env("BETGSN_OPTICODDS_API_KEY", "OPTICODDS_API_KEY")
        if not key:
            return None
        books = _first_env("BETGSN_OPTICODDS_SPORTSBOOKS")
        return cls(api_key=key, sportsbooks_env=books)

    # ------------------------------------------------------------- http

    def _get(self, path: str, params: dict) -> tuple[dict | list, dict[str, str]]:
        headers = {"X-Api-Key": self.api_key, "User-Agent": "BETGSN/1.0"}
        return _request_json(OPTICODDS_BASE, path, params, headers=headers)

    # --------------------------------------------------------- descoberta

    def leagues(self) -> list[dict]:
        body, _ = self._get("/leagues", {"sport": "soccer"})
        data = body.get("data") if isinstance(body, dict) else body
        return data if isinstance(data, list) else []

    def active_fixtures(self, league_id: str) -> list[dict]:
        body, _ = self._get("/fixtures/active", {"sport": "soccer", "league": league_id})
        data = body.get("data") if isinstance(body, dict) else body
        return data if isinstance(data, list) else []

    def fixture_odds(self, fixture_ids: list[str], sportsbooks: list[str]) -> list[dict]:
        params: dict[str, object] = {
            "fixture_id": fixture_ids,
            "sportsbook": sportsbooks,
            "odds_format": "DECIMAL",
            "is_main": "true",
        }
        body, _ = self._get("/fixtures/odds", params)
        data = body.get("data") if isinstance(body, dict) else body
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------- parse

    def _sportsbooks(self) -> list[str]:
        if self.sportsbooks_env:
            names = [b.strip() for b in self.sportsbooks_env.split(",") if b.strip()]
        else:
            names = list(OPTICODDS_DEFAULT_SPORTSBOOKS)
        return names[:OPTICODDS_MAX_SPORTSBOOKS_PER_REQUEST]

    def _canonical_events(self, fixtures_with_odds: list[dict]) -> list[dict]:
        """FixtureWithOdds -> shape canonico do parser unico.

        Odd com timestamp epoch REAL -> chave UTC por outcome; mercado sem
        market_id mapeado e descartado; preco DECIMAL ja vem da chamada.
        """
        out: list[dict] = []
        for fixture in fixtures_with_odds:
            if not isinstance(fixture, dict):
                continue
            home = self._team_name(fixture, "home")
            away = self._team_name(fixture, "away")
            start = str(fixture.get("start_date") or "").strip()
            if not home or not away or not start:
                continue
            league = fixture.get("league") or {}
            # agrupa odds por sportsbook para o shape do parser unico
            by_book: dict[str, list[dict]] = {}
            for odd in fixture.get("odds") or []:
                if not isinstance(odd, dict):
                    continue
                api_market = _OPTICODDS_MARKET_IDS.get(
                    str(odd.get("market_id") or "")
                )
                if api_market is None:
                    continue
                outcome = self._outcome(api_market, odd)
                if outcome is None:
                    continue
                book = str(odd.get("sportsbook") or "").strip()
                if not book:
                    continue
                by_book.setdefault(book, {}).setdefault(api_market, []).append(outcome)
            books = [
                {"key": book, "title": book, "markets": [
                    {"key": market, "outcomes": outcomes}
                    for market, outcomes in sorted(markets.items())
                ]}
                for book, markets in sorted(by_book.items())
            ]
            if books:
                out.append(
                    {
                        "id": fixture.get("id"),
                        "home_team": home,
                        "away_team": away,
                        "commence_time": start,
                        "league": str(league.get("name") or "") if isinstance(league, dict) else "",
                        "bookmakers": books,
                    }
                )
        return out

    @staticmethod
    def _team_name(fixture: dict, side: str) -> str:
        display = str(fixture.get(f"{side}_team_display") or "").strip()
        if display:
            return display
        competitors = fixture.get(f"{side}_competitors") or []
        if competitors and isinstance(competitors[0], dict):
            return str(competitors[0].get("name") or "").strip()
        return ""

    @staticmethod
    def _outcome(api_market: str, odd: dict) -> dict | None:
        try:
            price = float(odd["price"])
        except (KeyError, TypeError, ValueError):
            return None
        timestamp = _epoch_to_utc(odd.get("timestamp"))
        if api_market in ("h2h", "btts"):
            name = str(odd.get("selection") or odd.get("name") or "").strip()
            if not name:
                return None
            outcome = {"name": name, "price": price}
            if timestamp:
                outcome["timestamp"] = timestamp
            return outcome
        # totals: lado vem do selection_line (over/under) + points (linha)
        side = str(odd.get("selection_line") or "").strip().lower()
        if side not in ("over", "under"):
            return None
        try:
            point = float(odd["points"])
        except (KeyError, TypeError, ValueError):
            return None
        outcome = {"name": side.capitalize(), "price": price, "point": point}
        if timestamp:
            outcome["timestamp"] = timestamp
        return outcome

    # ------------------------------------------------ contrato (FASE B)

    def available(self) -> bool:
        """True: a instancia so existe com chave."""
        return True

    def fetch_odds(self, request):
        from .odds_normalize import normalize_events
        from .odds_provider import OddsProviderFetch

        specs = [
            (div, DIVISION_TO_OPTICODDS_LEAGUE[div])
            for div in request.divisions
            if div in DIVISION_TO_OPTICODDS_LEAGUE
        ]
        if not specs:
            return OddsProviderFetch(no_coverage=True)

        leagues = self.leagues()
        id_by_name: dict[str, str] = {}
        for league in leagues:
            if not isinstance(league, dict):
                continue
            name = str(league.get("name") or "").strip()
            league_id = str(league.get("id") or "").strip()
            if name and league_id:
                id_by_name[name] = league_id

        errors: list[str] = []
        matched: list[tuple[str, str]] = []
        for division, league_name in specs:
            league_id = id_by_name.get(league_name)
            if league_id is None:
                errors.append(
                    f"{division}: liga {league_name!r} nao encontrada no "
                    "catalogo OpticOdds — sem cobertura declarada"
                )
            else:
                matched.append((division, league_id))
        if not matched:
            return OddsProviderFetch(no_coverage=True, errors=tuple(errors))

        sportsbooks = self._sportsbooks()
        quotes = []
        raw_events: list[dict] = []
        for division, league_id in matched:
            fixtures = [
                f
                for f in self.active_fixtures(league_id)
                if isinstance(f, dict) and str(f.get("status") or "") == "unplayed"
            ]
            fixture_ids = [str(f.get("id") or "") for f in fixtures if f.get("id")]
            if not fixture_ids:
                errors.append(f"{division}: nenhum jogo ativo com odds na liga")
                continue
            for start in range(0, len(fixture_ids), OPTICODDS_MAX_FIXTURES_PER_REQUEST):
                batch = fixture_ids[start : start + OPTICODDS_MAX_FIXTURES_PER_REQUEST]
                odds_fixtures = self.fixture_odds(batch, sportsbooks)
                canonical = self._canonical_events(odds_fixtures)
                raw_events.extend(canonical)
                quotes.extend(
                    normalize_events(
                        canonical, self.name, request.fetched_at, sport_key=division
                    )
                )
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(raw_events),
            snapshot_provider="opticodds-live",
            credits=None,  # quota nao vem na resposta: None, nunca inventada
            no_coverage=not quotes,
            errors=tuple(errors),
        )

    def estimated_cost(self, request) -> int:
        """Chamadas contabilizadas: leagues + fixtures/liga + lotes de
        fixtures/odds (5 fixtures por chamada)."""
        n_leagues = len(
            [d for d in request.divisions if d in DIVISION_TO_OPTICODDS_LEAGUE]
        )
        if n_leagues == 0:
            return 0
        return 1 + n_leagues + n_leagues

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return tuple(
            d
            for d in SPORT_KEY_TO_DIVISIONS.get(scope, ())
            if d in DIVISION_TO_OPTICODDS_LEAGUE
        )


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
    """Quais providers tem chave configurada no ambiente ou no .env.

    Providers opcionais sem chave aparecem como False: o registro existe
    (a API e o frontend listam o provider como "nao configurado"), mas a
    fabrica devolve None e nenhuma chamada acontece.
    """
    return {
        "The Odds API": OddsApiProvider.from_env() is not None,
        "ParlayAPI": ParlayApiProvider.from_env() is not None,
        "OddsPapi": OddsPapiProvider.from_env() is not None,
        "Odds-API.io": OddsApiIoProvider.from_env() is not None,
        "OpticOdds": OpticOddsProvider.from_env() is not None,
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
