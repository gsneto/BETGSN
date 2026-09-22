"""BETGSN :: backtest_store — persistencia das execucoes de backtest.

Por que SQLite (stdlib) e nao um ORM
------------------------------------
O BETGSN nao possuia banco algum: o nucleo e deliberadamente livre de
dependencias externas e roda com a biblioteca padrao. `sqlite3` ja vem
com o Python, entao da para ter tabelas de verdade, consulta por
`run_id`, paginacao e comparacao entre execucoes SEM adicionar
SQLAlchemy/Alembic e sem migrar a arquitetura.

O arquivo fica em `output/backtests/betgsn_backtest.db` — dentro da
convencao ja existente de `output/` para artefatos.

Tabelas
-------
backtest_runs     metadados + configuracao + hash (auditoria)
backtest_signals  cada sinal congelado com o resultado (auditoria fina)
backtest_metrics  metricas agregadas por grupo (comparacao entre runs)

O schema e criado com `CREATE TABLE IF NOT EXISTS` e versionado em
`SCHEMA_VERSION`. Sem framework de migracao: se o schema mudar, a versao
sobe e o store reconstroi o arquivo (dados de backtest sao reproduziveis
a partir da config + corpus, entao nao ha risco de perda irreversivel).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from .backtest_engine import BacktestRun, SettledSignal
from .backtest_metrics import BacktestMetrics, compute_metrics

SCHEMA_VERSION = 1


def _default_db_path() -> Path:
    """Caminho default do banco de backtests (respeita BETGSN_OUTPUT_DIR)."""
    from .config import output_root

    return output_root() / "backtests" / "betgsn_backtest.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    model_version TEXT NOT NULL,
    point_in_time_schema TEXT NOT NULL,
    n_signals INTEGER NOT NULL,
    n_matches_evaluated INTEGER NOT NULL,
    n_matches_skipped INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    config_json TEXT NOT NULL,
    run_meta_json TEXT NOT NULL,
    simulation_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON backtest_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS backtest_signals (
    run_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    competition TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence TEXT NOT NULL,
    best_odd REAL NOT NULL,
    model_prob REAL NOT NULL,
    ev REAL NOT NULL,
    outcome_result TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, signal_id),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_signals_run ON backtest_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_signals_kickoff ON backtest_signals(run_id, kickoff);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    run_id TEXT NOT NULL,
    metric_group TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_group, metric_key),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
);
"""


