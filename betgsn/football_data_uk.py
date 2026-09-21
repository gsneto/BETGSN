"""BETGSN :: football_data_uk — importador dos CSVs historicos.

Fonte: football-data.co.uk (Joseph Buchdahl). Arquivos CSV publicos com
resultados, estatisticas de partida e ODDS REAIS de varios bookmakers,
incluindo a Pinnacle — a linha de fechamento mais eficiente que existe no
mercado de futebol.

Por que esta fonte importa
--------------------------
Sem odds reais o backtest so mede o modelo contra um baseline sintetico,
o que infla o resultado. Com a Pinnacle de fechamento o teste passa a ser
o de verdade: *o modelo bate a linha mais afiada do mercado?* Quase
ninguem bate. E isso que se quer saber.

Estrutura dos arquivos
----------------------
Ligas principais (mmz4281/{temporada}/{div}.csv):
    ~120 colunas. 1X2 (abertura e fechamento), Over/Under 2.5, Handicap
    Asiatico, cantos, cartoes, chutes, arbitro, publico.

Ligas extras (new/{codigo}.csv):
    ~25 colunas. Apenas 1X2 (abertura e fechamento). Sem estatisticas.

Uso e licenca
-------------
O site libera uso por pessoas fisicas para analise pessoal e proibe
redistribuicao comercial e coleta automatizada agressiva. Este modulo
baixa com rate limit, User-Agent identificado e cache em disco (nao
rebaixa o que ja tem). Nao redistribua os CSVs nem os use para treinar
modelo que voce va vender.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .backtest_data import HistoricalCorpus
from .model import HistoricalMatch
from .timeutil import utc_key

CACHE_ROOT = Path(__file__).resolve().parent.parent / "output" / "football_data_uk"
MAIN_DIR = CACHE_ROOT / "main"
EXTRA_DIR = CACHE_ROOT / "extra"
FIXTURES_DIR = CACHE_ROOT / "fixtures"

BASE_MAIN = "https://www.football-data.co.uk/mmz4281"
BASE_EXTRA = "https://www.football-data.co.uk/new"
#: Jogos FUTUROS com odds reais. Atualizados as sextas (fim de semana) e
#: as tercas (meio de semana) pelo mantenedor do site.
URL_FIXTURES_MAIN = "https://www.football-data.co.uk/fixtures.csv"
URL_FIXTURES_EXTRA = "https://www.football-data.co.uk/new_league_fixtures.csv"

USER_AGENT = "BETGSN/1.1 (analise pessoal de futebol; uso nao comercial)"

#: Intervalo entre requisicoes. O site e mantido por uma pessoa; baixar
#: centenas de arquivos sem pausa seria abuso.
REQUEST_DELAY_SECONDS = 0.6


class FootballDataError(RuntimeError):
    """Falha ao baixar ou interpretar os CSVs."""


#: Padroes de credencial que podem escapar em mensagens de terceiros.
_CREDENTIAL_RE = re.compile(r"(api_?key\s*[=:]\s*)[^\s&\"',}]+", re.IGNORECASE)


def redact_text(text: str) -> str:
    """Substitui credenciais por *** em texto livre antes de gravar em disco."""
    return _CREDENTIAL_RE.sub(r"\1***", text)


def _append_manifest(manifest_path: Path, kind: str, report: Any) -> None:
    """Registra a operacao no manifesto do cache (auditoria).

    O manifesto e um arquivo em disco, entao o payload passa por
    sanitizacao: mensagem de erro de terceiros pode conter a URL com a
    chave de API. Defesa em profundidade — `providers.redact_url` ja limpa
    na origem.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        current = {"imports": []}
    entry = report.to_json() if hasattr(report, "to_json") else dict(report)
    entry["kind"] = kind
    entry["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = json.loads(redact_text(json.dumps(entry, ensure_ascii=False)))
    current.setdefault("imports", []).append(entry)
    manifest_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def read_manifest(manifest_path: Path | None = None) -> dict[str, Any]:
    """Le o manifesto de importacoes do cache."""
    path = Path(manifest_path) if manifest_path else CACHE_ROOT / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"imports": []}


# --------------------------------------------------------------------------
# Catalogo de ligas
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class League:
    code: str
    name: str
    country: str
    timezone: str
    has_stats: bool
    has_ou: bool
    has_ah: bool


#: Divisões principais. `has_stats` indica se o arquivo traz cantos/cartoes.
MAIN_DIVISIONS: dict[str, League] = {
    "E0": League("E0", "Premier League", "England", "Europe/London", True, True, True),
    "E1": League("E1", "Championship", "England", "Europe/London", True, True, True),
    "E2": League("E2", "League One", "England", "Europe/London", True, True, True),
    "E3": League("E3", "League Two", "England", "Europe/London", True, True, True),
    "EC": League("EC", "Conference", "England", "Europe/London", True, True, True),
    "SC0": League("SC0", "Premiership", "Scotland", "Europe/London", True, True, True),
    "SC1": League("SC1", "Championship", "Scotland", "Europe/London", True, True, True),
    "SC2": League("SC2", "League One", "Scotland", "Europe/London", True, True, True),
    "SC3": League("SC3", "League Two", "Scotland", "Europe/London", True, True, True),
    "D1": League("D1", "Bundesliga", "Germany", "Europe/Berlin", True, True, True),
    "D2": League("D2", "Bundesliga 2", "Germany", "Europe/Berlin", True, True, True),
    "I1": League("I1", "Serie A", "Italy", "Europe/Rome", True, True, True),
    "I2": League("I2", "Serie B", "Italy", "Europe/Rome", True, True, True),
    "SP1": League("SP1", "La Liga", "Spain", "Europe/Madrid", True, True, True),
    "SP2": League("SP2", "Segunda División", "Spain", "Europe/Madrid", True, True, True),
    "F1": League("F1", "Ligue 1", "France", "Europe/Paris", True, True, True),
    "F2": League("F2", "Ligue 2", "France", "Europe/Paris", True, True, True),
    "N1": League("N1", "Eredivisie", "Netherlands", "Europe/Amsterdam", True, True, True),
    "B1": League("B1", "Jupiler League", "Belgium", "Europe/Brussels", True, True, True),
    "P1": League("P1", "Primeira Liga", "Portugal", "Europe/Lisbon", True, True, True),
    "T1": League("T1", "Süper Lig", "Turkey", "Europe/Istanbul", True, True, True),
    "G1": League("G1", "Super League", "Greece", "Europe/Athens", True, True, True),
}

