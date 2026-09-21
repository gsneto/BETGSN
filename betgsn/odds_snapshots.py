"""BETGSN :: odds_snapshots — Persistencia append-only de odds para CLV.

Cada observacao de preco e gravada com timestamp. Nada e sobrescrito.
"Closing odds" e uma DEFINICAO OPERACIONAL: a ultima observacao antes do
kickoff, exigindo que ela esteja dentro de uma janela maxima. Se nao houver
observacao valida, o CLV e None — nunca inventado.
"""
from __future__ import annotations

import sqlite3
import statistics
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from .timeutil import parse_kickoff, utc_key

SCHEMA_VERSION = 1
DEFAULT_DB = Path(__file__).resolve().parent.parent / "output" / "odds_snapshots.db"

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

    def __post_init__(self) -> None:
        if self.odd <= 1.0:
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
) -> list[OddsObservation]:
    """Converte cotacoes normalizadas em observacoes persistiveis.

    O `match_key` e `quote.event_id` — a MESMA chave canonica produzida por
    `odds_normalize.event_key` (mandante|visitante|kickoff UTC). A chave nao
    e derivada de novo aqui, apenas transportada: escrita e leitura precisam
    falar do mesmo jogo.

    Cotacoes pos-kickoff ou com preco invalido sao descartadas. A observacao
    nunca e "corrigida" para caber no contrato: sem dado utilizavel, nada e
    gravado.
    """
    out: list[OddsObservation] = []
    for quote in quotes:
        if not getattr(quote, "pre_kickoff", False):
            continue
        try:
            out.append(
                OddsObservation(
                    match_key=quote.event_id,
                    market=quote.market,
                    outcome=quote.selection,
                    bookmaker=quote.bookmaker,
                    odd=quote.price,
                    timestamp=quote.timestamp,
                    kickoff=quote.kickoff,
                    provider=getattr(quote, "provider", "") or default_provider,
                )
            )
        except ValueError:
            continue
    return out


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


class OddsSnapshotStore:
    """Store append-only de observacoes de odds."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_DB
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
                        UNIQUE(match_key, market, outcome, bookmaker, timestamp)
                    );
                    CREATE INDEX IF NOT EXISTS idx_obs_line
                        ON odds_observations(match_key, market, outcome);
                    CREATE INDEX IF NOT EXISTS idx_obs_ts
                        ON odds_observations(match_key, timestamp);
                    """
                )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_meta VALUES (?, ?)",
                    ("version", str(SCHEMA_VERSION)),
                )
                conn.commit()
            finally:
                conn.close()

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
                        minutes_before_kickoff)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    # ------------------------------------------------------------- leitura

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

    def closing_line(
        self,
        match_key: str,
        market: str,
        outcome: str,
        window_minutes: float = CLOSING_WINDOW_MINUTES,
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
                  and r["minutes_before_kickoff"] <= window_minutes]
        if not usable:
            return None

        odds = [r["odd"] for r in usable]
        median_odd = statistics.median(odds)
        best = min(usable, key=lambda r: abs(r["odd"] - median_odd))
        return (
            round(median_odd, 4),
            best["bookmaker"],
            best["timestamp"],
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
        """Cobertura agregada. bets: [{match_key, market, outcome, odd}]."""
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
        return OddsObservation(
            match_key=row["match_key"], market=row["market"],
            outcome=row["outcome"], bookmaker=row["bookmaker"],
            odd=row["odd"], timestamp=row["timestamp"],
            kickoff=row["kickoff"], provider=row["provider"] or "",
            is_opening=bool(row["is_opening"]),
            is_closing=bool(row["is_closing"]),
        )
