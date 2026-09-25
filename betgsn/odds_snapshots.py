"""BETGSN :: odds_snapshots — Persistencia append-only de odds para CLV.

Cada observacao de preco e gravada com timestamp. Nada e sobrescrito.
"Closing odds" e uma DEFINICAO OPERACIONAL: a ultima observacao antes do
kickoff, exigindo que ela esteja dentro de uma janela maxima. Se nao houver
observacao valida, o CLV e None — nunca inventado.
"""
from __future__ import annotations

import sqlite3
import math
import statistics
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .timeutil import now_utc, parse_kickoff, utc_key

#: v4: clv_entries ganha colunas de PROVENIENCIA da decisao (home, away,
#: league, casa representativa da entrada, execution_status) — a entrada
#: deixa de ser apenas um preco e passa a ser rastreavel ate o evento e
#: a casa que a sustentaram. Bases v3 sao migradas por ALTER TABLE.
SCHEMA_VERSION = 5

#: Fonte canonica das entradas de CLV prospectivo: o registro acontece em
#: `real_signal_report`, no instante da decisao, a partir de `line_at`.
CLV_ENTRY_SOURCE = "real_signal_report"

#: Estados do ciclo de vida de uma entrada de CLV (Etapa de validacao
#: market/CLV). Distincao obrigatoria: "sem fechamento AINDA" (kickoff
#: no futuro) e diferente de "sem fechamento NUNCA" (kickoff passou e
#: nenhuma observacao valida na janela). Ausencia de fechamento NUNCA
#: vira CLV=0.
#:
#:   PENDING   entrada congelada, kickoff ainda nao aconteceu — o
#:             fechamento ainda pode chegar;
#:   NO_CLOSE   kickoff passou e nao ha observacao valida na janela de
#:             fechamento;
#:   CLOSED     fechamento valido apos a entrada — CLV calculado;
#:   INVALID    dado inconsistente (fechamento anterior a entrada, odd
#:             invalida, timestamps fora de ordem);
#:   MISMATCH   a entrada nao resolve a NENHUMA observacao do store
#:             para aquela linha (identidade canonica divergente).
CLV_LIFECYCLE_STATES = (
    "PENDING", "NO_CLOSE", "CLOSED", "INVALID", "MISMATCH",
)

#: Sem execucao real registrada, o preco de execucao e DESCONHECIDO —
#: nunca presumido igual ao preco observado/selecionado.
CLV_EXECUTION_UNKNOWN = "UNKNOWN"


def _default_db() -> Path:
    """Caminho default do store operacional (respeita BETGSN_OUTPUT_DIR)."""
    from .config import output_root

    return output_root() / "odds_snapshots.db"


#: Colunas de health persistido. Sao EXATAMENTE os campos de
#: `ProviderHealth.to_dict()` (odds_health) mais a quota observada do
#: CreditController — nada alem do observado. NULL = nunca observado.
_PROVIDER_HEALTH_COLUMNS = (
    "name", "state", "consecutive_failures", "total_failures",
    "total_successes", "last_success_at", "last_failure_at", "last_error",
    "last_status", "last_kind", "credits_remaining", "observations",
    "updated_at", "latency_ms", "credits_used", "credits_remaining_known",
)

#: Uma observacao so conta como "closing" se estiver a no maximo X minutos
#: do kickoff. Sem isso, uma odd de 3 dias antes viraria "fechamento".
CLOSING_WINDOW_MINUTES = 120.0


@dataclass(frozen=True)
class OddsObservation:
    """Uma cotacao observada num instante."""

    match_key: str
    market: str
    outcome: str
    bookmaker: str
    odd: float
    timestamp: str
    kickoff: str
    provider: str = ""
    is_opening: bool = False
    is_closing: bool = False
    #: origem do timestamp: QUOTE_TIMESTAMP (por outcome) / EVENT_TIMESTAMP /
    #: CAPTURE_TIMESTAMP (fetched_at) / UNKNOWN. Nunca apresentar um
    #: fetched_at como horario individual do bookmaker.
    timestamp_source: str = "QUOTE_TIMESTAMP"

    def __post_init__(self) -> None:
        if not math.isfinite(self.odd) or self.odd <= 1.0:
            raise ValueError(f"odd decimal precisa ser > 1.0, recebi {self.odd!r}")
        if not self.timestamp:
            raise ValueError("observacao de odds exige timestamp")
        if not self.kickoff:
            raise ValueError("observacao de odds exige kickoff")
        if utc_key(self.timestamp) >= utc_key(self.kickoff):
            raise ValueError("odds observadas depois do kickoff nao sao utilizaveis")

    @property
    def minutes_before_kickoff(self) -> float:
        delta = parse_kickoff(self.kickoff) - parse_kickoff(self.timestamp)
        return delta.total_seconds() / 60.0


def observations_from_quotes(
    quotes: Sequence,
    *,
    default_provider: str = "",
    match_keys: Optional[Mapping[str, str]] = None,
) -> list[OddsObservation]:
    """Converte cotacoes normalizadas em observacoes persistiveis.

    O `match_key` e `quote.event_id` — a MESMA chave canonica produzida por
    `odds_normalize.event_key` (mandante|visitante|kickoff UTC). A chave nao
    e derivada de novo aqui, apenas transportada: escrita e leitura precisam
    falar do mesmo jogo.

    `match_keys` (opcional) substitui a chave por evento: mapeia
    `event_id` do provider -> `event_key` do FIXTURE casado via
    `FixtureMatchIndex`. So a chave muda — odd, timestamp e kickoff sao os
    observados; nada e reescrito para caber no contrato.

    Cotacoes pos-kickoff ou com preco invalido sao descartadas. A observacao
    nunca e "corrigida" para caber no contrato: sem dado utilizavel, nada e
    gravado.
    """
    out: list[OddsObservation] = []
    for quote in quotes:
        if not getattr(quote, "pre_kickoff", False):
            continue
        key = quote.event_id
        if match_keys and quote.event_id in match_keys:
            key = match_keys[quote.event_id]
        try:
            out.append(
                OddsObservation(
                    match_key=key,
                    market=quote.market,
                    outcome=quote.selection,
                    bookmaker=quote.bookmaker,
                    odd=quote.price,
                    timestamp=quote.timestamp,
                    kickoff=quote.kickoff,
                    provider=getattr(quote, "provider", "") or default_provider,
                    timestamp_source=getattr(
                        quote, "timestamp_source", "QUOTE_TIMESTAMP"
                    ) or "QUOTE_TIMESTAMP",
                )
            )
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class LineSnapshot:
    """Linha agregada num instante: MEDIANA entre as casas observadas.

    Metodologia do CLV prospectivo (I-07): entrada e fechamento sao
    MEDIANA vs MEDIANA, mesma fonte (este store), mesma populacao
    metodologica (ultima observacao de cada casa). Nunca MAX na entrada
    contra MEDIANA no fechamento.
    """

    odd: float
    timestamp: str
    bookmaker: str
    n_books: int