#: Ligas extras: só 1X2, sem estatísticas.
EXTRA_LEAGUES: dict[str, League] = {
    "BRA": League("BRA", "Serie A", "Brazil", "America/Sao_Paulo", False, False, False),
    "ARG": League("ARG", "Primera División", "Argentina", "America/Argentina/Buenos_Aires", False, False, False),
    "AUT": League("AUT", "Bundesliga", "Austria", "Europe/Vienna", False, False, False),
    "CHN": League("CHN", "Super League", "China", "Asia/Shanghai", False, False, False),
    "DNK": League("DNK", "Superliga", "Denmark", "Europe/Copenhagen", False, False, False),
    "FIN": League("FIN", "Veikkausliiga", "Finland", "Europe/Helsinki", False, False, False),
    "IRL": League("IRL", "Premier Division", "Ireland", "Europe/Dublin", False, False, False),
    "JPN": League("JPN", "J1 League", "Japan", "Asia/Tokyo", False, False, False),
    "MEX": League("MEX", "Liga MX", "Mexico", "America/Mexico_City", False, False, False),
    "NOR": League("NOR", "Eliteserien", "Norway", "Europe/Oslo", False, False, False),
    "POL": League("POL", "Ekstraklasa", "Poland", "Europe/Warsaw", False, False, False),
    "ROU": League("ROU", "Liga I", "Romania", "Europe/Bucharest", False, False, False),
    "RUS": League("RUS", "Premier League", "Russia", "Europe/Moscow", False, False, False),
    "SWE": League("SWE", "Allsvenskan", "Sweden", "Europe/Stockholm", False, False, False),
    "SWZ": League("SWZ", "Super League", "Switzerland", "Europe/Zurich", False, False, False),
    "USA": League("USA", "MLS", "USA", "America/New_York", False, False, False),
}

ALL_LEAGUES: dict[str, League] = {**MAIN_DIVISIONS, **EXTRA_LEAGUES}


def league_label(code: str) -> str:
    """Rotulo unico de liga usado no corpus (ex.: 'Premier League (England)')."""
    lg = ALL_LEAGUES.get(code)
    return f"{lg.name} ({lg.country})" if lg else code


def season_codes(start_year: int, end_year: int) -> list[str]:
    """2000 -> '0001', 2024 -> '2425'."""
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(start_year, end_year + 1)]


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

#: Nomes de bookmaker no CSV -> rotulo interno (mercado 1X2).
BOOKS_1X2 = {
    "PS": "Pinnacle",
    "PH": "Pinnacle",
    "B365": "Bet365",
    "BFE": "Betfair Exchange",
    "BF": "Betfair",
    "BFD": "Betfred",
    "BV": "BetVictor",
    "BW": "Bet&Win",
    "PP": "Paddy Power",
    "SKB": "SkyBet",
    "1XB": "1xBet",
    "IW": "Interwetten",
    "WH": "William Hill",
    "VC": "VC Bet",
    "LB": "Ladbrokes",
    "SJ": "Stan James",
    "SB": "Sportingbet",
    "GB": "Gamebookers",
    "BS": "Blue Square",
    "SO": "Sporting Odds",
    "SY": "Stanleybet",
    "BB": "Bet&Bragg",
}

#: No arquivo de JOGOS FUTUROS os prefixos sao outros: `PP` e Paddy Power
#: (nao Pinnacle), e a Pinnacle aparece como `PS`/`PH`. Estes dois
#: dicionarios existem para nao confundir as duas fontes.
BOOKS_1X2_FIXTURES = {
    "B365": "Bet365",
    "BFD": "Betfred",
    "BV": "BetVictor",
    "BW": "Bet&Win",
    "PP": "Paddy Power",
    "SKB": "SkyBet",
    "BFE": "Betfair Exchange",
    "PS": "Pinnacle",
    "PH": "Pinnacle",
    "WH": "William Hill",
    "LB": "Ladbrokes",
    "SY": "Stanleybet",
    "SB": "Sportingbet",
}

#: Prefixo do bookmaker em Over/Under e Handicap. A Pinnacle usa `P` nesses
#: mercados e `PS` no 1X2 — misturar os dois foi um bug real que deixava a
#: Pinnacle fora de over/under e handicap.
BOOKS_OU_AH = {
    "P": "Pinnacle",
    "B365": "Bet365",
    "BFE": "Betfair Exchange",
    "BF": "Betfair",
    "1XB": "1xBet",
    "IW": "Interwetten",
    "WH": "William Hill",
    "VC": "VC Bet",
    "LB": "Ladbrokes",
    "SJ": "Stan James",
    "SB": "Sportingbet",
    "SO": "Sporting Odds",
}

#: Colunas derivadas (nao sao casas). Ficam FORA da lista de bookmakers:
#: incluir "Max"/"Avg" como se fossem casas enviesa a mediana do consenso e
#: infla a contagem de casas no sinal.
AGGREGATE_PREFIXES = ("Max", "Avg")


