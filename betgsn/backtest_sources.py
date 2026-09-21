"""BETGSN :: backtest_sources — importacao de dados historicos reais.

Este modulo e a ponte entre os provedores externos e o motor de backtest.
Ele NAO calcula nada estatistico: apenas busca, valida, normaliza e guarda
dados, sempre preservando o instante de cada observacao.

Fontes suportadas
-----------------
- **The Odds API** (`/v4/historical/sports/{sport}/odds`): odds historicas
  multi-casa com timestamp por snapshot.
- **API-Football** (`/fixtures`, `/fixtures/statistics`): resultados, xG,
  cantos e cartoes de temporadas reais, com kickoff em ISO 8601 e offset.

Principio de seguranca temporal
-------------------------------
Odds sao a unica entrada que muda de valor ao longo do tempo. Um snapshot
posterior ao kickoff e inutil (e envenena o backtest). Por isso:

  1. o importador grava o `timestamp` que a API informa para o snapshot;
  2. `OddsHistoryCache.find_before` so devolve snapshots com timestamp
     ESTRITAMENTE anterior ao kickoff;
  3. snapshots mais velhos que `max_age_hours` sao recusados, porque uma
     odd de tres dias antes nao representa o mercado daquele momento.

Nenhum dado e inventado. Sem chave de API, a importacao falha com
mensagem explicita dizendo o que falta e como configurar.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .model import HistoricalMatch
from .providers import (
    SOCCER_KEYS,
    ApiFootballProvider,
    OddsApiProvider,
    ProviderError,
)
from .timeutil import UTC_FORMAT, KickoffError, parse_kickoff, utc_key

CACHE_ROOT = Path(__file__).resolve().parent.parent / "output" / "backtest_cache"
ODDS_DIR = CACHE_ROOT / "odds"
FIXTURES_DIR = CACHE_ROOT / "fixtures"
MANIFEST_PATH = CACHE_ROOT / "manifest.json"

#: Mercados internos que a The Odds API consegue alimentar diretamente.
ODDS_API_MARKET_MAP = {
    "h2h": "Resultado Final (1X2)",
    "totals": "Total de Gols",
    "btts": "Ambas Marcam",
}

#: Chaves de esporte da The Odds API (fonte unica: providers.SOCCER_KEYS).
ODDS_SPORT_KEYS: dict[str, str] = dict(SOCCER_KEYS)


class OddsHistoryError(RuntimeError):
    """Falha ao ler/escrever o cache de odds historicas."""


# --------------------------------------------------------------------------
# Normalizacao de nomes
# --------------------------------------------------------------------------


#: Siglas e palavras societarias que nao identificam o clube. Removidas
#: como PALAVRA INTEIRA (`\b`), entao "Sport Recife" -> "recife" mas
#: "Sporting" nao e afetado.
_CLUB_STOPWORDS = (
    r"fc|sc|cf|ac|ec|afc|cd|cs|cr|fr|se|sp|aa|ad|gr|fbc|sd|"
    r"club|clube|futebol|football|sport|sporting"
)


def normalize_team(name: str) -> str:
    """Chave de comparacao de nome de time.

    Remove acentos, pontuacao, siglas societarias e espacos. E o que
    permite casar "São Paulo" (API-Football) com "Sao Paulo" (dataset
    local), "CR Flamengo" com "Flamengo" e "Botafogo FR" com "Botafogo".
    """
    raw = unicodedata.normalize("NFKD", name or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower()
    raw = re.sub(rf"\b({_CLUB_STOPWORDS})\b", " ", raw)
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


# --------------------------------------------------------------------------
# Snapshot de odds
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OddsSnapshot:
    """Odds multi-casa de um instante, como capturadas do provedor."""

    sport_key: str
    requested_date: str
    timestamp: str          # UTC ISO 8601 — quando o mercado tinha esse estado
    events: tuple[dict[str, Any], ...]
    provider: str = "the-odds-api"

    @property
    def utc_key(self) -> str:
        return utc_key(self.timestamp)

    def to_json(self) -> dict[str, Any]:
        return {
            "sport_key": self.sport_key,
            "requested_date": self.requested_date,
            "timestamp": self.timestamp,
            "provider": self.provider,
            "events": list(self.events),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "OddsSnapshot":
        return cls(
            sport_key=payload["sport_key"],
            requested_date=payload.get("requested_date", ""),
            timestamp=payload["timestamp"],
            events=tuple(payload.get("events", ())),
            provider=payload.get("provider", "the-odds-api"),
        )


class OddsHistoryCache:
    """Cache em disco das capturas de odds, com indice em memoria.

    Arquivo por snapshot:
        output/backtest_cache/odds/{sport_key}/{timestamp}.json
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else ODDS_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, list[OddsSnapshot]] = {}

    def _dir_for(self, sport_key: str) -> Path:
        safe = re.sub(r"[^a-z0-9_]+", "_", sport_key.lower())
        path = self.root / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _slug(self, timestamp: str) -> str:
        return timestamp.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")

    def save(self, snapshot: OddsSnapshot) -> Path:
        path = self._dir_for(snapshot.sport_key) / f"{self._slug(snapshot.timestamp)}.json"
        path.write_text(
            json.dumps(snapshot.to_json(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        # invalida o indice em memoria desse esporte
        self._index.pop(snapshot.sport_key, None)
        return path

    def load(self, sport_key: str) -> list[OddsSnapshot]:
        """Todos os snapshots de um esporte, ordenados por timestamp."""
        if sport_key in self._index:
            return self._index[sport_key]
        folder = self._dir_for(sport_key)
        snapshots: list[OddsSnapshot] = []
        for path in sorted(folder.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                snapshots.append(OddsSnapshot.from_json(payload))
            except (json.JSONDecodeError, KeyError) as exc:
                raise OddsHistoryError(
                    f"snapshot corrompido em {path}: {exc}. Apague o arquivo e "
                    f"reimporte a janela."
                ) from exc
        snapshots.sort(key=lambda s: s.utc_key)
        self._index[sport_key] = snapshots
        return snapshots

    def find_before(
        self,
        sport_key: str,
        cutoff_utc: str,
        max_age_hours: float = 24.0,
    ) -> OddsSnapshot | None:
        """Snapshot mais recente ANTERIOR ao cutoff, dentro da janela.

        Devolve None se nao houver nenhum valido. Nunca devolve um snapshot
        posterior ao cutoff: essa e a garantia que impede data leakage na
        entrada de odds.
        """
        candidates = [
            s for s in self.load(sport_key) if s.utc_key < cutoff_utc
        ]
        if not candidates:
            return None
        latest = candidates[-1]
        age = parse_kickoff(cutoff_utc) - parse_kickoff(latest.timestamp)
        if age > timedelta(hours=max_age_hours):
            return None
        return latest

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not self.root.exists():
            return out
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            files = sorted(folder.glob("*.json"))
            if not files:
                continue
            timestamps = []
            for f in files:
                try:
                    timestamps.append(json.loads(f.read_text(encoding="utf-8"))["timestamp"])
                except (json.JSONDecodeError, KeyError):
                    continue
            out[folder.name] = {
                "snapshots": len(files),
                "first": min(timestamps) if timestamps else None,
                "last": max(timestamps) if timestamps else None,
            }
        return out


def odds_from_snapshot(
    snapshot: OddsSnapshot,
    match: HistoricalMatch,
) -> dict[str, dict[str, dict[str, float]]]:
    """Extrai as odds de UMA partida dentro de um snapshot.

    Reutiliza `providers.odds_event_to_internal` — a mesma conversao que o
    Scanner usa — para nao existir um segundo parser divergindo do primeiro.
    """
    from .providers import odds_event_to_internal

    want_home = normalize_team(match.home)
    want_away = normalize_team(match.away)
    for event in snapshot.events:
        if (
            normalize_team(event.get("home_team", "")) == want_home
            and normalize_team(event.get("away_team", "")) == want_away
        ):
            return odds_event_to_internal(event)
    return {}


# --------------------------------------------------------------------------
# Importador de odds (The Odds API)
# --------------------------------------------------------------------------


@dataclass
class ImportReport:
    source: str
    requested: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    window: tuple[str, str] | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class HistoricalOddsImporter:
    """Baixa snapshots historicos de odds e guarda no cache local.

    Estrategia: para cada instante da janela, pede o snapshot mais proximo
    daquele horario. Cada snapshot e gravado com o `timestamp` informado
    pela API — e esse timestamp, nao o horario pedido, que decide se a odd
    estava disponivel antes do kickoff.
    """

    def __init__(
        self,
        provider: OddsApiProvider,
        cache: OddsHistoryCache | None = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.provider = provider
        self.cache = cache or OddsHistoryCache()
        self.sleep_seconds = sleep_seconds

    def import_window(
        self,
        sport_key: str,
        start: str,
        end: str,
        step_hours: float = 6.0,
        max_requests: int | None = None,
        progress: Any = None,
    ) -> ImportReport:
        """Importa a janela [start, end] em passos de `step_hours`."""
        try:
            cursor = parse_kickoff(start)
            limit = parse_kickoff(end)
        except KickoffError as exc:
            raise OddsHistoryError(f"janela invalida: {exc}") from exc

        step = timedelta(hours=max(0.5, step_hours))
        report = ImportReport(source="odds", window=(start, end))
        total = 0
        while cursor <= limit:
            if max_requests is not None and report.requested >= max_requests:
                break
            total += 1
            cursor += step
        report.detail["planned_requests"] = total

        cursor = parse_kickoff(start)
        done = 0
        while cursor <= limit:
            if max_requests is not None and report.requested >= max_requests:
                report.detail["stopped_early"] = True
                break
            requested_iso = cursor.strftime(UTC_FORMAT)
            report.requested += 1
            done += 1
            if progress is not None:
                progress(done, total, requested_iso)
            try:
                payload = self.provider.historical_odds(sport_key, requested_iso)
            except ProviderError as exc:
                report.failed += 1
                report.errors.append(f"{requested_iso}: {exc}")
                cursor += step
                continue

            snapshot = self._to_snapshot(sport_key, requested_iso, payload)
            if snapshot is None:
                report.skipped += 1
            else:
                self.cache.save(snapshot)
                report.imported += 1
            cursor += step
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)

        _append_manifest(self.cache.root.parent / "manifest.json", "odds", report)
        return report

    @staticmethod
    def _to_snapshot(
        sport_key: str,
        requested_iso: str,
        payload: Any,
    ) -> OddsSnapshot | None:
        """Converte a resposta da API num snapshot. Nunca inventa timestamp.

        A The Odds API devolve `{"timestamp": ..., "data": [...]}` no
        endpoint historico. Se o timestamp nao vier, o snapshot e recusado:
        sem saber QUANDO o mercado tinha aquele estado, a odd nao pode ser
        usada point-in-time.
        """
        if not isinstance(payload, dict):
            return None
        timestamp = payload.get("timestamp")
        events = payload.get("data")
        if not timestamp or not isinstance(events, list) or not events:
            return None
        try:
            normalized_ts = utc_key(timestamp)
        except KickoffError:
            return None
        return OddsSnapshot(
            sport_key=sport_key,
            requested_date=requested_iso,
            timestamp=normalized_ts,
            events=tuple(events),
        )


# --------------------------------------------------------------------------
# Importador de partidas (API-Football)
# --------------------------------------------------------------------------

#: Status da API-Football que significam "partida encerrada com placar".
FINISHED_STATUSES = {"FT", "AET", "PEN"}


def fixture_to_match(payload: dict[str, Any]) -> HistoricalMatch | None:
    """Converte um item de `/fixtures` num `HistoricalMatch`.

    Devolve None quando a partida nao esta encerrada ou falta placar —
    melhor descartar do que gravar um resultado incompleto.

    O kickoff e mantido em ISO 8601 COM o offset do provedor
    (ex.: "2024-04-14T15:00:00-03:00"). O corpus normaliza para UTC na
    leitura, entao o fuso verdadeiro da partida fica preservado.
    """
    fixture = payload.get("fixture") or {}
    league = payload.get("league") or {}
    teams = payload.get("teams") or {}
    goals = payload.get("goals") or {}
    status = ((fixture.get("status") or {}).get("short") or "").upper()

    if status not in FINISHED_STATUSES:
        return None
    if goals.get("home") is None or goals.get("away") is None:
        return None
    date = fixture.get("date")
    home = (teams.get("home") or {}).get("name")
    away = (teams.get("away") or {}).get("name")
    if not date or not home or not away:
        return None
    if not _valid_kickoff(date):
        return None

    return HistoricalMatch(
        home=home,
        away=away,
        home_goals=int(goals["home"]),
        away_goals=int(goals["away"]),
        kickoff=date,
        timezone="",  # o offset ja vem na string ISO
        league=league.get("name") or "",
        season=str(league.get("season") or ""),
    )


def _valid_kickoff(value: str) -> bool:
    try:
        parse_kickoff(value)
        return True
    except KickoffError:
        return False


def apply_statistics(match: HistoricalMatch, payload: Any) -> HistoricalMatch:
    """Preenche xG, cantos e cartoes a partir de `/fixtures/statistics`.

    Aceita a resposta crua da API (lista de dois times). Campos ausentes
    ficam como None — e o settlement trata isso explicitamente, sem virar
    derrota silenciosa.
    """
    if not isinstance(payload, list) or len(payload) != 2:
        return match
    by_name = {normalize_team((item.get("team") or {}).get("name", "")): item for item in payload}
    home_stats = by_name.get(normalize_team(match.home))
    away_stats = by_name.get(normalize_team(match.away))
    if home_stats is None or away_stats is None:
        # Não é possível inferir mando pela magnitude do ID.
        return match

    def pick(entry: dict[str, Any], stat_type: str) -> float | None:
        for item in entry.get("statistics", []):
            if item.get("type") == stat_type:
                value = item.get("value")
                if value is None:
                    return None
                if isinstance(value, str) and value.endswith("%"):
                    value = value[:-1]
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    return HistoricalMatch(
        home=match.home,
        away=match.away,
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        home_xg=_pick_first(pick(home_stats, "expected_goals"), match.home_xg),
        away_xg=_pick_first(pick(away_stats, "expected_goals"), match.away_xg),
        home_xg_against=pick(away_stats, "expected_goals"),
        away_xg_against=pick(home_stats, "expected_goals"),
        xg_status="REAL" if pick(home_stats, "expected_goals") is not None and pick(away_stats, "expected_goals") is not None else "UNAVAILABLE",
        xg_source="API-Football" if pick(home_stats, "expected_goals") is not None else None,
        home_corners=_as_int(pick(home_stats, "Corner Kicks")),
        away_corners=_as_int(pick(away_stats, "Corner Kicks")),
        home_cards=_cards(pick(home_stats, "Yellow Cards"), pick(home_stats, "Red Cards")),
        away_cards=_cards(pick(away_stats, "Yellow Cards"), pick(away_stats, "Red Cards")),
        home_shots=_as_int(pick(home_stats, "Total Shots")),
        away_shots=_as_int(pick(away_stats, "Total Shots")),
        weight=match.weight,
        kickoff=match.kickoff,
        timezone=match.timezone,
        league=match.league,
        season=match.season,
    )


def _team_id(entry: Any) -> int:
    if isinstance(entry, dict):
        team = entry.get("team") or {}
        return int(team.get("id") or 0)
    return 0


def _pick_first(primary: float | None, fallback: float | None) -> float | None:
    return primary if primary is not None else fallback


def _as_int(value: float | None) -> int | None:
    return int(value) if value is not None else None


def _cards(yellow: float | None, red: float | None) -> int | None:
    if yellow is None and red is None:
        return None
    return int((yellow or 0) + (red or 0))


class HistoricalFixtureImporter:
    """Baixa temporadas reais (resultados + estatisticas) e guarda local."""

    def __init__(
        self,
        provider: ApiFootballProvider,
        root: Path | None = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.provider = provider
        self.root = Path(root) if root else FIXTURES_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.sleep_seconds = sleep_seconds

    def _path(self, league: int, season: int) -> Path:
        return self.root / f"league{league}_season{season}.jsonl"

    def import_season(
        self,
        league: int,
        season: int,
        with_statistics: bool = False,
        max_fixtures: int | None = None,
        progress: Any = None,
    ) -> ImportReport:
        """Importa uma temporada inteira. Opcionalmente busca estatisticas."""
        report = ImportReport(source="fixtures", window=(str(league), str(season)))
        try:
            raw = self.provider.fixtures_by_season(league, season)
        except ProviderError as exc:
            raise OddsHistoryError(f"falha ao baixar temporada: {exc}") from exc

        matches: list[HistoricalMatch] = []
        for item in raw:
            match = fixture_to_match(item)
            if match is None:
                report.skipped += 1
                continue
            matches.append(match)

        report.requested = len(raw)
        if max_fixtures is not None:
            matches = matches[:max_fixtures]

        enriched: list[HistoricalMatch] = []
        total = len(matches)
        for i, match in enumerate(matches, 1):
            if progress is not None:
                progress(i, total, f"{match.home} vs {match.away}")
            if with_statistics:
                fixture_id = _fixture_id_of(match, raw)
                if fixture_id is not None:
                    try:
                        stats = self.provider.fixture_statistics(fixture_id)
                        match = apply_statistics(match, stats)
                    except ProviderError as exc:
                        report.errors.append(f"{match.home} vs {match.away}: {exc}")
                    if self.sleep_seconds:
                        time.sleep(self.sleep_seconds)
            enriched.append(match)

        with self._path(league, season).open("w", encoding="utf-8") as fh:
            for match in enriched:
                fh.write(json.dumps(asdict(match), ensure_ascii=False) + "\n")

        report.imported = len(enriched)
        report.detail["with_statistics"] = with_statistics
        report.detail["path"] = str(self._path(league, season))
        _append_manifest(self.root.parent / "manifest.json", "fixtures", report)
        return report

    def load_season(self, league: int, season: int) -> list[HistoricalMatch]:
        path = self._path(league, season)
        if not path.exists():
            return []
        out: list[HistoricalMatch] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(HistoricalMatch(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                raise OddsHistoryError(f"linha invalida em {path}: {exc}") from exc
        return out

    def load_all(self) -> list[HistoricalMatch]:
        """Junta todas as temporadas importadas num unico corpus."""
        out: list[HistoricalMatch] = []
        for path in sorted(self.root.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(HistoricalMatch(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        return out

    def available_seasons(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("league*_season*.jsonl")):
            m = re.match(r"league(\d+)_season(\d+)\.jsonl", path.name)
            if not m:
                continue
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            out.append({
                "league": int(m.group(1)),
                "season": int(m.group(2)),
                "matches": len(lines),
                "file": path.name,
            })
        return out


def _fixture_id_of(match: HistoricalMatch, raw: Sequence[dict[str, Any]]) -> int | None:
    """Recupera o id do fixture casando por nomes e horario."""
    want_home = normalize_team(match.home)
    want_away = normalize_team(match.away)
    for item in raw:
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        home = normalize_team((teams.get("home") or {}).get("name", ""))
        away = normalize_team((teams.get("away") or {}).get("name", ""))
        if home == want_home and away == want_away and fixture.get("date") == match.kickoff:
            return int(fixture.get("id") or 0) or None
    return None


# --------------------------------------------------------------------------
# Manifesto de importacoes (auditoria)
# --------------------------------------------------------------------------


def _append_manifest(manifest_path: Path, kind: str, report: Any) -> None:
    """Registra a operacao no manifesto do cache (auditoria).

    O payload passa por sanitizacao: mensagens de erro de terceiros podem
    conter a URL com a chave de API, e o manifesto e um arquivo em disco.
    Defesa em profundidade — `providers.redact_url` ja limpa na origem.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        current = {"imports": []}
    entry = report.to_json()
    entry["kind"] = kind
    entry["finished_at"] = datetime.now(timezone.utc).strftime(UTC_FORMAT)
    entry = json.loads(redact_text(json.dumps(entry, ensure_ascii=False)))
    current.setdefault("imports", []).append(entry)
    manifest_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8"
    )


#: Padroes de credencial que podem escapar em mensagens de terceiros.
_CREDENTIAL_RE = re.compile(
    r"(api_?key\s*[=:]\s*)[^\s&\"',}]+", re.IGNORECASE
)


def redact_text(text: str) -> str:
    """Substitui credenciais por *** em texto livre.

    Aplicado antes de gravar qualquer coisa em disco ou devolver em
    resposta HTTP: uma chave nunca pode sair do processo.
    """
    return _CREDENTIAL_RE.sub(r"\1***", text)


def read_manifest(manifest_path: Path | None = None) -> dict[str, Any]:
    path = Path(manifest_path) if manifest_path else MANIFEST_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"imports": []}


def load_imported_matches(root: Path | None = None) -> list[HistoricalMatch]:
    """Carrega todas as temporadas importadas, sem precisar de provider.

    Le os JSONL gravados por `HistoricalFixtureImporter.import_season`.
    Devolve lista vazia se nada foi importado — quem chama decide o que
    fazer (a API devolve erro explicativo, nunca um backtest vazio).
    """
    base = Path(root) if root else FIXTURES_DIR
    if not base.exists():
        return []
    out: list[HistoricalMatch] = []
    for path in sorted(base.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(HistoricalMatch(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
    return out


def corpus_fingerprint(matches: Iterable[HistoricalMatch]) -> str:
    """Hash do conjunto de partidas — muda quando os dados mudam.

    Serve para saber se dois backtests rodaram sobre o mesmo corpus: se o
    hash difere, a comparacao entre execucoes mistura efeito de codigo com
    efeito de dados.
    """
    digest = hashlib.sha256()
    for m in sorted(matches, key=lambda x: (x.kickoff, x.home, x.away)):
        digest.update(
            f"{m.kickoff}|{m.home}|{m.away}|{m.home_goals}|{m.away_goals}".encode("utf-8")
        )
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------
# Captura de odds ao vivo -> historico real
# --------------------------------------------------------------------------


@dataclass
class CaptureReport:
    """Resultado de uma captura de odds ao vivo."""

    captured_at: str
    sport_keys: list[str] = field(default_factory=list)
    snapshots_saved: int = 0
    events: int = 0
    events_with_odds: int = 0
    credits_last: int | None = None
    credits_used: int | None = None
    credits_remaining: int | None = None
    #: mercados efetivamente usados por esporte (pode diferir do pedido)
    markets_used: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class LiveOddsCapture:
    """Captura odds AO VIVO e grava como snapshot point-in-time.

    Por que isso existe
    -------------------
    Odds historicas exigem plano pago em ambos os provedores. Mas o
    endpoint de odds AO VIVO e gratuito e cobre os jogos futuros.

    Entao: capturando periodicamente (ex.: 1x/dia), cada captura vira um
    `OddsSnapshot` com o timestamp do momento. Quando a partida acontece,
    aquele snapshot deixa de ser "ao vivo" e passa a ser odds HISTORICA
    REAL — e o `RealHistoricalOddsSource` ja sabe consumi-la, porque ele so
    exige que o snapshot seja anterior ao kickoff.

    Nenhum dado e inventado: a odd e a que o bookmaker publicou, com o
    horario em que foi observada. O que a captura nao pode garantir e que
    ela seja o ultimo preco antes do fechamento — se voce capturar 24h
    antes, e esse o preco que fica registrado.
    """

    def __init__(
        self,
        provider: OddsApiProvider,
        cache: OddsHistoryCache | None = None,
        regions: str = "eu,uk",
        markets: str = "h2h,totals,btts",
    ) -> None:
        self.provider = provider
        self.cache = cache or OddsHistoryCache()
        self.regions = regions
        self.markets = markets

    def capture(
        self,
        sport_keys: Sequence[str],
        now: datetime | None = None,
    ) -> CaptureReport:
        """Captura os jogos futuros de cada esporte e grava um snapshot.

        Um snapshot por esporte (nao por partida): e assim que a API
        responde, e manter a granularidade original facilita auditar depois
        exatamente o que foi visto naquele instante.

        Se o esporte nao suportar algum mercado pedido (ex.: `btts` no
        endpoint ao vivo), o mercado e removido e a captura segue com os
        demais — em vez de perder a captura inteira.
        """
        moment = now or datetime.now(timezone.utc)
        stamp = moment.strftime(UTC_FORMAT)
        report = CaptureReport(captured_at=stamp, sport_keys=list(sport_keys))

        for sport in sport_keys:
            events, headers, used_markets = self._fetch_with_fallback(sport, report)
            if events is None:
                continue

            report.credits_last = _int_or_none(headers.get("x-requests-last"))
            report.credits_used = _int_or_none(headers.get("x-requests-used"))
            report.credits_remaining = _int_or_none(headers.get("x-requests-remaining"))
            report.markets_used[sport] = used_markets

            if not events:
                continue

            with_odds = [e for e in events if e.get("bookmakers")]
            report.events += len(events)
            report.events_with_odds += len(with_odds)
            if not with_odds:
                continue

            self.cache.save(OddsSnapshot(
                sport_key=sport,
                requested_date=stamp,
                timestamp=stamp,
                events=tuple(with_odds),
                provider="the-odds-api-live",
            ))
            report.snapshots_saved += 1

        _append_manifest(self.cache.root.parent / "manifest.json", "capture", report)
        return report

    def _fetch_with_fallback(
        self,
        sport: str,
        report: CaptureReport,
    ) -> tuple[list[dict] | None, dict[str, str], list[str]]:
        """Busca odds removendo mercados nao suportados, um a um."""
        markets = [m.strip() for m in self.markets.split(",") if m.strip()]
        for _ in range(len(markets) + 1):
            if not markets:
                report.errors.append(f"{sport}: nenhum mercado valido restou")
                return None, {}, []
            try:
                events, headers = self.provider.live_odds_with_meta(
                    sport, regions=self.regions, markets=",".join(markets)
                )
                return events, headers, list(markets)
            except ProviderError as exc:
                message = str(exc)
                unsupported = _unsupported_markets(message)
                if not unsupported:
                    report.errors.append(f"{sport}: {exc}")
                    return None, {}, []
                for bad in unsupported:
                    if bad in markets:
                        markets.remove(bad)
                        report.errors.append(
                            f"{sport}: mercado '{bad}' nao suportado neste "
                            f"endpoint — removido e tentando de novo"
                        )
        return None, {}, []

    def capture_pending_matches(
        self,
        matches: Sequence[HistoricalMatch],
        sport_key: str,
        now: datetime | None = None,
    ) -> CaptureReport:
        """Captura apenas se houver partidas FUTURAS ainda sem snapshot.

        Evita gastar cota quando nao ha nada novo para capturar.
        """
        moment = now or datetime.now(timezone.utc)
        stamp = moment.strftime(UTC_FORMAT)
        pending = [
            m for m in matches
            if utc_key(m.kickoff, m.timezone) > stamp
        ]
        report = CaptureReport(captured_at=stamp, sport_keys=[sport_key])
        if not pending:
            report.errors.append(
                "nenhuma partida futura no corpus: nada a capturar"
            )
            return report
        return self.capture([sport_key], now=moment)


_UNSUPPORTED_RE = re.compile(
    r"Markets not supported by this endpoint:\s*([a-zA-Z0-9_,\s]+)"
)


def _unsupported_markets(message: str) -> list[str]:
    """Extrai os mercados rejeitados da mensagem de erro da API."""
    match = _UNSUPPORTED_RE.search(message)
    if not match:
        return []
    return [m.strip() for m in match.group(1).split(",") if m.strip()]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