@dataclass(frozen=True)
class ClvEntryRecord:
    """Entrada de CLV congelada (FIRST-WINS) no store.

    `entry_odd`/`entry_timestamp` vem SEMPRE de observacao real
    (`line_at` no instante da decisao): nunca odd sintetica, nunca
    `prediction_timestamp` no lugar do timestamp da odd.

    QUATRO preços DISTINTOS no ciclo CLV — nunca presumidos iguais:

      observed_price   `entry_odd` — a MEDIANA das casas observadas no
                       instante da decisão (o que o mercado estava
                       cotando), gravada aqui;
      selected_price   a melhor odd escolhida pela estratégia
                       (`fx.best_odds`, line-shopping) — vive no sinal,
                       NÃO aqui: a entrada de CLV é a linha observada,
                       não o preço selecionado;
      execution_price  DESCONHECIDO — `execution_status = UNKNOWN`
                       enquanto não houver registro real de execução
                       (nunca presumido igual ao observado/selecionado);
      closing_price    `closing_odd` do resultado, SEMPRE posterior à
                       entrada e anterior ao kickoff.

    Proveniencia (schema v4): `home`/`away`/`league` identificam o
    evento, `entry_bookmaker` e a casa representativa da mediana de
    entrada, e `execution_status` separa o preco OBSERVADO na decisao
    do preco de EXECUCAO.
    """

    id: int
    match_key: str
    market: str
    outcome: str
    entry_odd: float
    entry_timestamp: str
    entry_n_books: int
    kickoff: str
    prediction_timestamp: str
    source: str
    created_at: str
    home: str = ""
    away: str = ""
    league: str = ""
    entry_bookmaker: str = ""
    execution_status: str = CLV_EXECUTION_UNKNOWN


@dataclass(frozen=True)
class CLVResult:
    """Closing Line Value de uma aposta."""

    match_key: str
    market: str
    outcome: str
    entry_odd: float
    entry_implied: float
    closing_odd: Optional[float] = None
    closing_implied: Optional[float] = None
    clv_price: Optional[float] = None
    clv_probability: Optional[float] = None
    clv_percentage: Optional[float] = None
    closing_bookmaker: str = ""
    closing_timestamp: str = ""
    closing_minutes_before: Optional[float] = None
    n_books_closing: int = 0
    status: str = "NO_CLOSING_ODDS"
    entry_timestamp: str = ""
    closing_after_entry: bool = False

    @property
    def valid(self) -> bool:
        return self.status == "OK"


@dataclass(frozen=True)
class OddsMovement:
    """Movimento de uma linha entre a abertura e o ultimo preco observado.

    `status` e explicito: "OK" so quando ha ao menos duas observacoes.
    Sem dado suficiente, os campos ficam None — nunca zero, que seria
    indistinguivel de "o preco nao se moveu".
    """

    match_key: str
    market: str
    outcome: str
    status: str = "NO_DATA"
    opening_odd: Optional[float] = None
    current_odd: Optional[float] = None
    price_delta: Optional[float] = None
    price_delta_pct: Optional[float] = None
    implied_probability_delta: Optional[float] = None
    n_observations: int = 0
    n_books: int = 0
    first_timestamp: str = ""
    last_timestamp: str = ""
    minutes_between: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "match_key": self.match_key,
            "market": self.market,
            "outcome": self.outcome,
            "status": self.status,
            "opening_odd": self.opening_odd,
            "current_odd": self.current_odd,
            "price_delta": self.price_delta,
            "price_delta_pct": self.price_delta_pct,
            "implied_probability_delta": self.implied_probability_delta,
            "n_observations": self.n_observations,
            "n_books": self.n_books,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "minutes_between": self.minutes_between,
        }


@dataclass
class CLVCoverage:
    """Cobertura de CLV. Nunca reportar CLV agregado sem isso."""

    total_bets: int = 0
    bets_with_clv: int = 0
    avg_clv_percentage: Optional[float] = None
    median_clv_percentage: Optional[float] = None
    avg_clv_probability: Optional[float] = None
    positive_clv_rate: Optional[float] = None
    by_market: dict = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.bets_with_clv / self.total_bets if self.total_bets else 0.0

    def to_dict(self) -> dict:
        return {
            "total_bets": self.total_bets,
            "bets_with_clv": self.bets_with_clv,
            "coverage": round(self.coverage, 4),
            "avg_clv_percentage": self.avg_clv_percentage,
            "median_clv_percentage": self.median_clv_percentage,
            "avg_clv_probability": self.avg_clv_probability,
            "positive_clv_rate": self.positive_clv_rate,
            "by_market": self.by_market,
        }


@dataclass(frozen=True)
class ClvLifecycle:
    """Estado do ciclo de vida de UMA entrada de CLV.

    `result` so existe quando ha calculo (CLOSED) ou fechamento
    inconsistente (INVALID com closing) — nos demais estados o CLV e
    simplesmente inexistente, nunca zero.
    """

    entry: ClvEntryRecord
    state: str
    result: Optional[CLVResult] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "match_key": self.entry.match_key,
            "market": self.entry.market,
            "outcome": self.entry.outcome,
            "kickoff": self.entry.kickoff,
            "state": self.state,
            "detail": self.detail,
            "entry_odd": self.entry.entry_odd,
            "entry_timestamp": self.entry.entry_timestamp,
            "closing_odd": self.result.closing_odd if self.result else None,
            "closing_timestamp": (
                self.result.closing_timestamp if self.result else None),
            "clv_percentage": (
                self.result.clv_percentage if self.result else None),
            "execution_status": self.entry.execution_status,
        }


@dataclass(frozen=True)
class ClvLifecycleSummary:
    """Classificacao do ciclo de vida de TODAS as entradas do store."""

    evaluated_at: str
    by_state: dict
    n_entries: int
    lifecycles: list  # list[ClvLifecycle]

    @property
    def n_closed(self) -> int:
        return self.by_state.get("CLOSED", 0)

    @property
    def n_pending(self) -> int:
        return self.by_state.get("PENDING", 0)

    def to_dict(self) -> dict:
        return {
            "evaluated_at": self.evaluated_at,
            "n_entries": self.n_entries,
            "by_state": dict(self.by_state),
            "entries": [lc.to_dict() for lc in self.lifecycles],
        }