@lru_cache(maxsize=16384)
def _norm_team(name: str) -> str:
    """Chave de comparacao de time (sem acento, pontuacao ou sufixo).

    Memoizada: chamada centenas de milhares de vezes ao indexar o cache e
    o conjunto de nomes de time e pequeno e estavel.
    """
    raw = unicodedata.normalize("NFKD", name or "")
    raw = "".join(c for c in raw if not unicodedata.combining(c)).lower()
    raw = re.sub(r"\b(fc|sc|cf|ac|ec|afc|cd|club|clube|futebol|football|sport)\b", " ", raw)
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _f(value: Any) -> float | None:
    """Converte uma ODD para float. Vazio/invalido/<=1 vira ausente.

    Odd decimal e sempre > 1.0. Usar esta funcao para a linha do handicap
    seria errado (a linha pode ser -1, 0 ou +0.5) — para isso existe
    `_line`.

    Evita `str().strip()`: os valores do CSV ja sao strings, e esta funcao
    e chamada dezenas de milhoes de vezes ao indexar o cache inteiro.
    """
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 1.0 else None


def _line(value: Any) -> float | None:
    """Converte a LINHA de handicap. Aceita negativo, zero e fracionario."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


#: Cache de datas e de nomes de time. Ambos sao funcoes puras com espaco
#: de entrada pequeno e altissima repeticao (centenas de milhares de
#: chamadas por importacao), entao memoizar corta a maior parte do custo
#: de parse dos CSVs.
@lru_cache(maxsize=8192)
def parse_date(value: str, default_year: int | None = None) -> str | None:
    """Aceita dd/mm/yy e dd/mm/yyyy. Devolve YYYY-MM-DD."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _build_odds_1x2(row: dict[str, str], closing: bool) -> dict[str, dict[str, float]]:
    """Extrai 1X2 de todos os bookmakers disponiveis na linha."""
    suffix = "C" if closing else ""
    out: dict[str, dict[str, float]] = {}
    for prefix, book in BOOKS_1X2.items():
        h = _f(row.get(f"{prefix}{suffix}H"))
        d = _f(row.get(f"{prefix}{suffix}D"))
        a = _f(row.get(f"{prefix}{suffix}A"))
        if h and d and a:
            out[book] = {"1": h, "X": d, "2": a}
    return out


def _build_odds_ou(row: dict[str, str], closing: bool) -> dict[str, dict[str, float]]:
    """Over/Under 2.5 (colunas '>2.5' / '<2.5')."""
    suffix = "C" if closing else ""
    out: dict[str, dict[str, float]] = {}
    for prefix, book in BOOKS_OU_AH.items():
        over = _f(row.get(f"{prefix}{suffix}>2.5"))
        under = _f(row.get(f"{prefix}{suffix}<2.5"))
        if over and under:
            out[book] = {"Over 2.5": over, "Under 2.5": under}
    return out


def _build_odds_ah(
    row: dict[str, str], closing: bool, line: float | None
) -> dict[str, dict[str, float]]:
    """Handicap asiatico. A linha vem em AHh (abertura) / AHCh (fechamento)."""
    if line is None:
        return {}
    suffix = "C" if closing else ""
    out: dict[str, dict[str, float]] = {}
    for prefix, book in BOOKS_OU_AH.items():
        home = _f(row.get(f"{prefix}{suffix}AHH"))
        away = _f(row.get(f"{prefix}{suffix}AHA"))
        if home and away:
            # rotulo casa com markets.market_asian_handicap
            out[book] = {
                f"AH Casa {line:+g}": home,
                f"AH Fora {-line:+g}": away,
            }
    return out


def market_best_odd(
    row: dict[str, str], closing: bool
) -> dict[str, dict[str, float]]:
    """Melhor odd e media do mercado, como mercados auxiliares.

    Nao entram como bookmakers (isso enviesaria o consenso), mas ficam
    registrados: sao a referencia de "preco maximo disponivel".
    """
    suffix = "C" if closing else ""
    out: dict[str, dict[str, float]] = {}
    best: dict[str, float] = {}
    avg: dict[str, float] = {}
    for col, label in (("H", "1"), ("D", "X"), ("A", "2")):
        b = _f(row.get(f"Max{suffix}{col}"))
        a = _f(row.get(f"Avg{suffix}{col}"))
        if b:
            best[label] = b
        if a:
            avg[label] = a
    if len(best) == 3:
        out["Melhor do mercado"] = best
    if len(avg) == 3:
        out["Media do mercado"] = avg
    return out


@dataclass(frozen=True)
class UpcomingFixture:
    """Um jogo FUTURO com odds reais, pronto para gerar sinais.

    Diferente de `CsvMatch`, nao tem placar: e um jogo que ainda nao
    aconteceu. E a fonte que a tela SINAIS deve usar em vez do dataset
    sintetico.
    """

    division: str
    league: str
    date: str            # YYYY-MM-DD
    time: str            # HH:MM
    timezone: str
    home: str
    away: str
    referee: str = ""
    #: {mercado: {casa: {resultado: odd}}} — odds reais de abertura
    odds: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    #: melhor odd por resultado (o que voce de fato consegue)
    best_odds: dict[str, dict[str, float]] = field(default_factory=dict)
    #: casa que oferece cada melhor odd
    best_books: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def kickoff(self) -> str:
        return f"{self.date} {self.time or '00:00'}"

    @property
    def match(self) -> str:
        return f"{self.home} vs {self.away}"

    @property
    def event_key(self) -> str:
        """Chave canonica de evento independente de provider.

        E exatamente `odds_normalize.event_key(mandante, visitante,
        kickoff_utc)`: a mesma chave gravada como `match_key` nas
        observacoes de odds. O kickoff entra convertido para UTC com o fuso
        da liga, preservando a identidade temporal da partida — sem isso,
        escrita e leitura apontariam para jogos diferentes.
        """
        from .odds_normalize import event_key
        from .timeutil import utc_key

        return event_key(self.home, self.away, utc_key(self.kickoff, self.timezone))

    @property
    def n_books(self) -> int:
        """Casas distintas com odds de 1X2."""
        return len(self.odds.get("Resultado Final (1X2)", {}))

    @property
    def has_odds(self) -> bool:
        return bool(self.odds)