class BacktestStore:
    """Armazena e recupera execucoes de backtest. Thread-safe."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else _default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    # ------------------------------------------------------------- infra

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            current = row["value"] if row else None
            if current != str(SCHEMA_VERSION):
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(SCHEMA_VERSION),),
                )

    # ------------------------------------------------------------ escrita

    def save_run(
        self,
        run: BacktestRun,
        metrics: BacktestMetrics,
        simulation_json: dict[str, Any],
    ) -> str:
        """Persiste uma execucao completa. Devolve o run_id."""
        run_meta = {
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "n_matches_in_period": run.n_matches_in_period,
            "skipped_reasons": run.skipped_reasons,
            "missing_data_notes": list(run.missing_data_notes),
            "duration_ms": run.duration_ms,
            "prediction_metrics": metrics.prediction_metrics,
            "predictions": run.predictions,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO backtest_runs("
                " run_id, created_at, config_hash, model_version, point_in_time_schema,"
                " n_signals, n_matches_evaluated, n_matches_skipped, duration_ms,"
                " config_json, run_meta_json, simulation_json"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.finished_at,
                    run.config_hash,
                    run.model_version,
                    run.point_in_time_schema,
                    len(run.signals),
                    run.n_matches_evaluated,
                    run.n_matches_skipped,
                    run.duration_ms,
                    json.dumps(asdict(run.config), sort_keys=True, default=str),
                    json.dumps(run_meta, sort_keys=True, default=str),
                    json.dumps(simulation_json, sort_keys=True, default=str),
                ),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO backtest_signals("
                " run_id, signal_id, kickoff, competition, market, outcome,"
                " confidence, best_odd, model_prob, ev, outcome_result, payload_json"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run.run_id,
                        s.signal.signal_id,
                        s.signal.kickoff,
                        s.signal.competition,
                        s.signal.market,
                        s.signal.outcome,
                        s.signal.confidence,
                        s.signal.best_odd,
                        s.signal.model_prob,
                        s.signal.ev,
                        s.outcome_result or "",
                        json.dumps(_signal_payload(s), sort_keys=True, default=str),
                    )
                    for s in run.signals
                ],
            )
            metric_rows: list[tuple[str, str, str, str]] = []
            agg = metrics.aggregate
            for key, value in asdict(agg).items():
                metric_rows.append((
                    run.run_id, "aggregate", key,
                    json.dumps(_jsonable(value), default=str),
                ))
            for b in metrics.calibration:
                metric_rows.append((
                    run.run_id, "calibration", b.label,
                    json.dumps(asdict(b), default=str),
                ))
            for b in metrics.ev_buckets:
                metric_rows.append((
                    run.run_id, "ev_bucket", b.label,
                    json.dumps(asdict(b), default=str),
                ))
            for gran, series in metrics.temporal.items():
                for point in series:
                    metric_rows.append((
                        run.run_id, f"temporal_{gran}", point.label,
                        json.dumps(asdict(point), default=str),
                    ))
            for seg in metrics.segments:
                metric_rows.append((
                    run.run_id, f"segment_{seg.dimension}", seg.segment,
                    json.dumps(asdict(seg), default=str),
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO backtest_metrics("
                " run_id, metric_group, metric_key, payload_json) VALUES (?,?,?,?)",
                metric_rows,
            )
        return run.run_id

    # ------------------------------------------------------------ leitura

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, created_at, config_hash, model_version,"
                " n_signals, n_matches_evaluated, n_matches_skipped, duration_ms,"
                " config_json, run_meta_json, simulation_json"
                " FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_run_summary(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        summary = _run_summary(row)
        summary["config"] = json.loads(row["config_json"])
        # a simulacao completa (com a curva de capital) so e necessaria no
        # detalhe; a listagem usa o resumo enxuto
        summary["simulation_full"] = json.loads(row["simulation_json"])
        return summary

    def get_metrics(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Metricas agrupadas: {"aggregate": {...}, "calibration": {...}}."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT metric_group, metric_key, payload_json FROM backtest_metrics"
                " WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out.setdefault(r["metric_group"], {})[r["metric_key"]] = json.loads(
                r["payload_json"]
            )
        return out

    def get_signals(
        self,
        run_id: str,
        *,
        search: str = "",
        market: str = "",
        confidence: str = "",
        outcome_result: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Sinais paginados e pesquisaveis, direto do banco."""
        where = ["run_id = ?"]
        params: list[Any] = [run_id]
        if search:
            where.append(
                "(LOWER(json_extract(payload_json,'$.home')) LIKE ?"
                " OR LOWER(json_extract(payload_json,'$.away')) LIKE ?"
                " OR LOWER(market) LIKE ? OR LOWER(outcome) LIKE ?)"
            )
            needle = f"%{search.lower()}%"
            params += [needle, needle, needle, needle]
        if market:
            where.append("market = ?")
            params.append(market)
        if confidence:
            where.append("confidence = ?")
            params.append(confidence)
        if outcome_result:
            where.append("outcome_result = ?")
            params.append(outcome_result)
        clause = " AND ".join(where)

        with self._lock, self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM backtest_signals WHERE {clause}", params
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT payload_json FROM backtest_signals WHERE {clause}"
                " ORDER BY kickoff ASC, ev DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [json.loads(r["payload_json"]) for r in rows],
        }

    def delete_run(self, run_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM backtest_signals WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM backtest_metrics WHERE run_id = ?", (run_id,))
            return cur.rowcount > 0

    def count_runs(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM backtest_runs").fetchone()["c"]


# --------------------------------------------------------------------------
# Comparacao entre execucoes
# --------------------------------------------------------------------------


def compare_runs(
    store: BacktestStore,
    run_id_a: str,
    run_id_b: str,
) -> dict[str, Any]:
    """Compara duas execucoes lado a lado.

    Preparado para 'modelo antigo vs modelo novo': mostra diferencas em
    calibracao, Brier, volume de sinais, taxa observada e estabilidade
    temporal. Nao faz juizo automatico de qual e melhor — apenas expoe.
    """
    run_a = store.get_run(run_id_a)
    run_b = store.get_run(run_id_b)
    if run_a is None or run_b is None:
        raise KeyError("run_id inexistente")

    metrics_a = store.get_metrics(run_id_a)
    metrics_b = store.get_metrics(run_id_b)
    agg_a = metrics_a.get("aggregate", {})
    agg_b = metrics_b.get("aggregate", {})

    def delta(key: str) -> dict[str, float]:
        va = _num(agg_a.get(key))
        vb = _num(agg_b.get(key))
        return {"a": va, "b": vb, "delta": vb - va}

    temporal_keys = sorted(
        set(metrics_a.get("temporal_month", {})) | set(metrics_b.get("temporal_month", {}))
    )
    temporal = []
    for key in temporal_keys:
        ta = metrics_a.get("temporal_month", {}).get(key, {})
        tb = metrics_b.get("temporal_month", {}).get(key, {})
        temporal.append({
            "label": key,
            "hit_rate_a": _num(ta.get("hit_rate")),
            "hit_rate_b": _num(tb.get("hit_rate")),
            "n_a": _num(ta.get("n")),
            "n_b": _num(tb.get("n")),
        })

    return {
        "run_a": {"run_id": run_id_a, "created_at": run_a["created_at"],
                  "config_hash": run_a["config_hash"]},
        "run_b": {"run_id": run_id_b, "created_at": run_b["created_at"],
                  "config_hash": run_b["config_hash"]},
        "same_config": run_a["config_hash"] == run_b["config_hash"],
        "metrics": {
            "n_signals": delta("n_signals"),
            "hit_rate": delta("hit_rate"),
            "brier": delta("brier"),
            "logloss": delta("logloss"),
            "avg_ev": delta("avg_ev"),
            "avg_realized_return": delta("avg_realized_return"),
            "ev_gap": delta("ev_gap"),
        },
        "temporal_month": temporal,
    }


def _num(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def _signal_payload(s: SettledSignal) -> dict[str, Any]:
    """Payload completo do sinal, incluindo o contexto point-in-time."""
    f = s.signal
    return {
        "signal_id": f.signal_id,
        "match_id": f.match_id,
        "competition": f.competition,
        "season": f.season,
        "kickoff": f.kickoff,
        "kickoff_utc": f.kickoff_utc,
        "home": f.home,
        "away": f.away,
        "market": f.market,
        "outcome": f.outcome,
        "best_odd": f.best_odd,
        "best_book": f.best_book,
        "median_odd": f.median_odd,
        "fair_odd": f.fair_odd,
        "n_books": f.n_books,
        "model_prob": f.model_prob,
        "market_prob": f.market_prob,
        "edge": f.edge,
        "ev": f.ev,
        "kelly": f.kelly,
        "stake": f.stake,
        "stake_pct": f.stake_pct,
        "expected_profit": f.expected_profit,
        "confidence": f.confidence,
        "rationale": f.rationale,
        "lambda_home": f.lambda_home,
        "lambda_away": f.lambda_away,
        "home_attack": f.home_attack,
        "home_defense": f.home_defense,
        "away_attack": f.away_attack,
        "away_defense": f.away_defense,
        "home_xg_for": f.home_xg_for,
        "away_xg_for": f.away_xg_for,
        "n_prior_matches": f.n_prior_matches,
        "league_goals": f.league_goals,
        "attack_blend": f.attack_blend,
        "odds_source": f.odds_source,
        "odds_as_of": f.odds_as_of,
        "model_version": f.model_version,
        "config_hash": f.config_hash,
        "result_home_goals": s.result_home_goals,
        "result_away_goals": s.result_away_goals,
        "outcome_result": s.outcome_result,
        "settled": s.settled,
        "realized_return": s.realized_return,
        "profit": s.profit,
    }


def _run_summary(row: sqlite3.Row) -> dict[str, Any]:
    meta = json.loads(row["run_meta_json"])
    sim = json.loads(row["simulation_json"]) if "simulation_json" in row.keys() else {}
    return {
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "config_hash": row["config_hash"],
        "model_version": row["model_version"],
        "n_signals": row["n_signals"],
        "n_matches_evaluated": row["n_matches_evaluated"],
        "n_matches_skipped": row["n_matches_skipped"],
        "duration_ms": row["duration_ms"],
        "meta": meta,
        "simulation": {
            "initial_bankroll": sim.get("initial_bankroll"),
            "final_bankroll": sim.get("final_bankroll"),
            "return_pct": sim.get("return_pct"),
            "max_drawdown": sim.get("max_drawdown"),
            "roi": sim.get("roi"),
        },
    }


def simulation_to_json(sim) -> dict[str, Any]:
    """Serializa a simulacao para persistencia."""
    data = asdict(sim)
    data.pop("equity", None)
    data["equity"] = [asdict(p) for p in sim.equity]
    return data


def metrics_for_run(run: BacktestRun, simulation, signal_limit: int | None = None):
    """Atalho: calcula metricas a partir de uma execucao + simulacao."""
    return compute_metrics(run, simulation, signal_limit=signal_limit)


def summarize_signals(signals: Sequence[SettledSignal]) -> dict[str, int]:
    return {
        "total": len(signals),
        "wins": sum(1 for s in signals if s.won),
        "losses": sum(1 for s in signals if s.settled and not s.won and not s.pushed),
        "pushes": sum(1 for s in signals if s.pushed),
        "unsettled": sum(1 for s in signals if not s.settled),
    }