def _quantile(values: list[float], q: float) -> Optional[float]:
    """Quantil empirico (mesma definicao do clv_report)."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(q * len(ordered)))
    return round(ordered[idx], 6)


def clv_statistics(summary: ClvLifecycleSummary) -> dict:
    """Estatisticas do CLV VALIDO (estado CLOSED) de um sweep.

    Nulas-seguras por contrato: n=0 => mean/median/quantis/positive_rate
    None — nunca 0 (0 diria "medimos e o CLV e zero", mentira). O CLV
    so existe no estado CLOSED; PENDING/NO_CLOSE/INVALID/MISMATCH nao
    entram na amostra e nao viram zero.
    """
    pcts = [
        lc.result.clv_percentage
        for lc in summary.lifecycles
        if lc.state == "CLOSED" and lc.result is not None
        and lc.result.clv_percentage is not None
    ]
    closings = [
        lc.result.closing_timestamp
        for lc in summary.lifecycles
        if lc.state == "CLOSED" and lc.result is not None
        and lc.result.closing_timestamp
    ]
    return {
        "n": len(pcts),
        "mean": round(statistics.fmean(pcts), 6) if pcts else None,
        "median": (
            round(statistics.median(pcts), 6) if pcts else None),
        "p10": _quantile(pcts, 0.10),
        "p25": _quantile(pcts, 0.25),
        "p75": _quantile(pcts, 0.75),
        "p90": _quantile(pcts, 0.90),
        "positive_rate": (
            round(sum(1 for p in pcts if p > 0) / len(pcts), 6)
            if pcts else None
        ),
        "last_closing_timestamp": max(closings) if closings else None,
    }


def sweep_close_rate(summary: ClvLifecycleSummary) -> Optional[float]:
    """Taxa de fechamento: CLOSED / (CLOSED + NO_CLOSE).

    Populacao: entradas validas cujo kickoff ja passou (o fechamento ja
    podia ter sido observado). PENDING sai do denominador (ainda pode
    fechar); INVALID/MISMATCH sao problemas de dado, medidos a parte.
    Sem populacao => None (taxa nao medida), nunca 0.
    """
    closed = summary.by_state.get("CLOSED", 0)
    no_close = summary.by_state.get("NO_CLOSE", 0)
    denominator = closed + no_close
    if not denominator:
        return None
    return round(closed / denominator, 4)


def sweep_resolve_rate(summary: ClvLifecycleSummary) -> Optional[float]:
    """Taxa de resolucao de identidade: 1 - MISMATCH/n_entries.

    Entradas que resolvem a observacoes do store (identidade canonica
    casada). Store vazio => None (nao medido)."""
    if not summary.n_entries:
        return None
    mismatch = summary.by_state.get("MISMATCH", 0)
    return round(1.0 - mismatch / summary.n_entries, 4)


class OddsSnapshotStore:
    """Store append-only de observacoes de odds."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else _default_db()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY, value TEXT
                    );
                    CREATE TABLE IF NOT EXISTS odds_observations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_key TEXT NOT NULL,
                        market TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        bookmaker TEXT NOT NULL,
                        odd REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        kickoff TEXT NOT NULL,
                        provider TEXT DEFAULT '',
                        is_opening INTEGER DEFAULT 0,
                        is_closing INTEGER DEFAULT 0,
                        minutes_before_kickoff REAL,
                        timestamp_source TEXT DEFAULT 'QUOTE_TIMESTAMP',
                        UNIQUE(match_key, market, outcome, bookmaker, timestamp)
                    );
                    CREATE INDEX IF NOT EXISTS idx_obs_line
                        ON odds_observations(match_key, market, outcome);
                    CREATE INDEX IF NOT EXISTS idx_obs_ts
                        ON odds_observations(match_key, timestamp);
                    CREATE TABLE IF NOT EXISTS provider_health (
                        name TEXT PRIMARY KEY,
                        state TEXT NOT NULL,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        total_failures INTEGER NOT NULL DEFAULT 0,
                        total_successes INTEGER NOT NULL DEFAULT 0,
                        last_success_at TEXT,
                        last_failure_at TEXT,
                        last_error TEXT,
                        last_status INTEGER,
                        last_kind TEXT,
                        credits_remaining INTEGER,
                        observations INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT,
                        latency_ms REAL,
                        credits_used INTEGER,
                        credits_remaining_known INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS clv_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_key TEXT NOT NULL,
                        market TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        entry_odd REAL NOT NULL,
                        entry_timestamp TEXT NOT NULL,
                        entry_n_books INTEGER NOT NULL,
                        kickoff TEXT NOT NULL,
                        prediction_timestamp TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(match_key, market, outcome, source)
                    );
                    CREATE INDEX IF NOT EXISTS idx_clv_entries_line
                        ON clv_entries(match_key, market, outcome);
                    CREATE TABLE IF NOT EXISTS execution_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_key TEXT NOT NULL,
                        market TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        executed_price REAL NOT NULL,
                        executed_at TEXT NOT NULL,
                        observed_price REAL,
                        decision_timestamp TEXT,
                        recorded_at TEXT NOT NULL,
                        UNIQUE(match_key, market, outcome)
                    );
                    CREATE INDEX IF NOT EXISTS idx_exec_line
                        ON execution_records(match_key, market, outcome);
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta VALUES (?, ?)",
                    ("version", str(SCHEMA_VERSION)),
                )
                self._migrate_clv_entries(conn)
                self._migrate_odds_observations(conn)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _migrate_clv_entries(conn: sqlite3.Connection) -> None:
        """v3 -> v4: colunas de proveniencia da decisao em clv_entries.

        ALTER TABLE ADD COLUMN com defaults: linhas antigas ficam com
        string vazia (proveniencia nao registrada na epoca — declarado,
        nunca inventado) e execution_status UNKNOWN. Nada e sobrescrito;
        o store continua append-only.
        """
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(clv_entries)")
        }
        additions = (
            ("home", "TEXT DEFAULT ''"),
            ("away", "TEXT DEFAULT ''"),
            ("league", "TEXT DEFAULT ''"),
            ("entry_bookmaker", "TEXT DEFAULT ''"),
            ("execution_status", "TEXT DEFAULT 'UNKNOWN'"),
        )
        for col, decl in additions:
            if col not in cols:
                conn.execute(
                    f"ALTER TABLE clv_entries ADD COLUMN {col} {decl}")

    @staticmethod
    def _migrate_odds_observations(conn: sqlite3.Connection) -> None:
        """v4 -> v5: origem do timestamp em odds_observations.

        Linhas antigas ficam com o default declarado QUOTE_TIMESTAMP — a
        origem nao era registrada na epoca; o valor e o rotulo conservador
        (nao fabrica CAPTURE nem inventa horario de bookmaker).
        """
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(odds_observations)")
        }
        if "timestamp_source" not in cols:
            conn.execute(
                "ALTER TABLE odds_observations ADD COLUMN "
                "timestamp_source TEXT DEFAULT 'QUOTE_TIMESTAMP'"
            )

    # ------------------------------------------------------------- escrita

    def add(self, observations: Sequence[OddsObservation]) -> int:
        """Grava observacoes. Duplicata exata e ignorada, nunca sobrescrita."""
        if not observations:
            return 0
        rows = [
            (
                o.match_key, o.market, o.outcome, o.bookmaker, o.odd,
                utc_key(o.timestamp), utc_key(o.kickoff), o.provider,
                int(o.is_opening), int(o.is_closing),
                round(o.minutes_before_kickoff, 3),
                o.timestamp_source,
            )
            for o in observations
        ]
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.executemany(
                    """INSERT OR IGNORE INTO odds_observations
                       (match_key, market, outcome, bookmaker, odd, timestamp,
                        kickoff, provider, is_opening, is_closing,
                        minutes_before_kickoff, timestamp_source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    # ------------------------------------------------------------- leitura

    def save_provider_health(
        self,
        health: Mapping[str, dict],
        credits: Mapping[str, dict],
    ) -> None:
        """Upsert do health de providers observado por OUTRO processo.

        A captura de odds roda em processo separado da API; este store e o
        canal compartilhado entre eles. Os registros vem de
        `HealthTracker.snapshot()` / `CreditController.snapshot()` — apenas
        dados observados (sucessos, falhas, latencia medida, quota de
        headers). Campos ausentes viram NULL, nunca defaults inventados.

        `credits` mapeia nome -> registro de credito; um provider presente
        em `health` mas ausente em `credits` persiste quota NULL.
        """
        rows = []
        for name, record in sorted(health.items()):
            credit = credits.get(name) or {}
            rows.append(
                (
                    name,
                    str(record.get("state", "")),
                    int(record.get("consecutive_failures", 0) or 0),
                    int(record.get("total_failures", 0) or 0),
                    int(record.get("total_successes", 0) or 0),
                    record.get("last_success_at") or None,
                    record.get("last_failure_at") or None,
                    record.get("last_error") or None,
                    record.get("last_status"),
                    record.get("last_kind") or None,
                    record.get("credits_remaining"),
                    int(record.get("observations", 0) or 0),
                    record.get("updated_at") or None,
                    record.get("latency_ms"),
                    credit.get("used"),
                    credit.get("known_remaining"),
                )
            )
        if not rows:
            return
        with self._lock:
            conn = self._conn()
            try:
                conn.executemany(
                    """INSERT OR REPLACE INTO provider_health
                       (name, state, consecutive_failures, total_failures,
                        total_successes, last_success_at, last_failure_at,
                        last_error, last_status, last_kind, credits_remaining,
                        observations, updated_at, latency_ms, credits_used,
                        credits_remaining_known)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def load_provider_health(self) -> dict[str, dict]:
        """Health persistido por processo capturador, no formato de
        `HealthTracker.snapshot()` — {nome: registro}. Banco sem health
        devolve ``{}`` (ausencia, nunca UNKNOWN sintetico aqui)."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM provider_health"
                ).fetchall()
            finally:
                conn.close()
        out: dict[str, dict] = {}
        for r in rows:
            out[r["name"]] = {
                "provider": r["name"],
                "state": r["state"],
                "consecutive_failures": r["consecutive_failures"],
                "total_failures": r["total_failures"],
                "total_successes": r["total_successes"],
                "last_success_at": r["last_success_at"] or "",
                "last_failure_at": r["last_failure_at"] or "",
                "last_error": r["last_error"] or "",
                "last_status": r["last_status"],
                "last_kind": r["last_kind"] or "",
                "credits_remaining": r["credits_remaining"],
                "observations": r["observations"],
                "updated_at": r["updated_at"] or "",
                "latency_ms": r["latency_ms"],
            }
        return out

    def load_provider_credits(self) -> dict[str, dict]:
        """Quota persistida por processo capturador, no formato de
        `CreditController.snapshot()` — {nome: registro}."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT name, credits_used, credits_remaining_known "
                    "FROM provider_health"
                ).fetchall()
            finally:
                conn.close()
        out: dict[str, dict] = {}
        for r in rows:
            out[r["name"]] = {
                "provider": r["name"],
                "daily_limit": 0,
                "used": r["credits_used"],
                "remaining": r["credits_remaining_known"],
                "known_remaining": r["credits_remaining_known"],
                "exhausted": bool(
                    r["credits_remaining_known"] is not None
                    and r["credits_remaining_known"] <= 0
                ),
                "last_updated": "",
            }
        return out

    def observations_before(
        self, match_key: str, cutoff: str
    ) -> list[OddsObservation]:
        """Observacoes estritamente anteriores ao cutoff. Point-in-time."""
        key = utc_key(cutoff)
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM odds_observations
                       WHERE match_key = ? AND timestamp < ?
                       ORDER BY timestamp""",
                    (match_key, key),
                ).fetchall()
            finally:
                conn.close()
        return [self._to_obs(r) for r in rows]

    def all_observations(self, match_key: str) -> list[OddsObservation]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM odds_observations WHERE match_key = ? ORDER BY timestamp",
                    (match_key,),
                ).fetchall()
            finally:
                conn.close()
        return [self._to_obs(r) for r in rows]

    def latest_observation_stamp(
        self,
        match_keys: Sequence[str] = (),
        cutoff: str = "",
    ) -> tuple[Optional[str], list[str]]:
        """(maior timestamp, providers observados) sem ler observacoes.

        Leitura de RESUMO para proveniencia/dashboard: devolve o carimbo
        da observacao mais recente e os providers que gravaram no store —
        nada alem do observado. `match_keys` vazio considera o store
        inteiro (visao operacional); `cutoff` nao vazio restringe a
        observacoes point-in-time (`timestamp <= cutoff`). Store vazio
        devolve (None, []) — ausencia explicita, nunca carimbo fabricado.
        """
        with self._lock:
            conn = self._conn()
            try:
                sql = "SELECT timestamp, provider FROM odds_observations"
                clauses: list[str] = []
                params: list = []
                if match_keys:
                    marks = ",".join("?" for _ in match_keys)
                    clauses.append(f"match_key IN ({marks})")
                    params.extend(match_keys)
                if cutoff:
                    clauses.append("timestamp <= ?")
                    params.append(utc_key(cutoff))
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
                sql += " ORDER BY timestamp DESC"
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        if not rows:
            return None, []
        providers = sorted({r["provider"] for r in rows if r["provider"]})
        return rows[0]["timestamp"], providers

    def closing_line(
        self,
        match_key: str,
        market: str,
        outcome: str,
        window_minutes: float = CLOSING_WINDOW_MINUTES,
        *, after: str = "",
    ) -> Optional[tuple[float, str, str, float, int]]:
        """Retorna (odd_mediana, book, timestamp, minutos_antes, n_books).

        Pega a ULTIMA observacao de cada casa antes do kickoff, exige que a
        mais recente esteja dentro da janela, e usa a mediana entre casas.
        Devolve None quando nao ha observacao valida.
        """
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT bookmaker, odd, timestamp, minutes_before_kickoff
                       FROM odds_observations o
                       WHERE match_key = ? AND market = ? AND outcome = ?
                         AND timestamp = (
                             SELECT MAX(timestamp) FROM odds_observations i
                             WHERE i.match_key = o.match_key
                               AND i.market = o.market
                               AND i.outcome = o.outcome
                               AND i.bookmaker = o.bookmaker
                         )""",
                    (match_key, market, outcome),
                ).fetchall()
            finally:
                conn.close()

        if not rows:
            return None
        usable = [r for r in rows
                  if r["minutes_before_kickoff"] is not None
                  and 0 < r["minutes_before_kickoff"] <= window_minutes
                  and (not after or r["timestamp"] > utc_key(after))]
        if not usable:
            return None

        odds = [r["odd"] for r in usable]
        median_odd = statistics.median(odds)
        best = min(usable, key=lambda r: abs(r["odd"] - median_odd))
        return (
            round(median_odd, 4),
            best["bookmaker"],
            max(r["timestamp"] for r in usable),
            round(best["minutes_before_kickoff"], 2),
            len(usable),
        )

    # ------------------------------------------------------------- movimento

    def observations_at_or_before(
        self, match_key: str, cutoff: str
    ) -> list[OddsObservation]:
        """Observacoes ate o cutoff, inclusive. Consulta 'as of'."""
        key = utc_key(cutoff)
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM odds_observations
                       WHERE match_key = ? AND timestamp <= ?
                       ORDER BY timestamp""",
                    (match_key, key),
                ).fetchall()
            finally:
                conn.close()
        return [self._to_obs(r) for r in rows]

    def latest_observation(
        self, match_key: str, market: str, outcome: str
    ) -> Optional[tuple[float, str, int]]:
        """(odd_mediana_das_ultimas_por_casa, timestamp, n_books) ou None.

        A mediana entre casas evita que uma unica casa atrasada determine o
        preco "atual".
        """
        return self._aggregate_line(match_key, market, outcome, last=True)

    def opening_line(
        self, match_key: str, market: str, outcome: str
    ) -> Optional[tuple[float, str, int]]:
        """(odd_mediana_das_primeiras_por_casa, timestamp, n_books) ou None."""
        return self._aggregate_line(match_key, market, outcome, last=False)

    def _aggregate_line(
        self, match_key: str, market: str, outcome: str, last: bool
    ) -> Optional[tuple[float, str, int]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT bookmaker, odd, timestamp, id FROM odds_observations
                       WHERE match_key = ? AND market = ? AND outcome = ?
                       ORDER BY bookmaker, timestamp, id""",
                    (match_key, market, outcome),
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return None

        per_book: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            per_book.setdefault(row["bookmaker"], []).append(row)
        selected = [pts[-1] if last else pts[0] for pts in per_book.values()]
        odds = [r["odd"] for r in selected]
        median_odd = statistics.median(odds)
        reference = (
            max(selected, key=lambda r: r["timestamp"])
            if last
            else min(selected, key=lambda r: r["timestamp"])
        )
        return (
            round(median_odd, 4),
            reference["timestamp"],
            len(selected),
        )

    def movement(
        self,
        match_key: str,
        market: str,
        outcome: str,
    ) -> OddsMovement:
        """Movimento abertura -> ultimo preco. Nunca inventa dado ausente."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT * FROM odds_observations
                       WHERE match_key = ? AND market = ? AND outcome = ?
                       ORDER BY timestamp, id""",
                    (match_key, market, outcome),
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return OddsMovement(match_key=match_key, market=market, outcome=outcome)

        per_book: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            per_book.setdefault(row["bookmaker"], []).append(row)
        opening = statistics.median([pts[0]["odd"] for pts in per_book.values()])
        current = statistics.median([pts[-1]["odd"] for pts in per_book.values()])
        first_ts = rows[0]["timestamp"]
        last_ts = rows[-1]["timestamp"]
        minutes = (
            parse_kickoff(last_ts) - parse_kickoff(first_ts)
        ).total_seconds() / 60.0

        if len(rows) < 2:
            return OddsMovement(
                match_key=match_key,
                market=market,
                outcome=outcome,
                status="INSUFFICIENT_DATA",
                opening_odd=round(opening, 4),
                current_odd=round(current, 4),
                n_observations=len(rows),
                n_books=len(per_book),
                first_timestamp=first_ts,
                last_timestamp=last_ts,
                minutes_between=round(minutes, 3),
            )

        delta = current - opening
        return OddsMovement(
            match_key=match_key,
            market=market,
            outcome=outcome,
            status="OK",
            opening_odd=round(opening, 4),
            current_odd=round(current, 4),
            price_delta=round(delta, 4),
            price_delta_pct=round(delta / opening, 6) if opening else None,
            implied_probability_delta=round(1.0 / current - 1.0 / opening, 6),
            n_observations=len(rows),
            n_books=len(per_book),
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            minutes_between=round(minutes, 3),
        )

    def staleness_seconds(
        self, now: str, match_key: Optional[str] = None
    ) -> Optional[float]:
        """Segundos desde a observacao mais recente. None quando nao ha dado."""
        with self._lock:
            conn = self._conn()
            try:
                if match_key is None:
                    row = conn.execute(
                        "SELECT MAX(timestamp) AS ts FROM odds_observations"
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT MAX(timestamp) AS ts FROM odds_observations"
                        " WHERE match_key = ?",
                        (match_key,),
                    ).fetchone()
            finally:
                conn.close()
        last = row["ts"] if row else None
        if not last:
            return None
        return (parse_kickoff(now) - parse_kickoff(last)).total_seconds()

    # ------------------------------------------------------------- CLV

    def line_at(
        self, match_key: str, market: str, outcome: str, at: str
    ) -> Optional[LineSnapshot]:
        """Linha (mediana entre casas) tal como existia no instante `at`.

        Point-in-time estrito: so entram observacoes com timestamp <= at.
        Para cada casa considera-se a ULTIMA observacao visivel; a odd e
        a MEDIANA entre as casas utilizadas. O timestamp e o maior entre
        as observacoes usadas e o bookmaker representativo e a casa cuja
        odd esta mais proxima da mediana. `n_books` reflete as casas
        realmente utilizadas.

        Sem observacao valida devolve None — nunca odd sintetica, nunca
        `at` no lugar do timestamp da observacao.
        """
        key = utc_key(at)
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT bookmaker, odd, timestamp, id FROM odds_observations
                       WHERE match_key = ? AND market = ? AND outcome = ?
                         AND timestamp <= ?
                       ORDER BY bookmaker, timestamp, id""",
                    (match_key, market, outcome, key),
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return None

        # ultima observacao visivel de cada casa (rows ja ordenadas)
        per_book: dict[str, sqlite3.Row] = {}
        for row in rows:
            per_book[row["bookmaker"]] = row
        selected = list(per_book.values())
        odds = [r["odd"] for r in selected]
        median_odd = statistics.median(odds)
        representative = min(selected, key=lambda r: abs(r["odd"] - median_odd))
        return LineSnapshot(
            odd=round(median_odd, 4),
            timestamp=max(r["timestamp"] for r in selected),
            bookmaker=representative["bookmaker"],
            n_books=len(selected),
        )

    def register_entry(
        self,
        match_key: str,
        market: str,
        outcome: str,
        entry_odd: float,
        entry_timestamp: str,
        entry_n_books: int,
        kickoff: str,
        prediction_timestamp: str,
        source: str = CLV_ENTRY_SOURCE,
        *,
        home: str = "",
        away: str = "",
        league: str = "",
        entry_bookmaker: str = "",
        execution_status: str = CLV_EXECUTION_UNKNOWN,
    ) -> bool:
        """Registra entrada de CLV, congelada FIRST-WINS.

        Contrato da entrada (I-02): `entry_odd`/`entry_timestamp` sao
        produzidos por `line_at` no instante da decisao — observacao
        REAL, nunca `fx.best_odds` atual nem `prediction_timestamp` no
        lugar do timestamp da odd. A garantia point-in-time e verificada
        aqui: entry_timestamp NAO pode ser posterior ao instante da
        decisao nem alcancar o kickoff.

        Proveniencia (schema v4): `home`/`away`/`league`/`entry_bookmaker`
        rastreiam a entrada ate o evento e a casa que sustentaram a
        mediana. `execution_status` comeca UNKNOWN: o preco observado na
        decisao NAO e o preco de execucao — sem execucao real registrada,
        nada e presumido.

        UNIQUE(match_key, market, outcome, source) + INSERT OR IGNORE =
        FIRST-WINS: o primeiro registro congela a entrada; chamadas
        posteriores nao alteram entry_odd nem entry_timestamp. Devolve
        True somente quando a linha foi inserida AGORA.
        """
        if not math.isfinite(entry_odd) or entry_odd <= 1.0:
            raise ValueError("entry_odd precisa ser > 1.0")
        if entry_n_books < 1:
            raise ValueError("entry_n_books precisa ser >= 1")
        if execution_status != CLV_EXECUTION_UNKNOWN:
            # A unica excecao legitima e UNKNOWN ate existir um caminho
            # de execucao real com feedback proprio. Nada de "EXECUTED"
            # fabricado no registro.
            raise ValueError(
                "execution_status so pode ser UNKNOWN no registro: "
                "execucao real e atestada por um caminho proprio, nunca "
                "presumida no instante da decisao"
            )
        entry_key = utc_key(entry_timestamp)
        prediction_key = utc_key(prediction_timestamp)
        if entry_key > prediction_key:
            raise ValueError(
                "entry_timestamp posterior ao instante da decisao: "
                "observacao pos-decisao nao pode definir a entrada (leakage)"
            )
        kickoff_key = utc_key(kickoff)
        if prediction_key >= kickoff_key:
            raise ValueError("prediction_timestamp precisa ser anterior ao kickoff")
        if entry_key >= kickoff_key:
            raise ValueError("entry_timestamp precisa ser anterior ao kickoff")

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO clv_entries
                       (match_key, market, outcome, entry_odd, entry_timestamp,
                        entry_n_books, kickoff, prediction_timestamp, source,
                        created_at, home, away, league, entry_bookmaker,
                        execution_status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        match_key, market, outcome, entry_odd, entry_key,
                        entry_n_books, kickoff_key, prediction_key, source,
                        now_utc(), home, away, league, entry_bookmaker,
                        execution_status,
                    ),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def clv_entries(self) -> list[ClvEntryRecord]:
        """Entradas registradas, em ordem de criacao (FIRST-WINS primeiro)."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM clv_entries ORDER BY created_at, id"
                ).fetchall()
            finally:
                conn.close()
        return [
            ClvEntryRecord(
                id=r["id"], match_key=r["match_key"], market=r["market"],
                outcome=r["outcome"], entry_odd=r["entry_odd"],
                entry_timestamp=r["entry_timestamp"],
                entry_n_books=r["entry_n_books"], kickoff=r["kickoff"],
                prediction_timestamp=r["prediction_timestamp"],
                source=r["source"], created_at=r["created_at"],
                home=r["home"] or "", away=r["away"] or "",
                league=r["league"] or "",
                entry_bookmaker=r["entry_bookmaker"] or "",
                execution_status=r["execution_status"] or "UNKNOWN",
            )
            for r in rows
        ]

    def clv_provenance(self, entry: ClvEntryRecord) -> dict:
        """Reconstruct the observed line; representative book is NOT execution.

        A median can lie between two actual quotes. All constituents are
        returned rather than attributing that synthetic aggregate to one book.
        """
        from dataclasses import asdict

        observations = [o for o in self.all_observations(entry.match_key)
                        if o.market == entry.market and o.outcome == entry.outcome
                        and utc_key(o.kickoff) == utc_key(entry.kickoff)]
        original = {}
        for quote in observations:
            if quote.timestamp <= utc_key(entry.prediction_timestamp):
                original[quote.bookmaker] = quote
        subsequent = [o for o in observations
                      if o.timestamp > utc_key(entry.prediction_timestamp)]
        latest = {}
        for quote in subsequent:
            if 0 < quote.minutes_before_kickoff <= CLOSING_WINDOW_MINUTES:
                latest[quote.bookmaker] = quote
        return {
            "entry_id": entry.id,
            "source": entry.source,
            "decision_price": entry.entry_odd,
            "decision_timestamp": entry.prediction_timestamp,
            "observed_price": entry.entry_odd,
            "observation_timestamp": entry.entry_timestamp,
            "selected_price": None,
            "execution_price": None,
            "execution_status": entry.execution_status,
            "aggregation": "median_of_latest_per_bookmaker",
            "original_quotes": [asdict(q) for q in original.values()],
            "subsequent_quotes": [asdict(q) for q in subsequent],
            "closing_candidates": [asdict(q) for q in latest.values()],
            "formula": "decision_price / closing_price - 1",
            "selected_price_note": "not persisted by historical FIRST_WINS; unknown",
        }

    # ------------------------------------------------------- ciclo de vida

    def clv_lifecycle(
        self,
        entry: ClvEntryRecord,
        *,
        now: Optional[str] = None,
        window_minutes: float = CLOSING_WINDOW_MINUTES,
    ) -> "ClvLifecycle":
        """Estado do ciclo de vida de uma entrada de CLV.

        Distincao obrigatoria entre "sem fechamento AINDA" (PENDING,
        kickoff no futuro) e "sem fechamento NUNCA" (NO_CLOSE, kickoff
        passou sem observacao valida). Ausencia de fechamento JAMAIS
        vira CLV=0: o valor so existe no estado CLOSED.

        Ordem de avaliacao:
          1. INVALID    — dado inconsistente (odd <= 1, entrada >= kickoff,
                          fechamento anterior a entrada);
          2. MISMATCH   — a entrada nao resolve a nenhuma observacao do
                          store para aquela linha (identidade canonica
                          divergente entre registro e observacoes);
          3. CLOSED     — clv_prospective devolve OK;
          4. PENDING    — sem fechamento e kickoff ainda no futuro;
          5. NO_CLOSE   — sem fechamento e kickoff ja passou.
        """
        now = now or now_utc()
        detail = ""
        result: Optional[CLVResult] = None

        if not math.isfinite(entry.entry_odd) or entry.entry_odd <= 1.0:
            state = "INVALID"
            detail = f"entry_odd invalida: {entry.entry_odd!r}"
        elif utc_key(entry.entry_timestamp) >= utc_key(entry.kickoff):
            state = "INVALID"
            detail = "entry_timestamp >= kickoff (timestamps fora de ordem)"
        elif (utc_key(entry.entry_timestamp) > utc_key(entry.prediction_timestamp)
              or utc_key(entry.prediction_timestamp) >= utc_key(entry.kickoff)):
            state = "INVALID"
            detail = "timestamps da decisao fora de ordem"
        else:
            has_obs = any(
                o.market == entry.market and o.outcome == entry.outcome
                for o in self.all_observations(entry.match_key)
            )
            if not has_obs:
                state = "MISMATCH"
                detail = (
                    "entrada sem nenhuma observacao correspondente no "
                    "store: identidade canonica divergente entre o "
                    "registro e as observacoes da linha"
                )
            else:
                if entry.entry_bookmaker and not any(
                    o.bookmaker == entry.entry_bookmaker
                    and o.timestamp <= utc_key(entry.prediction_timestamp)
                    for o in self.all_observations(entry.match_key)
                    if o.market == entry.market and o.outcome == entry.outcome
                ):
                    return ClvLifecycle(entry, "MISMATCH", detail="bookmaker original nao observado")
                if any(utc_key(o.kickoff) != utc_key(entry.kickoff)
                       for o in self.all_observations(entry.match_key)
                       if o.market == entry.market and o.outcome == entry.outcome):
                    return ClvLifecycle(entry, "MISMATCH", detail="kickoff divergente")
                if utc_key(now) < utc_key(entry.kickoff):
                    return ClvLifecycle(entry, "PENDING", detail="kickoff no futuro")
                result = self.clv_prospective(
                    entry.match_key, entry.market, entry.outcome,
                    entry_odd=entry.entry_odd,
                    entry_timestamp=entry.prediction_timestamp,
                    window_minutes=window_minutes,
                    strict_components=True,
                )
                if result.status == "OK":
                    state = "CLOSED"
                    detail = (
                        "fechamento valido apos a entrada: CLV calculado "
                        "(entry < closing < kickoff)"
                    )
                elif result.status == "CLOSING_BEFORE_ENTRY":
                    state = "INVALID"
                    detail = (
                        "fechamento ANTERIOR a entrada: aposta pos-"
                        "fechamento, CLV nao e evidencia prospectiva"
                    )
                else:
                    if utc_key(now) < utc_key(entry.kickoff):
                        state = "PENDING"
                        detail = (
                            "kickoff no futuro: fechamento ainda pode "
                            "chegar (ausencia nao e CLV=0)"
                        )
                    else:
                        state = "NO_CLOSE"
                        detail = (
                            "kickoff passou sem observacao valida na "
                            "janela de fechamento"
                        )

        return ClvLifecycle(entry=entry, state=state, result=result,
                            detail=detail)

    def clv_lifecycle_sweep(
        self,
        *,
        now: Optional[str] = None,
        window_minutes: float = CLOSING_WINDOW_MINUTES,
    ) -> "ClvLifecycleSummary":
        """Varre TODAS as entradas e classifica o ciclo de vida de cada uma.

        Operacao de leitura, idempotente: rodar duas vezes produz o
        mesmo resultado (nada e gravado, nenhuma duplicata nasce). Este
        e o passo operacional do CLV — depois de novas capturas de odds,
        o sweep re-classifica PENDING -> NO_CLOSE/CLOSED conforme o que
        o store REALMENTE observou.
        """
        now = now or now_utc()
        by_state: dict[str, int] = {s: 0 for s in CLV_LIFECYCLE_STATES}
        lifecycles: list[ClvLifecycle] = []
        for rec in self.clv_entries():
            lc = self.clv_lifecycle(rec, now=now, window_minutes=window_minutes)
            by_state[lc.state] += 1
            lifecycles.append(lc)
        return ClvLifecycleSummary(
            evaluated_at=utc_key(now),
            by_state=by_state,
            n_entries=len(lifecycles),
            lifecycles=lifecycles,
        )

    def clv(
        self,
        match_key: str,
        market: str,
        outcome: str,
        entry_odd: float,
        window_minutes: float = CLOSING_WINDOW_MINUTES,
    ) -> CLVResult:
        """CLV de uma aposta. Sem fechamento valido, campos ficam None."""
        if entry_odd <= 1.0:
            raise ValueError("entry_odd precisa ser > 1.0")
        entry_implied = 1.0 / entry_odd
        closing = self.closing_line(match_key, market, outcome, window_minutes)
        if closing is None:
            return CLVResult(
                match_key=match_key, market=market, outcome=outcome,
                entry_odd=entry_odd, entry_implied=entry_implied,
                status="NO_CLOSING_ODDS",
            )
        closing_odd, book, ts, minutes, n_books = closing
        closing_implied = 1.0 / closing_odd
        return CLVResult(
            match_key=match_key, market=market, outcome=outcome,
            entry_odd=entry_odd, entry_implied=entry_implied,
            closing_odd=closing_odd, closing_implied=closing_implied,
            clv_price=round(entry_odd - closing_odd, 4),
            clv_probability=round(closing_implied - entry_implied, 6),
            clv_percentage=round(entry_odd / closing_odd - 1.0, 6),
            closing_bookmaker=book, closing_timestamp=ts,
            closing_minutes_before=minutes, n_books_closing=n_books,
            status="OK",
        )

    def clv_prospective(
        self,
        match_key: str,
        market: str,
        outcome: str,
        entry_odd: float,
        entry_timestamp: str,
        window_minutes: float = CLOSING_WINDOW_MINUTES,
        *, strict_components: bool = False,
    ) -> CLVResult:
        """CLV prospectivo: a entrada TEM de ser anterior ao fechamento.

        Diferente de `clv`, aqui o instante da aposta e explicito. Se o
        "fechamento" encontrado for anterior a entrada, a aposta ja era
        pos-fechamento: o CLV seria calculado com informacao que nao
        existia na hora da decisao. Nesse caso o status e
        CLOSING_BEFORE_ENTRY e nenhum valor e reportado.
        """
        if entry_odd <= 1.0:
            raise ValueError("entry_odd precisa ser > 1.0")
        entry_implied = 1.0 / entry_odd
        closing = self.closing_line(
            match_key, market, outcome, window_minutes,
            after=entry_timestamp if strict_components else "")
        # Preserve INVALID diagnostic when observations exist only before
        # the decision. Such quotes never contribute to a valid aggregate.
        if closing is None and strict_components:
            closing = self.closing_line(match_key, market, outcome, window_minutes)
        if closing is None:
            return CLVResult(
                match_key=match_key, market=market, outcome=outcome,
                entry_odd=entry_odd, entry_implied=entry_implied,
                entry_timestamp=utc_key(entry_timestamp),
                status="NO_CLOSING_ODDS",
            )

        closing_odd, book, ts, minutes, n_books = closing
        entry_key = utc_key(entry_timestamp)
        if utc_key(ts) <= entry_key:
            return CLVResult(
                match_key=match_key, market=market, outcome=outcome,
                entry_odd=entry_odd, entry_implied=entry_implied,
                closing_odd=closing_odd,
                closing_implied=1.0 / closing_odd,
                closing_bookmaker=book, closing_timestamp=ts,
                closing_minutes_before=minutes, n_books_closing=n_books,
                entry_timestamp=entry_key,
                status="CLOSING_BEFORE_ENTRY",
            )

        closing_implied = 1.0 / closing_odd
        return CLVResult(
            match_key=match_key, market=market, outcome=outcome,
            entry_odd=entry_odd, entry_implied=entry_implied,
            closing_odd=closing_odd, closing_implied=closing_implied,
            clv_price=round(entry_odd - closing_odd, 4),
            clv_probability=round(closing_implied - entry_implied, 6),
            clv_percentage=round(entry_odd / closing_odd - 1.0, 6),
            closing_bookmaker=book, closing_timestamp=ts,
            closing_minutes_before=minutes, n_books_closing=n_books,
            entry_timestamp=entry_key, closing_after_entry=True,
            status="OK",
        )

    def coverage(self, bets: Sequence[dict],
                 window_minutes: float = CLOSING_WINDOW_MINUTES) -> CLVCoverage:
        """Cobertura agregada. bets: [{match_key, market, outcome, odd}].

        ATENCAO (Fase D): esta agregacao usa CLV RETROSPECTIVO — a odd
        de entrada vem do chamador (tipicamente a melhor odd atual), nao
        de uma entrada congelada no instante da decisao. E um indicador
        de DIAGNOSTICO de cobertura de fechamento; NAO constitui
        evidencia de promocao. A unica CLV que sustenta promocao e a
        prospectiva (`clv_prospective`, com entrada FIRST-WINS via
        `register_entry` e ordem entry < closing verificada).
        """
        report = CLVCoverage(total_bets=len(bets))
        pcts: list[float] = []
        probs: list[float] = []
        per_market: dict[str, list[float]] = {}
        for bet in bets:
            result = self.clv(
                bet["match_key"], bet["market"], bet["outcome"],
                bet["odd"], window_minutes,
            )
            if not result.valid:
                continue
            report.bets_with_clv += 1
            pcts.append(result.clv_percentage)
            probs.append(result.clv_probability)
            per_market.setdefault(bet["market"], []).append(result.clv_percentage)
        if pcts:
            report.avg_clv_percentage = round(statistics.fmean(pcts), 6)
            report.median_clv_percentage = round(statistics.median(pcts), 6)
            report.avg_clv_probability = round(statistics.fmean(probs), 6)
            report.positive_clv_rate = round(
                sum(1 for p in pcts if p > 0) / len(pcts), 4)
        report.by_market = {
            market: {
                "n": len(values),
                "avg_clv_percentage": round(statistics.fmean(values), 6),
            }
            for market, values in sorted(per_market.items())
        }
        return report

    # ------------------------------------------------------------- resumo

    def stats(self) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    """SELECT COUNT(*) AS n,
                              COUNT(DISTINCT match_key) AS matches,
                              COUNT(DISTINCT market) AS markets,
                              COUNT(DISTINCT bookmaker) AS books,
                              MIN(timestamp) AS first_ts,
                              MAX(timestamp) AS last_ts
                       FROM odds_observations"""
                ).fetchone()
                provider_rows = conn.execute(
                    """SELECT provider, COUNT(*) AS n
                       FROM odds_observations GROUP BY provider"""
                ).fetchall()
            finally:
                conn.close()
        return {
            "observations": row["n"] or 0,
            "matches": row["matches"] or 0,
            "markets": row["markets"] or 0,
            "bookmakers": row["books"] or 0,
            "first_timestamp": row["first_ts"] or "",
            "last_timestamp": row["last_ts"] or "",
            "providers": {r["provider"] or "": r["n"] for r in provider_rows},
        }

    @staticmethod
    def _to_obs(row: sqlite3.Row) -> OddsObservation:
        keys = row.keys()
        return OddsObservation(
            match_key=row["match_key"], market=row["market"],
            outcome=row["outcome"], bookmaker=row["bookmaker"],
            odd=row["odd"], timestamp=row["timestamp"],
            kickoff=row["kickoff"], provider=row["provider"] or "",
            is_opening=bool(row["is_opening"]),
            is_closing=bool(row["is_closing"]),
            timestamp_source=(
                (row["timestamp_source"] if "timestamp_source" in keys else None)
                or "QUOTE_TIMESTAMP"
            ),
        )

    # ------------------------------------------------------ execucao medida

    def save_execution(
        self,
        *,
        match_key: str,
        market: str,
        outcome: str,
        executed_price: float,
        executed_at: str,
        observed_price: float | None = None,
        decision_timestamp: str | None = None,
    ) -> None:
        """Persiste uma EXECUCAO medida (upsert por linha).

        Diferente de `clv_entries` (FIRST-WINS da entrada), a execucao pode
        ser corrigida/limpa: o operador registra a fill real e a API deixa
        de mostrar UNKNOWN. Sem chamada, nada e gravado — execucao nunca e
        presumida.
        """
        if not math.isfinite(executed_price) or executed_price <= 1.0:
            raise ValueError("executed_price precisa ser finito e > 1.0")
        if not executed_at:
            raise ValueError("execucao exige executed_at")
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO execution_records
                       (match_key, market, outcome, executed_price, executed_at,
                        observed_price, decision_timestamp, recorded_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        match_key, market, outcome, float(executed_price),
                        utc_key(executed_at), observed_price,
                        decision_timestamp, utc_key(executed_at),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_execution(self, *, match_key: str, market: str, outcome: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """DELETE FROM execution_records
                       WHERE match_key=? AND market=? AND outcome=?""",
                    (match_key, market, outcome),
                )
                conn.commit()
            finally:
                conn.close()

    def load_executions(self) -> dict[tuple[str, str, str], dict]:
        """Execucoes persistidas por (match_key, market, outcome)."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """SELECT match_key, market, outcome, executed_price,
                              executed_at, observed_price, decision_timestamp,
                              recorded_at
                       FROM execution_records"""
                ).fetchall()
            finally:
                conn.close()
        return {
            (r["match_key"], r["market"], r["outcome"]): {
                "executed_price": r["executed_price"],
                "executed_at": r["executed_at"],
                "observed_price": r["observed_price"],
                "decision_timestamp": r["decision_timestamp"],
                "recorded_at": r["recorded_at"],
            }
            for r in rows
        }