def _best_odds(
    books: dict[str, dict[str, float]],
) -> tuple[dict[str, float], dict[str, str]]:
    """Melhor odd e a casa responsavel, por resultado."""
    best: dict[str, float] = {}
    who: dict[str, str] = {}
    for book, outcomes in books.items():
        for oc, odd in outcomes.items():
            if odd and (oc not in best or odd > best[oc]):
                best[oc] = odd
                who[oc] = book
    return best, who


def _build_fixture_odds(row: dict[str, str]) -> dict[str, dict[str, dict[str, float]]]:
    """Extrai 1X2, Over/Under 2.5 e handicap das colunas de jogo futuro."""
    out: dict[str, dict[str, dict[str, float]]] = {}

    one_x_two: dict[str, dict[str, float]] = {}
    for prefix, book in BOOKS_1X2_FIXTURES.items():
        h = _f(row.get(f"{prefix}H"))
        d = _f(row.get(f"{prefix}D"))
        a = _f(row.get(f"{prefix}A"))
        if h and d and a:
            one_x_two[book] = {"1": h, "X": d, "2": a}
    if one_x_two:
        out["Resultado Final (1X2)"] = one_x_two

    ou: dict[str, dict[str, float]] = {}
    for prefix, book in (("B365", "Bet365"), ("BFE", "Betfair Exchange")):
        over = _f(row.get(f"{prefix}>2.5"))
        under = _f(row.get(f"{prefix}<2.5"))
        if over and under:
            ou[book] = {"Over 2.5": over, "Under 2.5": under}
    if ou:
        out["Total de Gols"] = ou

    ah_line = _line(row.get("AHCh"))
    if ah_line is None:
        ah_line = _line(row.get("AHh"))
    if ah_line is not None:
        ah: dict[str, dict[str, float]] = {}
        for prefix, book in (("B365", "Bet365"), ("BFE", "Betfair Exchange")):
            home = _f(row.get(f"{prefix}CAHH")) or _f(row.get(f"{prefix}AHH"))
            away = _f(row.get(f"{prefix}CAHA")) or _f(row.get(f"{prefix}AHA"))
            if home and away:
                ah[book] = {
                    f"AH Casa {ah_line:+g}": home,
                    f"AH Fora {-ah_line:+g}": away,
                }
        if ah:
            out["Handicap Asiatico"] = ah
    return out


def parse_fixture_row(row: dict[str, str]) -> UpcomingFixture | None:
    """Interpreta uma linha do arquivo de JOGOS FUTUROS (ligas principais).

    A divisao vem da propria linha (coluna `Div`) — o arquivo mistura
    todas as ligas num so.
    """
    division = (row.get("Div") or "").strip()
    league = MAIN_DIVISIONS.get(division)
    if league is None:
        return None
    date = parse_date(row.get("Date", ""))
    home = (row.get("HomeTeam") or "").strip()
    away = (row.get("AwayTeam") or "").strip()
    if not date or not home or not away:
        return None

    odds = _build_fixture_odds(row)
    if not odds:
        return None

    best: dict[str, dict[str, float]] = {}
    who: dict[str, dict[str, str]] = {}
    for market, books in odds.items():
        b, w = _best_odds(books)
        best[market] = b
        who[market] = w

    return UpcomingFixture(
        division=division,
        league=league_label(division),
        date=date,
        time=(row.get("Time") or "").strip(),
        timezone=league.timezone,
        home=home,
        away=away,
        referee=(row.get("Referee") or "").strip(),
        odds=odds,
        best_odds=best,
        best_books=who,
    )


def _extra_code_for(country: str, league_name: str) -> str | None:
    """Descobre o codigo da liga extra a partir do pais/nome da linha.

    O arquivo de jogos futuros usa nomes de liga que nao batem exatamente
    com o catalogo, entao o pais e a chave primaria e o nome e o desempate.
    """
    pais = (country or "").strip().lower()
    nome = (league_name or "").strip().lower()
    for code, lg in EXTRA_LEAGUES.items():
        if lg.country.lower() != pais:
            continue
        if not nome or lg.name.lower() == nome:
            return code
    # fallback: so o pais (evita descartar por diferenca de nome)
    for code, lg in EXTRA_LEAGUES.items():
        if lg.country.lower() == pais:
            return code
    return None


def parse_extra_fixture_row(row: dict[str, str]) -> UpcomingFixture | None:
    """Interpreta uma linha do arquivo de jogos futuros das ligas extras."""
    code = _extra_code_for(row.get("Country", ""), row.get("League", ""))
    if code is None:
        return None
    league = EXTRA_LEAGUES[code]
    date = parse_date(row.get("Date", ""))
    home = (row.get("Home") or "").strip()
    away = (row.get("Away") or "").strip()
    if not date or not home or not away:
        return None

    one_x_two: dict[str, dict[str, float]] = {}
    for prefix, book in (("PS", "Pinnacle"), ("B365", "Bet365"),
                         ("BFE", "Betfair Exchange")):
        h = _f(row.get(f"{prefix}H"))
        d = _f(row.get(f"{prefix}D"))
        a = _f(row.get(f"{prefix}A"))
        if h and d and a:
            one_x_two[book] = {"1": h, "X": d, "2": a}
    if not one_x_two:
        return None

    odds = {"Resultado Final (1X2)": one_x_two}
    best, who = _best_odds(one_x_two)
    return UpcomingFixture(
        division=code,
        league=league_label(code),
        date=date,
        time=(row.get("Time") or "").strip(),
        timezone=league.timezone,
        home=home,
        away=away,
        odds=odds,
        best_odds={"Resultado Final (1X2)": best},
        best_books={"Resultado Final (1X2)": who},
    )


