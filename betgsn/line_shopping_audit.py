"""Auditoria do efeito do line-shopping (+6,4pp) — decomposição honesta.

O efeito observado entre strategy_model COM line-shopping (ROI -4,89%)
e SEM line-shopping (ROI -11,31%) NÃO é assumido como edge real. Este
módulo o decomponde em:

  1. POPULAÇÃO CONSTANTE: mesmas apostas (eligibilidade fixa pela regra
     de produção: n_books >= 3 e melhor odd < 1.30), preço variando
     (melhor / segunda melhor / mediana / média / pior). Isole o efeito
     PREÇO do efeito SELEÇÃO.
  2. POPULAÇÃO VARIÁVEL: eligibilidade E preço mudando (semântica da
     ablação `use_median` do harness) — reproduz o número reportado e
     mostra quanto dele é população diferente.
  3. UPLIFT de preço: best-vs-median, best-vs-second, dispersão entre
     casas, por número de casas.
  4. SEGMENTAÇÃO do efeito: odd band, liga, janela walk-forward,
     quantidade de bookmakers.

LIMITAÇÕES TEMPORAIS DECLARADAS (corpus football-data.co.uk):
  - as odds do corpus são de ABERTURA, SEM timestamp de publicação;
  - portanto "quote age" e "time-to-kickoff" NÃO são mensuráveis no
    histórico — a auditoria temporal desses campos vive no store
    operacional (`OddsSnapshotStore.line_at`, PIT estrito, testado em
    test_clv_prospective_real.py);
  - o "melhor preço" do corpus é uma seleção cross-sectional entre
    casas (todas anteriores ao kickoff por construção do CSV), não uma
    seleção temporal. Look-ahead de resultado é impossível (as odds
    existem antes do jogo); look-ahead intra-mercado (quote posterior
    ao instante da decisão) é indemonstrável sem timestamps — declarado,
    nunca escondido.

Nada aqui muda a regra, o harness de produção ou o promotion gate.
Auditoria não é tuning: nenhum parâmetro é otimizado.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .value_strategy import MAX_ODD, MIN_BOOKS, ODD_BANDS
from .value_walkforward import (
    WalkForwardConfig,
    _bootstrap_ci,
    _ret,
    _roi_stats,
    audit_windows,
    build_windows,
    wilson_ci,
)

#: Chaves de preço auditáveis. "best" é a odd do dicionário canônico.
PRICE_KEYS: tuple[str, ...] = ("best", "second", "median", "mean", "worst")

#: Buckets de bookmakers para a pergunta "depende de poucos jogos com
#: MUITAS casas?" (pergunta I da auditoria).
N_BOOKS_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("3", 3, 3), ("4", 4, 4), ("5-6", 5, 6), ("7-9", 7, 9), ("10+", 10, 99),
)

#: Amostra mínima de um segmento (liga/banda/bucket) para ser reportado.
MIN_SEGMENT_LINES = 200


def price_of(bet: dict, key: str) -> float | None:
    """Preço de uma linha canônica sob a ótica `key`. None = indisponível."""
    if key == "best":
        return float(bet["odd"])
    if key == "second":
        value = bet.get("second")
        return float(value) if value else None
    if key == "median":
        return float(bet.get("median") or bet["odd"])
    if key == "mean":
        value = bet.get("mean")
        return float(value) if value else None
    if key == "worst":
        value = bet.get("worst")
        return float(value) if value else None
    raise ValueError(f"chave de preço desconhecida: {key!r}")


def _eligible(bet: dict, *, max_odd: float, min_books: int, on: str) -> bool:
    """Eligibilidade da linha sob o preço `on` (best ou median)."""
    if int(bet.get("n_books", 0)) < min_books:
        return False
    price = price_of(bet, on)
    return price is not None and price < max_odd


def _roi_at(
    bets: Sequence[dict],
    price: str,
    config: WalkForwardConfig,
) -> dict[str, Any]:
    """ROI/n/SE/t/Wilson/bootstrap de `bets` liquidadas ao preço `price`."""
    pairs: list[tuple[float, str]] = []
    for b in bets:
        p = price_of(b, price)
        if p is None:
            continue
        pairs.append((p, b.get("res", "loss")))
    if not pairs:
        return {"n": 0, "roi": None, "roi_se": None, "roi_t": None,
                "wilson_low": None, "wilson_high": None,
                "bootstrap_low": None, "bootstrap_high": None}
    rets = [_ret(p, res) for p, res in pairs]
    roi, se, t = _roi_stats(rets)
    wins = sum(1 for _p, res in pairs if res == "win")
    losses = sum(1 for _p, res in pairs if res == "loss")
    lo, hi = wilson_ci(wins, losses)
    blo, bhi = _bootstrap_ci(
        rets, config.bootstrap_resamples, config.bootstrap_seed)
    return {
        "n": len(rets),
        "roi": round(roi, 6),
        "roi_se": round(se, 6),
        "roi_t": round(t, 4),
        "wilson_low": round(lo, 6),
        "wilson_high": round(hi, 6),
        "bootstrap_low": round(blo, 6),
        "bootstrap_high": round(bhi, 6),
    }


def _band_of(bet: dict) -> str:
    best = float(bet["odd"])
    for lo, hi in ODD_BANDS:
        if lo <= best < hi:
            return f"{lo:.2f}-{hi:.2f}" if hi < 90 else f">= {lo:.2f}"
    return "n/d"


def _bucket_of(bet: dict) -> str:
    n = int(bet.get("n_books", 0))
    for label, lo, hi in N_BOOKS_BUCKETS:
        if lo <= n <= hi:
            return label
    return "n/d"


def _uplift_stats(bets: Sequence[dict]) -> dict[str, Any]:
    """Uplift de preço na população dada: best vs median/second/worst.

    Convenção de percentis: nearest-rank (o elemento de índice
    int(q*n), truncado aos limites) — declarada aqui porque quantil
    "exato" não existe para amostras discretas.
    """
    def _pct(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"mean": None, "median": None, "p10": None, "p90": None}
        ordered = sorted(values)
        return {
            "mean": round(statistics.fmean(values), 6),
            "median": round(statistics.median(values), 6),
            "p10": round(ordered[max(0, int(0.10 * len(ordered)) - 1)], 6),
            "p90": round(ordered[min(len(ordered) - 1, int(0.90 * len(ordered)))], 6),
        }

    best_median: list[float] = []
    best_second: list[float] = []
    dispersion: list[float] = []
    for b in bets:
        best = price_of(b, "best")
        median = price_of(b, "median")
        second = price_of(b, "second")
        if best and median and median > 1.0:
            best_median.append(best / median - 1.0)
        if best and second:
            best_second.append(best / second - 1.0)
        disp = b.get("disp")
        if disp is not None and median and median > 1.0:
            dispersion.append(float(disp) / median)
    return {
        "best_vs_median": _pct(best_median),
        "best_vs_second": _pct(best_second),
        "dispersion_over_median": _pct(dispersion),
        "n_books_distribution": _n_books_distribution(bets),
    }


def _n_books_distribution(bets: Sequence[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for b in bets:
        out[_bucket_of(b)] = out.get(_bucket_of(b), 0) + 1
    return dict(sorted(out.items()))


def _segment(
    bets: Sequence[dict],
    key_fn: Callable[[dict], str],
    config: WalkForwardConfig,
    min_lines: int = MIN_SEGMENT_LINES,
) -> list[dict[str, Any]]:
    """Delta ROI(best) - ROI(median) por segmento, população constante."""
    groups: dict[str, list[dict]] = {}
    for b in bets:
        groups.setdefault(key_fn(b), []).append(b)
    out: list[dict[str, Any]] = []
    for name, rows in sorted(groups.items()):
        if len(rows) < min_lines:
            continue
        at_best = _roi_at(rows, "best", config)
        at_median = _roi_at(rows, "median", config)
        delta = (
            None if (at_best["roi"] is None or at_median["roi"] is None)
            else round(at_best["roi"] - at_median["roi"], 6)
        )
        out.append({
            "segment": name,
            "n": len(rows),
            "roi_best": at_best["roi"],
            "roi_median": at_median["roi"],
            "delta_best_minus_median": delta,
        })
    return out


@dataclass
class LineShoppingAudit:
    """Resultado completo da auditoria (serializável)."""

    config: WalkForwardConfig
    n_lines_total: int = 0
    n_lines_rule: int = 0
    #: população CONSTANTE (regra de produção), preço variando
    rule_population_by_price: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: população VARIÁVEL (semântica da ablação use_median)
    ablation_semantics: dict[str, Any] = field(default_factory=dict)
    #: efeito por janela (população constante)
    by_window: list[dict[str, Any]] = field(default_factory=list)
    #: efeito por odd band / liga / bucket de casas
    by_band: list[dict[str, Any]] = field(default_factory=list)
    by_league: list[dict[str, Any]] = field(default_factory=list)
    by_n_books: list[dict[str, Any]] = field(default_factory=list)
    #: uplift de preço e dispersão
    price_uplift: dict[str, Any] = field(default_factory=dict)
    #: respostas A-I, com limitações declaradas
    answers: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        import dataclasses

        out = dataclasses.asdict(self)
        out["config"] = {
            "train_days": self.config.train_days,
            "test_days": self.config.test_days,
            "gap_days": self.config.gap_days,
            "bootstrap_resamples": self.config.bootstrap_resamples,
            "bootstrap_seed": self.config.bootstrap_seed,
            "max_odd": MAX_ODD,
            "min_books": MIN_BOOKS,
        }
        return out


def run_line_shopping_audit(
    bets: Sequence[dict],
    config: WalkForwardConfig | None = None,
    *,
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
    min_segment_lines: int = MIN_SEGMENT_LINES,
    progress: Callable[[int, int, str], None] | None = None,
) -> LineShoppingAudit:
    """Executa a auditoria completa sobre as linhas canônicas.

    `bets` = saída de `collect_bets(99.0, 1)` (TODAS as linhas, com os
    campos enriquecidos odd/second/median/mean/worst/disp/n_books).
    Mesmas janelas e embargo do harness OOS (`build_windows` /
    `audit_windows`); violação temporal levanta — auditoria não mede
    sobre janelas inválidas.
    """
    config = config or WalkForwardConfig()
    config.validate()
    if bets:
        days = sorted({str(b["d"]) for b in bets})
        windows = build_windows(days[0], days[-1], config)
        violations = audit_windows(windows)
        if violations:
            raise ValueError(
                "janelas com violação temporal: " + "; ".join(violations))

    audit = LineShoppingAudit(config=config, n_lines_total=len(bets))

    # ---- população CONSTANTE: a regra de produção (eligibilidade no best)
    rule = [
        b for b in bets
        if _eligible(b, max_odd=max_odd, min_books=min_books, on="best")
    ]
    audit.n_lines_rule = len(rule)
    audit.rule_population_by_price = {
        price: _roi_at(rule, price, config) for price in PRICE_KEYS
    }

    # ---- população VARIÁVEL: semântica da ablação use_median
    median_pop = [
        b for b in bets
        if _eligible(b, max_odd=max_odd, min_books=min_books, on="median")
    ]
    audit.ablation_semantics = {
        "note": (
            "ablação use_median troca eligibilidade E preço: populações "
            "diferentes. O delta aqui reproduz a semântica do número "
            "reportado; o isolamento correto do efeito PREÇO está em "
            "rule_population_by_price."
        ),
        "population_best": {
            "n": len(rule),
            "roi_best": _roi_at(rule, "best", config)["roi"],
        },
        "population_median": {
            "n": len(median_pop),
            "roi_median": _roi_at(median_pop, "median", config)["roi"],
        },
        "delta_reported_semantics": _delta_or_none(
            _roi_at(median_pop, "median", config)["roi"],
            _roi_at(rule, "best", config)["roi"],
        ),
    }

    # ---- janelas: mesma regra, TEST de cada janela, efeito por janela
    if bets:
        for i, window in enumerate(windows):
            if progress is not None:
                progress(i + 1, len(windows), f"janela {window.index}")
            test = [
                b for b in rule
                if window.test_start <= str(b["d"]) < window.test_end
            ]
            if not test:
                continue
            at_best = _roi_at(test, "best", config)
            at_median = _roi_at(test, "median", config)
            audit.by_window.append({
                "window": window.index,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "n": at_best["n"],
                "roi_best": at_best["roi"],
                "roi_median": at_median["roi"],
                "delta_best_minus_median": _delta_or_none(
                    at_best["roi"], at_median["roi"]),
            })

    # ---- segmentações (população constante)
    audit.by_band = _segment(rule, _band_of, config, min_segment_lines)
    audit.by_league = _segment(
        rule, lambda b: str(b.get("lg", "")), config, min_segment_lines)
    audit.by_n_books = _segment(rule, _bucket_of, config, min_segment_lines)

    # ---- uplift de preço
    audit.price_uplift = _uplift_stats(rule)

    # ---- respostas A-I
    audit.answers = _build_answers(audit)
    audit.limitations = [
        "odds do corpus football-data.co.uk são de abertura, SEM timestamp "
        "de publicação: quote age e time-to-kickoff não são mensuráveis no "
        "histórico (perguntas B/E); no caminho operacional o PIT é garantido "
        "por OddsSnapshotStore.line_at e testado em test_clv_prospective_real.py",
        "melhor preço é seleção cross-sectional entre casas pré-kickoff; a "
        "cotação escolhida traça até as colunas de bookmaker do CSV (pergunta "
        "D), mas o instante exato de cada cotação não existe no corpus",
        "auditoria é diagnóstica: nenhum parâmetro foi ajustado para "
        "maximizar resultado",
    ]
    return audit


def _delta_or_none(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 6)


def strategy_model_decomposition(
    strategy_rows: Sequence[dict],
    config: WalkForwardConfig | None = None,
) -> dict[str, Any]:
    """Decomposição do efeito reportado da strategy_model (+6,4pp).

    `strategy_rows` = linhas EV>0 do harness de modelo
    (`ModelComparisonResult.strategy_rows`), cada uma com odd (best) e
    mediana DA MESMA LINHA. População CONSTANTE: liquidação ao melhor
    preço vs liquidação à mediana nas mesmas apostas — isola o efeito
    PREÇO. O número reportado (ablação `use_median`) também troca a
    eligibilidade (EV recalculado à mediana) — aqui fica explícito
    quanto do delta é população diferente.
    """
    config = config or WalkForwardConfig()
    at_best = _roi_at(strategy_rows, "best", config)
    at_median = _roi_at(strategy_rows, "median", config)

    # segmentação por janela (população constante)
    by_window: list[dict[str, Any]] = []
    windows = sorted({int(r["window"]) for r in strategy_rows})
    for w in windows:
        rows_w = [r for r in strategy_rows if int(r["window"]) == w]
        b = _roi_at(rows_w, "best", config)
        m = _roi_at(rows_w, "median", config)
        by_window.append({
            "window": w, "n": b["n"],
            "roi_best": b["roi"], "roi_median": m["roi"],
            "delta_best_minus_median": _delta_or_none(b["roi"], m["roi"]),
        })
    n_positive = sum(
        1 for w in by_window if (w["delta_best_minus_median"] or 0) > 0)

    return {
        "note": (
            "strategy_model (EV>0 com prob do modelo): MESMAS apostas "
            "liquidadas ao melhor preço e à mediana. O delta aqui é puro "
            "efeito PREÇO; o número reportado pela ablação use_median "
            "também troca a população (EV recalculado)."
        ),
        "population": "EV>0 ao melhor preço (strategy_model reportada)",
        "n_rows": len(strategy_rows),
        "roi_best": at_best["roi"],
        "roi_best_bootstrap": [at_best["bootstrap_low"], at_best["bootstrap_high"]],
        "roi_median": at_median["roi"],
        "roi_median_bootstrap": [at_median["bootstrap_low"], at_median["bootstrap_high"]],
        "delta_price_effect": _delta_or_none(at_best["roi"], at_median["roi"]),
        "by_window": by_window,
        "n_windows_positive": n_positive,
        "n_windows": len(by_window),
    }


def _build_answers(audit: LineShoppingAudit) -> dict[str, Any]:
    by_price = audit.rule_population_by_price
    best = by_price.get("best", {})
    median = by_price.get("median", {})

    def _effect_with_ci(delta: float) -> str:
        # SE da diferença (aproximação conservadora: soma das variâncias)
        se_best = best.get("roi_se")
        se_median = median.get("roi_se")
        if se_best is None or se_median is None:
            return "n/d"
        se = math.sqrt(se_best ** 2 + se_median ** 2)
        t = delta / se if se > 0 else 0.0
        return f"t≈{t:.2f}"

    price_delta = _delta_or_none(
        best.get("roi"), median.get("roi"))

    windows = [w for w in audit.by_window if w["delta_best_minus_median"] is not None]
    n_windows_positive = sum(
        1 for w in windows if w["delta_best_minus_median"] > 0)

    return {
        "A_ganho_e_do_melhor_preco": {
            "resposta": (
                "mesma população (regra de produção), ROI ao melhor preço "
                f"{best.get('roi')} vs mediana {median.get('roi')} "
                f"(delta {price_delta}, {_effect_with_ci(price_delta or 0.0)})"
            ),
            "roi_por_preco": {k: v.get("roi") for k, v in by_price.items()},
            "n": best.get("n"),
        },
        "B_melhor_preco_disponivel_na_decisao": (
            "INDEMONSTRÁVEL no corpus (odds sem timestamp de publicação); "
            "no caminho operacional, garantido por line_at (PIT) e verificado "
            "por teste de leakage"
        ),
        "C_existe_look_ahead": (
            "look-ahead de RESULTADO impossível (odds pré-kickoff); "
            "look-ahead de QUOTE indemonstrável sem timestamps — declarado"
        ),
        "D_cotacao_rastreavel_ate_snapshot": (
            "sim, no corpus: cada linha traça até as colunas de bookmaker do "
            "CSV; no caminho operacional, até odds_observations do store"
        ),
        "E_restricao_a_quotes_ate_o_instante": (
            "N/A no corpus (sem timestamps); no operacional, line_at só usa "
            "timestamp <= decisão (testado)"
        ),
        "F_depende_de_poucos_bookmakers": {
            "por_bucket_de_casas": {
                row["segment"]: {
                    "n": row["n"],
                    "delta": row["delta_best_minus_median"],
                }
                for row in audit.by_n_books
            },
        },
        "G_depende_de_poucas_janelas": {
            "n_janelas_com_efeito": len(windows),
            "n_janelas_positivas": n_windows_positive,
        },
        "H_permece_nas_odd_bands": {
            "por_banda": {
                row["segment"]: {
                    "n": row["n"],
                    "roi_best": row["roi_best"],
                    "roi_median": row["roi_median"],
                    "delta": row["delta_best_minus_median"],
                }
                for row in audit.by_band
            },
        },
        "I_depende_de_jogos_com_muitas_casas": (
            "ver F: distribuição por bucket de bookmakers e delta por bucket"
        ),
    }