def _clean_reader(text: str) -> csv.DictReader:
    """DictReader com os nomes de coluna normalizados.

    Os arquivos de jogos futuros vem com BOM UTF-8, o que transforma a
    primeira coluna em `\\ufeffDiv` e faz todo `row.get("Div")` devolver
    None — silenciosamente, sem erro. Normalizar o cabecalho aqui evita
    que o parser inteiro vire zero linhas sem ninguem perceber.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames:
        reader.fieldnames = [
            (name or "").lstrip("\ufeff").strip() for name in reader.fieldnames
        ]
    return reader


def parse_fixtures_csv(text: str, extra: bool = False) -> list[UpcomingFixture]:
    """Interpreta um arquivo de jogos futuros inteiro."""
    reader = _clean_reader(text)
    out: list[UpcomingFixture] = []
    for row in reader:
        fx = parse_extra_fixture_row(row) if extra else parse_fixture_row(row)
        if fx is not None:
            out.append(fx)
    return out


@dataclass(frozen=True)
class CsvMatch:
    """Uma partida dos CSVs, com resultados, estatisticas e odds reais."""

    division: str
    league: str
    season: str
    date: str            # YYYY-MM-DD
    time: str            # HH:MM (vazio quando o CSV nao informa)
    timezone: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    home_corners: int | None = None
    away_corners: int | None = None
    home_cards: int | None = None
    away_cards: int | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    referee: str = ""
    attendance: int | None = None
    #: {mercado: {casa: {resultado: odd}}} — fechamento
    odds_closing: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    #: mesma estrutura, odds de abertura
    odds_opening: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    #: melhor odd e media do mercado (referencia, nao sao casas)
    market_reference: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def kickoff(self) -> str:
        """Kickoff local no formato aceito por timeutil."""
        return f"{self.date} {self.time or '00:00'}"

    @property
    def match_key(self) -> str:
        """Chave estavel para casar com o corpus de resultados."""
        return f"{self.date}|{_norm_team(self.home)}|{_norm_team(self.away)}"

    def to_historical(self) -> HistoricalMatch:
        """Converte para o tipo usado pelo motor de backtest."""
        return HistoricalMatch(
            home=self.home,
            away=self.away,
            home_goals=self.home_goals,
            away_goals=self.away_goals,
            home_corners=self.home_corners,
            away_corners=self.away_corners,
            home_cards=self.home_cards,
            away_cards=self.away_cards,
            home_shots=self.home_shots,
            away_shots=self.away_shots,
            home_shots_on_target=self.home_shots_on_target,
            away_shots_on_target=self.away_shots_on_target,
            kickoff=self.kickoff,
            timezone=self.timezone,
            league=self.league,
            season=self.season,
        )


def parse_main_row(row: dict[str, str], division: str) -> CsvMatch | None:
    """Interpreta uma linha de liga principal."""
    league = MAIN_DIVISIONS.get(division)
    if league is None:
        return None
    date = parse_date(row.get("Date", ""))
    home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
    hg, ag = _i(row.get("FTHG")), _i(row.get("FTAG"))
    if not date or not home or not away or hg is None or ag is None:
        return None

    season = _season_from_row(row)
    hy, ay = _i(row.get("HY")), _i(row.get("AY"))
    hr, ar = _i(row.get("HR")), _i(row.get("AR"))

    def cards(y: int | None, r: int | None) -> int | None:
        if y is None and r is None:
            return None
        return (y or 0) + (r or 0)

    ah_line = _line(row.get("AHh"))
    ahc_line = _line(row.get("AHCh"))

    closing: dict[str, dict[str, dict[str, float]]] = {}
    opening: dict[str, dict[str, dict[str, float]]] = {}
    if o := _build_odds_1x2(row, closing=True):
        closing["Resultado Final (1X2)"] = o
    if o := _build_odds_ou(row, closing=True):
        closing["Total de Gols"] = o
    if o := _build_odds_ah(row, closing=True, line=ahc_line if ahc_line is not None else ah_line):
        closing["Handicap Asiatico"] = o
    if o := _build_odds_1x2(row, closing=False):
        opening["Resultado Final (1X2)"] = o
    if o := _build_odds_ou(row, closing=False):
        opening["Total de Gols"] = o
    if o := _build_odds_ah(row, closing=False, line=ah_line):
        opening["Handicap Asiatico"] = o

    return CsvMatch(
        division=division,
        league=league_label(division),
        season=season,
        date=date,
        time=(row.get("Time") or "").strip(),
        timezone=league.timezone,
        home=home,
        away=away,
        home_goals=hg,
        away_goals=ag,
        home_corners=_i(row.get("HC")),
        away_corners=_i(row.get("AC")),
        home_cards=cards(hy, hr),
        away_cards=cards(ay, ar),
        home_shots=_i(row.get("HS")),
        away_shots=_i(row.get("AS")),
        home_shots_on_target=_i(row.get("HST")),
        away_shots_on_target=_i(row.get("AST")),
        referee=(row.get("Referee") or "").strip(),
        attendance=_i(row.get("Attendance")),
        odds_closing=closing,
        odds_opening=opening,
        market_reference=market_best_odd(row, closing=True),
    )


def parse_extra_row(row: dict[str, str], code: str) -> CsvMatch | None:
    """Interpreta uma linha de liga extra (so 1X2)."""
    league = EXTRA_LEAGUES.get(code)
    if league is None:
        return None
    date = parse_date(row.get("Date", ""))
    home, away = (row.get("Home") or "").strip(), (row.get("Away") or "").strip()
    hg, ag = _i(row.get("HG")), _i(row.get("AG"))
    if not date or not home or not away or hg is None or ag is None:
        return None

    closing: dict[str, dict[str, dict[str, float]]] = {}
    opening: dict[str, dict[str, dict[str, float]]] = {}
    if o := _build_odds_1x2(row, closing=True):
        closing["Resultado Final (1X2)"] = o
    if o := _build_odds_1x2(row, closing=False):
        opening["Resultado Final (1X2)"] = o

    return CsvMatch(
        division=code,
        league=league_label(code),
        season=(row.get("Season") or "").strip(),
        date=date,
        time=(row.get("Time") or "").strip(),
        timezone=league.timezone,
        home=home,
        away=away,
        home_goals=hg,
        away_goals=ag,
        odds_closing=closing,
        odds_opening=opening,
    )


def _season_from_row(row: dict[str, str]) -> str:
    """Temporada a partir da data (o CSV principal nao tem coluna Season)."""
    date = parse_date(row.get("Date", ""))
    if not date:
        return ""
    year, month = int(date[:4]), int(date[5:7])
    # temporadas europeias comecam em julho
    start = year if month >= 7 else year - 1
    return f"{start}/{str(start + 1)[2:]}"


def parse_csv(text: str, division: str) -> list[CsvMatch]:
    """Interpreta um CSV inteiro, escolhendo o parser pela origem."""
    is_extra = division in EXTRA_LEAGUES
    reader = _clean_reader(text)
    out: list[CsvMatch] = []
    for row in reader:
        match = (
            parse_extra_row(row, division)
            if is_extra
            else parse_main_row(row, division)
        )
        if match is not None:
            out.append(match)
    return out


# --------------------------------------------------------------------------
# Cliente de download
# --------------------------------------------------------------------------


@dataclass
class DownloadReport:
    requested: int = 0
    downloaded: int = 0
    cached: int = 0
    failed: int = 0
    bytes_total: int = 0
    errors: list[str] = field(default_factory=list)
    per_league: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "downloaded": self.downloaded,
            "cached": self.cached,
            "failed": self.failed,
            "bytes_total": self.bytes_total,
            "errors": self.errors[:20],
            "per_league": self.per_league,
        }


class FootballDataClient:
    """Baixa os CSVs com cache em disco e rate limit."""

    def __init__(
        self,
        root: Path | None = None,
        delay: float = REQUEST_DELAY_SECONDS,
        timeout: int = 45,
    ) -> None:
        self.root = Path(root) if root else CACHE_ROOT
        self.main_dir = self.root / "main"
        self.extra_dir = self.root / "extra"
        self._fixtures_dir = self.root / "fixtures"
        self.main_dir.mkdir(parents=True, exist_ok=True)
        self.extra_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.timeout = timeout
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def _get(self, url: str) -> str:
        """Baixa um arquivo. Qualquer falha vira FootballDataError.

        Captura ampla de proposito: uma excecao nao tratada (timeout de
        socket, erro de SSL, resposta truncada) derrubaria a importacao
        inteira por causa de UM arquivo. O chamador contabiliza a falha e
        segue para o proximo.
        """
        self._throttle()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise FootballDataError(f"HTTP {exc.code} em {url}") from exc
        except urllib.error.URLError as exc:
            raise FootballDataError(f"rede falhou em {url}: {exc.reason}") from exc
        except (TimeoutError, OSError, ConnectionError) as exc:
            raise FootballDataError(f"conexao caiu em {url}: {exc}") from exc
        except Exception as exc:  # ultima linha de defesa
            raise FootballDataError(f"{type(exc).__name__} em {url}: {exc}") from exc

        try:
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise FootballDataError(f"resposta ilegivel de {url}: {exc}") from exc

    def _path_main(self, season: str, division: str) -> Path:
        return self.main_dir / f"{season}_{division}.csv"

    def _path_extra(self, code: str) -> Path:
        return self.extra_dir / f"{code}.csv"

    def fetch_main(
        self, division: str, season: str, report: DownloadReport
    ) -> Path | None:
        dest = self._path_main(season, division)
        report.requested += 1
        if dest.exists() and dest.stat().st_size > 500:
            report.cached += 1
            return dest
        try:
            text = self._get(f"{BASE_MAIN}/{season}/{division}.csv")
        except FootballDataError as exc:
            report.failed += 1
            report.errors.append(str(exc))
            return None
        if len(text) < 500:
            report.failed += 1
            report.errors.append(f"resposta curta para {division} {season}")
            return None
        dest.write_text(text, encoding="utf-8")
        report.downloaded += 1
        report.bytes_total += len(text.encode("utf-8"))
        report.per_league[division] = report.per_league.get(division, 0) + 1
        return dest

    def fetch_extra(self, code: str, report: DownloadReport) -> Path | None:
        dest = self._path_extra(code)
        report.requested += 1
        if dest.exists() and dest.stat().st_size > 500:
            report.cached += 1
            return dest
        try:
            text = self._get(f"{BASE_EXTRA}/{code}.csv")
        except FootballDataError as exc:
            report.failed += 1
            report.errors.append(str(exc))
            return None
        if len(text) < 500:
            report.failed += 1
            report.errors.append(f"resposta curta para {code}")
            return None
        dest.write_text(text, encoding="utf-8")
        report.downloaded += 1
        report.bytes_total += len(text.encode("utf-8"))
        report.per_league[code] = report.per_league.get(code, 0) + 1
        return dest

    def fetch_all(
        self,
        seasons: Sequence[str] | None = None,
        divisions: Sequence[str] | None = None,
        extras: Sequence[str] | None = None,
        progress: Any = None,
    ) -> DownloadReport:
        """Baixa tudo que foi pedido, pulando o que ja esta em cache."""
        seasons = list(seasons or season_codes(2010, 2025))
        divisions = list(divisions or MAIN_DIVISIONS)
        extras = list(extras or EXTRA_LEAGUES)
        report = DownloadReport()
        total = len(divisions) * len(seasons) + len(extras)
        done = 0
        for division in divisions:
            for season in seasons:
                self.fetch_main(division, season, report)
                done += 1
                if progress is not None and done % 10 == 0:
                    progress(done, total, f"{division} {season}")
        for code in extras:
            self.fetch_extra(code, report)
            done += 1
            if progress is not None and done % 10 == 0:
                progress(done, total, code)
        if progress is not None:
            progress(total, total, "concluido")
        return report

    # ------------------------------------------------------------ jogos futuros

    @property
    def fixtures_dir(self) -> Path:
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        return self._fixtures_dir

    def fetch_fixtures(
        self, report: DownloadReport | None = None
    ) -> tuple[dict[str, Path], DownloadReport]:
        """Baixa os jogos FUTUROS com odds reais.

        Sao dois arquivos: ligas principais e ligas extras. O site os
        atualiza as sextas (jogos de fim de semana) e as tercas (meio de
        semana), entao vale rebaixar sempre.

        Devolve (caminhos gravados, relatorio).
        """
        report = report or DownloadReport()
        out: dict[str, Path] = {}
        for key, url in (("main", URL_FIXTURES_MAIN), ("extra", URL_FIXTURES_EXTRA)):
            dest = self.fixtures_dir / f"{key}.csv"
            report.requested += 1
            try:
                text = self._get(url)
            except FootballDataError as exc:
                report.failed += 1
                report.errors.append(str(exc))
                continue
            if len(text) < 200:
                report.failed += 1
                report.errors.append(f"resposta curta para {url}")
                continue
            try:
                dest.write_text(text, encoding="utf-8")
            except OSError as exc:
                report.failed += 1
                report.errors.append(f"falha ao gravar {dest.name}: {exc}")
                continue
            out[key] = dest
            report.downloaded += 1
            report.bytes_total += len(text.encode("utf-8"))
        _append_manifest(self.root / "manifest.json", "fixtures", report)
        return out, report

    def load_fixtures(self) -> list[UpcomingFixture]:
        """Le os jogos futuros do cache local. Vazio se nunca baixados."""
        out: list[UpcomingFixture] = []
        main = self._fixtures_dir / "main.csv"
        if main.exists():
            out.extend(parse_fixtures_csv(
                main.read_text(encoding="utf-8"), extra=False
            ))
        extra = self._fixtures_dir / "extra.csv"
        if extra.exists():
            out.extend(parse_fixtures_csv(
                extra.read_text(encoding="utf-8"), extra=True
            ))
        # ordena por kickoff, mais proximo primeiro
        out.sort(key=lambda f: (f.date, f.time))
        return out

    def fixtures_inventory(self) -> dict[str, Any]:
        """Resumo dos jogos futuros em cache."""
        main = self._fixtures_dir / "main.csv"
        extra = self._fixtures_dir / "extra.csv"
        if not main.exists() and not extra.exists():
            return {"available": False, "n_fixtures": 0}
        fixtures = self.load_fixtures()
        return {
            "available": True,
            "n_fixtures": len(fixtures),
            "with_odds": sum(1 for f in fixtures if f.has_odds),
            "competitions": sorted({f.league for f in fixtures}),
            "first_date": fixtures[0].date if fixtures else None,
            "last_date": fixtures[-1].date if fixtures else None,
            "updated_at": max(
                (p.stat().st_mtime for p in (main, extra) if p.exists()),
                default=0.0,
            ),
        }

    # ------------------------------------------------------------ leitura

    def load_matches(self, divisions: Sequence[str] | None = None) -> list[CsvMatch]:
        """Le todos os CSVs em cache e devolve as partidas parseadas."""
        out: list[CsvMatch] = []
        wanted = set(divisions) if divisions else None
        for path in sorted(self.main_dir.glob("*.csv")):
            division = path.stem.split("_", 1)[1]
            if wanted is not None and division not in wanted:
                continue
            out.extend(parse_csv(path.read_text(encoding="utf-8"), division))
        for path in sorted(self.extra_dir.glob("*.csv")):
            code = path.stem
            if wanted is not None and code not in wanted:
                continue
            out.extend(parse_csv(path.read_text(encoding="utf-8"), code))
        return out

    def sample_books(self, sample_files: int = 12) -> list[str]:
        """Bookmakers presentes na fonte, lendo apenas alguns arquivos.

        Carregar os ~600 CSVs inteiros leva dezenas de segundos; a lista de
        casas disponiveis nao precisa disso. Ler uma amostra recente da o
        mesmo conjunto de nomes em milissegundos — e mantem o endpoint de
        opcoes rapido.
        """
        found: set[str] = set()
        candidates = sorted(self.main_dir.glob("*.csv"), reverse=True)[:sample_files]
        candidates += sorted(self.extra_dir.glob("*.csv"))[:3]
        for path in candidates:
            if not path.exists():
                continue
            division = (
                path.stem.split("_", 1)[1] if "_" in path.stem else path.stem
            )
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in parse_csv(text, division):
                for books in match.odds_closing.values():
                    found.update(books)
                for books in match.odds_opening.values():
                    found.update(books)
            if len(found) >= len(BOOKS_1X2):
                break
        return sorted(found)

    def inventory(self) -> dict[str, Any]:
        """O que ja esta em cache, sem tocar a rede."""
        main = sorted(p.stem for p in self.main_dir.glob("*.csv"))
        extra = sorted(p.stem for p in self.extra_dir.glob("*.csv"))
        size = sum(p.stat().st_size for p in self.root.rglob("*.csv"))
        return {
            "main_files": len(main),
            "extra_files": len(extra),
            "total_mb": round(size / 1024 / 1024, 2),
            "divisions": sorted({n.split("_", 1)[1] for n in main}),
            "extras": extra,
        }

    # -------------------------------------------------- indice em disco

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def corpus_signature(self) -> str:
        """Assinatura do que esta em cache: muda quando os arquivos mudam.

        Combina a contagem e a maior data de modificacao. Se um CSV novo
        chegar, a assinatura muda e o indice e refeito.
        """
        files = sorted(self.root.rglob("*.csv"))
        newest = max((p.stat().st_mtime for p in files), default=0.0)
        return f"{len(files)}|{newest:.0f}"

    def cached_summary(self) -> dict[str, Any] | None:
        """Resumo do corpus lido do indice em disco, se ainda for valido.

        Parsear ~600 CSVs leva ~20s. O resumo (contagem, periodo,
        competicoes, fingerprint) so muda quando os dados mudam, entao
        fica gravado em `index.json` e o endpoint de opcoes responde
        instantaneamente.
        """
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if payload.get("signature") != self.corpus_signature():
            return None
        return payload

    def write_summary(self, summary: dict[str, Any]) -> None:
        payload = dict(summary)
        payload["signature"] = self.corpus_signature()
        payload["written_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            # indice e otimizacao, nao dado: falhar aqui nao pode quebrar nada
            pass

    def corpus_summary(self, refresh: bool = False) -> dict[str, Any]:
        """Resumo do corpus, usando o indice em disco quando possivel."""
        if not refresh:
            cached = self.cached_summary()
            if cached is not None:
                return cached
        matches = self.load_matches()
        corpus = HistoricalCorpus([m.to_historical() for m in matches])
        st = corpus.stats()
        summary = {
            "n_matches": st.n_matches,
            "first_kickoff": st.first_kickoff[:10],
            "last_kickoff": st.last_kickoff[:10],
            "competitions": list(st.competitions),
            "seasons": list(st.seasons),
            "n_with_closing_odds": sum(1 for m in matches if m.odds_closing),
            "books": sorted({
                b for m in matches for books in m.odds_closing.values() for b in books
            }),
        }
        self.write_summary(summary)
        return summary


# --------------------------------------------------------------------------
# Store de odds reais
# --------------------------------------------------------------------------


class RealOddsStore:
    """Indice de odds reais por partida, construido a partir dos CSVs.

    As odds do football-data.co.uk sao POR PARTIDA (nao snapshots ao longo
    do tempo). A de fechamento e o preco no instante do kickoff — a linha
    mais eficiente do mercado e o teste mais duro que existe.

    Point-in-time: a linha de fechamento esta disponivel no kickoff, entao
    usa-la nao viola a regra do backtest (o modelo continua vendo apenas o
    que existia antes). A de abertura esta disponivel ainda antes.
    """

    def __init__(self, closing: bool = True) -> None:
        self.closing = closing
        self._by_key: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
        self._by_teams: dict[tuple[str, str], list[CsvMatch]] = {}

    def add(self, matches: Iterable[CsvMatch]) -> int:
        count = 0
        for m in matches:
            odds = m.odds_closing if self.closing else m.odds_opening
            if not odds:
                continue
            self._by_key[m.match_key] = odds
            key = (_norm_team(m.home), _norm_team(m.away))
            self._by_teams.setdefault(key, []).append(m)
            count += 1
        return count

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def n_matches(self) -> int:
        return len(self._by_key)

    def books(self) -> list[str]:
        found: set[str] = set()
        for markets in self._by_key.values():
            for outcomes in markets.values():
                found.update(outcomes)
        return sorted(found)

    def markets(self) -> list[str]:
        found: set[str] = set()
        for markets in self._by_key.values():
            found.update(markets)
        return sorted(found)

    def odds_for(self, match: HistoricalMatch) -> dict[str, dict[str, dict[str, float]]]:
        """Odds da partida. Aceita data exata ou casa/fora (time unico)."""
        key = f"{match.kickoff[:10]}|{_norm_team(match.home)}|{_norm_team(match.away)}"
        if key in self._by_key:
            return self._by_key[key]
        # fallback: mesmo confronto em outra data (fuso/dia diferente)
        candidates = self._by_teams.get((_norm_team(match.home), _norm_team(match.away)), [])
        for c in candidates:
            if c.date == match.kickoff[:10]:
                return (c.odds_closing if self.closing else c.odds_opening)
        return {}


class CsvRealOddsSource:
    """`OddsSource` que serve odds reais do football-data.co.uk.

    Cumpre o mesmo contrato de `NaiveSyntheticOddsSource`, entao o motor de
    backtest nao precisa saber de onde vem. Sem odds para a partida, falha
    alto — nunca cai para o mercado sintetico em silencio.
    """

    name = "football_data_uk"
    #: odds reais vem do bookmaker; o mercado de referencia nao e usado
    needs_reference_probs = False

    def __init__(
        self,
        store: RealOddsStore,
        book_filter: Sequence[str] | None = None,
    ) -> None:
        self.store = store
        self.book_filter = set(book_filter) if book_filter else None
        self.last_as_of: str | None = None
        self._misses: list[str] = []
        self._missing_books: list[str] = []

    @property
    def misses(self) -> list[str]:
        return list(self._misses)

    @property
    def missing_books(self) -> list[str]:
        return list(self._missing_books)

    def odds_for(
        self,
        match: HistoricalMatch,
        reference_probs: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, dict[str, float]]]:
        raw = self.store.odds_for(match)
        if not raw:
            label = f"{match.home} vs {match.away} ({match.kickoff})"
            self._misses.append(label)
            raise KeyError(
                f"sem odds reais para {label}: a partida nao esta nos CSVs "
                f"importados (ou o confronto mudou de data)"
            )
        if self.book_filter is not None:
            raw = {
                market: {b: o for b, o in books.items() if b in self.book_filter}
                for market, books in raw.items()
            }
            raw = {m: b for m, b in raw.items() if b}
        # CSV não contém timestamp de publicação. Não inventar kickoff.
        self.last_as_of = None
        return raw
