"""BETGSN :: value_walkforward — validação OOS walk-forward da regra de valor.

POR QUE ESTE MODULO EXISTE
--------------------------
A validacao historica da regra (`value_strategy.validate`) e FULL-SAMPLE:
a banda de odd (< 1.30) foi ESCOLHIDA olhando o corpus inteiro e depois
medida no MESMO corpus. Isso e evidencia in-sample para o processo de
selecao da regra — util como historia, insuficiente como validacao.

Aqui o processo de SELECAO e replicado por janela, honestamente:

    WINDOW 1: TRAIN -> GAP -> TEST
    WINDOW 2: TRAIN -> GAP -> TEST
    ...

Em cada janela, a banda candidata e escolhida APENAS com as apostas do
TRAIN (a de maior limite inferior conservador do ROI) e medida no TEST,
que fica estritamente depois do TRAIN + gap. O agregado OOS e a uniao
dos TESTs — nada do que a regra "acertou" no treino entra.

GAP/EMBARGO: `gap_days` entre train e test. O default (2 dias) espelha o
embargo de publicacao de resultados do corpus (`temporal.result_time`,
resultados publicados ~2 dias apos o kickoff): uma aposta no gap nao
entra em TREINO nem em TESTE daquela janela.

Metricas OOS por janela e agregadas:
    ROI, SE, t, Wilson (taxa de vitoria), bootstrap CI (ROI),
    Brier, LogLoss, ECE — sobre as probabilidades IMPLICITAS das odds
    reais usadas pela regra (p = 1/odd), nunca sobre previsao do modelo.

Este modulo so consome dados canonicos (a lista de apostas de
`value_strategy.collect_bets`): nao conhece providers, endpoints ou
detalhes HTTP. Adicionar um provider nao muda nada aqui.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .value_strategy import MARKETS, MAX_ODD, MIN_BOOKS, collect_bets

#: Versao do esquema do cache OOS. Mudou o layout/semantica -> bump,
#: caches antigos sao invalidados.
OOS_CACHE_SCHEMA_VERSION = 1

#: Configuracao DEFAULT do walk-forward: 2 anos de treino, 1 ano de teste,
#: embargo de 2 dias (publicacao de resultados), bandas candidatas em
#: torno da regra de producao.
DEFAULT_TRAIN_DAYS = 730
DEFAULT_TEST_DAYS = 365
#: Embargo: resultados do football-data.co.uk sao publicados ~2 dias
#: apos o kickoff (ver `temporal.result_time`). Apostas no gap ficam
#: fora de TREINO e TESTE da janela.
DEFAULT_GAP_DAYS = 2
DEFAULT_CANDIDATE_MAX_ODDS: tuple[float, ...] = (1.20, 1.25, 1.30, 1.40)
DEFAULT_MIN_TRAIN_BETS = 150
DEFAULT_MIN_TEST_BETS = 10
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_BOOTSTRAP_SEED = 424242

#: Stake plano usado na curva de equity do drawdown (fracao da banca).
DRAWDOWN_STAKE = 0.01

#: z one-sided 95% (o mesmo de `staking.Z_CONSERVATIVE`).
_Z_CONSERVATIVE = 1.6448536269514722


# --------------------------------------------------------------------------
# Config e fingerprint do cache
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardConfig:
    """Parametros que definem a medicao OOS — todos entram no fingerprint."""

    train_days: int = DEFAULT_TRAIN_DAYS
    test_days: int = DEFAULT_TEST_DAYS
    gap_days: int = DEFAULT_GAP_DAYS
    candidate_max_odds: tuple[float, ...] = DEFAULT_CANDIDATE_MAX_ODDS
    min_books: int = MIN_BOOKS
    min_train_bets: int = DEFAULT_MIN_TRAIN_BETS
    min_test_bets: int = DEFAULT_MIN_TEST_BETS
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED

    def validate(self) -> None:
        if self.train_days < 1 or self.test_days < 1:
            raise ValueError("train_days e test_days precisam ser >= 1")
        if self.gap_days < 0:
            raise ValueError("gap_days (embargo) nao pode ser negativo")
        if not self.candidate_max_odds:
            raise ValueError("sem bandas candidatas nao existe selecao")
        if self.min_train_bets < 1 or self.min_test_bets < 1:
            raise ValueError("minimos de apostas precisam ser >= 1")


def _oos_cache_path() -> Path:
    from .config import output_root

    return output_root() / "value_validation_oos.json"


def oos_cache_fingerprint(
    *,
    config: WalkForwardConfig,
    corpus_signature: str,
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
    markets: Sequence[str] = MARKETS,
    closing: bool = False,
    selection_mode: str = "conservative_lower_bound",
) -> str:
    """Fingerprint do cache OOS: TUDO que muda a medicao entra na chave.

    Alem dos parametros da regra e do corpus (como no fingerprint da
    validacao full-sample), entram os parametros de walk-forward —
    janelas, gap/embargo, bandas candidatas, minimos e bootstrap —
    porque mudar qualquer um deles produz OUTRA medicao OOS.
    """
    payload = {
        "schema": OOS_CACHE_SCHEMA_VERSION,
        "max_odd": float(max_odd),
        "min_books": int(min_books),
        "markets": list(markets),
        "closing": bool(closing),
        "corpus": corpus_signature,
        "selection_mode": selection_mode,
        "train_days": int(config.train_days),
        "test_days": int(config.test_days),
        "gap_days": int(config.gap_days),
        "candidate_max_odds": [float(b) for b in config.candidate_max_odds],
        "wf_min_books": int(config.min_books),
        "min_train_bets": int(config.min_train_bets),
        "min_test_bets": int(config.min_test_bets),
        "bootstrap_resamples": int(config.bootstrap_resamples),
        "bootstrap_seed": int(config.bootstrap_seed),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


# --------------------------------------------------------------------------
# Janelas
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WFWindow:
    """Uma janela: TREINO -> GAP -> TESTE (datas de kickoff)."""

    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    gap_days: int

    @property
    def embargo_days(self) -> int:
        return self.gap_days


def build_windows(
    first_day: str, last_day: str, config: WalkForwardConfig
) -> list[WFWindow]:
    """Janelas deslizantes: avancam `test_days` por vez.

    Teste de janelas consecutivas sao ADJACENTES (nunca sobrepostos); o
    gap/embargo fica entre train_end e test_start. Uma janela so nasce
    quando ha espaco para o teste completo.
    """
    config.validate()
    first = date.fromisoformat(first_day)
    last = date.fromisoformat(last_day)
    out: list[WFWindow] = []
    train_start = first
    index = 0
    while True:
        train_end = train_start + timedelta(days=config.train_days)
        test_start = train_end + timedelta(days=config.gap_days)
        test_end = test_start + timedelta(days=config.test_days)
        if test_start > last:
            break
        out.append(WFWindow(
            index=index,
            train_start=train_start.isoformat(),
            train_end=train_end.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
            gap_days=config.gap_days,
        ))
        index += 1
        train_start += timedelta(days=config.test_days)
    return out


def audit_windows(windows: Sequence[WFWindow]) -> list[str]:
    """Violacoes de disciplina temporal das janelas. Vazio = ok.

    - teste comeca APENAS depois de train_end + gap (embargo aplicado);
    - testes de janelas diferentes nunca se sobrepoem;
    - treinos nunca avancam sobre o teste da propria janela.
    """
    violations: list[str] = []
    for w in windows:
        gap = (date.fromisoformat(w.test_start)
               - date.fromisoformat(w.train_end)).days
        if gap < w.gap_days:
            violations.append(
                f"janela {w.index}: embargo nao aplicado "
                f"(test_start - train_end = {gap}d < {w.gap_days}d)"
            )
        if date.fromisoformat(w.test_end) <= date.fromisoformat(w.test_start):
            violations.append(f"janela {w.index}: teste vazio")
    for a, b in zip(windows, windows[1:]):
        if date.fromisoformat(b.test_start) < date.fromisoformat(a.test_end):
            violations.append(
                f"teste das janelas {a.index} e {b.index} sobrepostos"
            )
    return violations


# --------------------------------------------------------------------------
# Metricas
# --------------------------------------------------------------------------


def _ret(odd: float, res: str) -> float:
    if res == "win":
        return odd - 1.0
    if res == "push":
        return 0.0
    return -1.0


def _roi_stats(rets: Sequence[float]) -> tuple[float, float, float]:
    n = len(rets)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(rets) / n
    var = (sum((x - mean) ** 2 for x in rets) / (n - 1)) if n > 1 else 0.0
    se = math.sqrt(var / n) if n > 1 else 0.0
    return mean, se, (mean / se if se > 0 else 0.0)


def wilson_ci(wins: int, losses: int) -> tuple[float, float]:
    """IC 95% de Wilson para a taxa de vitoria (pushes fora do denominador)."""
    n = wins + losses
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    z = 1.959963984540054
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - margin) / denom), min(1.0, (center + margin) / denom)


def _bootstrap_ci(
    rets: Sequence[float], resamples: int, seed: int
) -> tuple[float, float]:
    if len(rets) < 2:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(rets)
    means = sorted(
        sum(rng.choices(rets, k=n)) / n for _ in range(resamples)
    )
    return (
        means[int(0.025 * resamples)],
        means[int(0.975 * resamples)],
    )


#: Binns do ECE sobre probabilidade implicita (odds < 1.40 -> p > 0.71).
_ECE_BINS: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0 + 1e-9)


def _prob_metrics(bets: Sequence[dict], odd_key: str = "odd") -> dict[str, float]:
    """Brier/LogLoss/ECE das probabilidades implicitas das odds reais.

    p = 1/odd; y = 1 se a aposta ganhou, 0 se perdeu (pushs ficam fora:
    nao sao vitoria nem derrota). E o poder PREDICTIVO do preco que a
    regra efetivamente pega — sem modelo no meio.
    """
    ps: list[float] = []
    ys: list[int] = []
    for b in bets:
        if b.get("res") == "push":
            continue
        odd = float(b[odd_key])
        if odd <= 1.0:
            continue
        ps.append(1.0 / odd)
        ys.append(1 if b.get("res") == "win" else 0)
    n = len(ps)
    if n == 0:
        return {"brier": 0.0, "logloss": 0.0, "ece": 0.0, "n_prob": 0}
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    logloss = -sum(
        (y * math.log(p) + (1 - y) * math.log(1.0 - p))
        if 0.0 < p < 1.0 else 0.0
        for p, y in zip(ps, ys)
    ) / n
    # ECE: |confianca media - acerto medio| por bin, ponderado por n
    ece = 0.0
    for lo, hi in zip(_ECE_BINS, _ECE_BINS[1:]):
        bucket = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi]
        if not bucket:
            continue
        conf = sum(p for p, _ in bucket) / len(bucket)
        acc = sum(y for _, y in bucket) / len(bucket)
        ece += len(bucket) / n * abs(conf - acc)
    return {
        "brier": round(brier, 6),
        "logloss": round(logloss, 6),
        "ece": round(ece, 6),
        "n_prob": n,
    }


def _max_drawdown(rets: Sequence[float], stake: float = DRAWDOWN_STAKE) -> float:
    """Pico-a-vale da banca com stake plano (fracao da banca por aposta)."""
    bank = 1.0
    peak = 1.0
    dd = 0.0
    for r in rets:
        bank += stake * r
        peak = max(peak, bank)
        dd = max(dd, peak - bank)
    return dd


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleView:
    """Como as apostas da regra sao derivadas da lista completa.

    `max_odd=None` -> modo SELECAO (a banda e escolhida no train de cada
    janela); float -> banda fixa (robustez/ablação). `use_median` troca
    a melhor odd pela mediana (ablação do line-shopping).
    """

    max_odd: float | None = None
    min_books: int = MIN_BOOKS
    use_median: bool = False

    @property
    def odd_key(self) -> str:
        return "median" if self.use_median else "odd"


def _bet_odd(bet: dict, view: RuleView) -> float:
    return float(bet["median" if view.use_median else "odd"])


def _eligible(bet: dict, view: RuleView, max_odd: float) -> bool:
    if int(bet.get("n_books", 0)) < view.min_books:
        return False
    return _bet_odd(bet, view) < max_odd


@dataclass
class WindowResult:
    """Resultado OOS de uma janela."""

    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    gap_days: int
    selected_max_odd: float | None = None
    n_train_bets: int = 0
    n_test_bets: int = 0
    roi: float | None = None
    se: float | None = None
    t: float | None = None
    wilson_low: float | None = None
    wilson_high: float | None = None
    brier: float | None = None
    logloss: float | None = None
    ece: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WalkForwardResult:
    """Agregado OOS + por janela + apostas OOS (para o promotion gate)."""

    config: WalkForwardConfig
    n_windows: int = 0
    n_windows_valid: int = 0
    n_bets_oos: int = 0
    roi: float | None = None
    roi_se: float | None = None
    roi_t: float | None = None
    wilson_low: float | None = None
    wilson_high: float | None = None
    bootstrap_low: float | None = None
    bootstrap_high: float | None = None
    brier: float | None = None
    logloss: float | None = None
    ece: float | None = None
    avg_odd: float | None = None
    max_drawdown: float | None = None
    embargo_days: int = 0
    windows: list[WindowResult] = field(default_factory=list)
    #: apostas OOS da regra e do MERCADO (todas as odds) nos mesmos
    #: testes — insumo dos segmentos do promotion gate
    oos_rule_bets: list[dict] = field(default_factory=list)
    oos_market_bets: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["windows"] = [w.to_dict() for w in self.windows]
        return out


def _select_band(
    train_bets_all: list[dict], config: WalkForwardConfig, view: RuleView
) -> tuple[float | None, int]:
    """Escolhe a banda candidata SO com as apostas do TRAIN.

    Criterio conservador: maior limite inferior one-sided 95% do ROI
    (roi - z*se). Banda sem `min_train_bets` apostas no train nao e
    candidata — sem amostra nao ha selecao.
    """
    best_band: float | None = None
    best_lower = -math.inf
    best_n = 0
    for band in config.candidate_max_odds:
        rets = [
            _ret(_bet_odd(b, view), b.get("res", "loss"))
            for b in train_bets_all
            if _eligible(b, view, band)
        ]
        if len(rets) < config.min_train_bets:
            continue
        roi, se, _t = _roi_stats(rets)
        lower = roi - _Z_CONSERVATIVE * se
        if lower > best_lower:
            best_lower = lower
            best_band = band
            best_n = len(rets)
    return best_band, best_n


def run_walk_forward(
    bets: Sequence[dict],
    config: WalkForwardConfig | None = None,
    view: RuleView | None = None,
) -> WalkForwardResult:
    """Executa o walk-forward OOS sobre a lista de apostas (canonica).

    `bets` e a saida de `collect_bets(99.0, min_books=1)`: TODAS as
    linhas apostaveis, cada uma com odd (melhor), mediana, n_books,
    resultado e data. O harness filtra por janela e por view — nada de
    providers ou HTTP aqui.

    Leakage: se a auditoria de janelas acusar violacao, o harness LEVANTA
    — medicao com janelas invalidas nao e medicao.
    """
    config = config or WalkForwardConfig()
    view = view or RuleView()
    config.validate()
    if not bets:
        return WalkForwardResult(config=config, embargo_days=config.gap_days)

    days = sorted({str(b["d"]) for b in bets})
    windows = build_windows(days[0], days[-1], config)
    violations = audit_windows(windows)
    if violations:
        raise ValueError(
            "janelas walk-forward com violacao temporal: "
            + "; ".join(violations)
        )

    result = WalkForwardResult(
        config=config, n_windows=len(windows), embargo_days=config.gap_days
    )

    for window in windows:
        train_bets = [
            b for b in bets
            if window.train_start <= str(b["d"]) < window.train_end
        ]
        # APOSTAS NO GAP FICAM FORA: nem treino, nem teste (embargo)
        test_bets = [
            b for b in bets
            if window.test_start <= str(b["d"]) < window.test_end
        ]

        if view.max_odd is None:
            band, n_train = _select_band(train_bets, config, view)
        else:
            band = float(view.max_odd)
            n_train = sum(
                1 for b in train_bets if _eligible(b, view, band)
            )

        win = WindowResult(
            index=window.index,
            train_start=window.train_start,
            train_end=window.train_end,
            test_start=window.test_start,
            test_end=window.test_end,
            gap_days=window.gap_days,
            selected_max_odd=band,
            n_train_bets=n_train,
        )

        if band is not None:
            rule_test = [
                b for b in test_bets if _eligible(b, view, band)
            ]
            win.n_test_bets = len(rule_test)
            if rule_test:
                rets = [
                    _ret(_bet_odd(b, view), b.get("res", "loss"))
                    for b in rule_test
                ]
                roi, se, t = _roi_stats(rets)
                wins = sum(1 for b in rule_test if b.get("res") == "win")
                losses = sum(1 for b in rule_test if b.get("res") == "loss")
                lo, hi = wilson_ci(wins, losses)
                probs = _prob_metrics(rule_test, view.odd_key)
                win.roi = round(roi, 6)
                win.se = round(se, 6)
                win.t = round(t, 4)
                win.wilson_low = round(lo, 6)
                win.wilson_high = round(hi, 6)
                win.brier = probs["brier"]
                win.logloss = probs["logloss"]
                win.ece = probs["ece"]
                if len(rule_test) >= config.min_test_bets:
                    result.oos_rule_bets.extend(rule_test)
                    # mercado: todas as linhas apostaveis do MESMO teste
                    result.oos_market_bets.extend(test_bets)
        result.windows.append(win)

    result.n_windows_valid = sum(1 for w in result.windows if w.n_test_bets > 0)
    pooled = list(result.oos_rule_bets)
    result.n_bets_oos = len(pooled)
    if pooled:
        rets = [_ret(_bet_odd(b, view), b.get("res", "loss")) for b in pooled]
        roi, se, t = _roi_stats(rets)
        wins = sum(1 for b in pooled if b.get("res") == "win")
        losses = sum(1 for b in pooled if b.get("res") == "loss")
        lo, hi = wilson_ci(wins, losses)
        blo, bhi = _bootstrap_ci(
            rets, config.bootstrap_resamples, config.bootstrap_seed
        )
        probs = _prob_metrics(pooled, view.odd_key)
        result.roi = round(roi, 6)
        result.roi_se = round(se, 6)
        result.roi_t = round(t, 4)
        result.wilson_low = round(lo, 6)
        result.wilson_high = round(hi, 6)
        result.bootstrap_low = round(blo, 6)
        result.bootstrap_high = round(bhi, 6)
        result.brier = probs["brier"]
        result.logloss = probs["logloss"]
        result.ece = probs["ece"]
        odds = [_bet_odd(b, view) for b in pooled]
        result.avg_odd = round(sum(odds) / len(odds), 4)
        result.max_drawdown = round(_max_drawdown(rets), 6)
    return result


# --------------------------------------------------------------------------
# Robustez e ablação (cenarios OOS sobre o MESMO harness)
# --------------------------------------------------------------------------


def robustness_scenarios(
    bets: Sequence[dict],
    config: WalkForwardConfig | None = None,
) -> list[dict[str, Any]]:
    """Variações de parâmetros da regra, cada uma medida OOS.

    NAO e para escolher campeao: e para ver o quanto o resultado OOS
    depende dos parametros exatos. Cenario, amostra, resultado e
    degradação vs baseline ficam registrados.
    """
    config = config or WalkForwardConfig()
    baseline_view = RuleView(max_odd=MAX_ODD)
    baseline = run_walk_forward(bets, config, baseline_view)

    scenarios: list[dict[str, Any]] = []
    variations: list[tuple[str, RuleView]] = [
        (f"max_odd={b}", RuleView(max_odd=b))
        for b in (1.25, 1.35)
    ] + [
        (f"min_books={m}", RuleView(max_odd=MAX_ODD, min_books=m))
        for m in (2, 4)
    ]
    for name, view in variations:
        outcome = run_walk_forward(bets, config, view)
        degradation = (
            None if (baseline.roi is None or outcome.roi is None)
            else round(outcome.roi - baseline.roi, 6)
        )
        scenarios.append({
            "scenario": name,
            "n_bets_oos": outcome.n_bets_oos,
            "roi": outcome.roi,
            "roi_se": outcome.roi_se,
            "wilson_low": outcome.wilson_low,
            "n_windows_valid": outcome.n_windows_valid,
            "degradation_vs_baseline": degradation,
        })
    return scenarios


def ablation_scenarios(
    bets: Sequence[dict],
    config: WalkForwardConfig | None = None,
) -> list[dict[str, Any]]:
    """Ablação de COMPONENTES da regra, cada um medido OOS.

    Baseline: banda fixa de produção + consenso minimo + melhor odd.
    Variantes removem UM componente por vez. Objetivo: descobrir o que
    realmente contribui — nao promover vencedor automaticamente.
    """
    config = config or WalkForwardConfig()
    baseline = run_walk_forward(bets, config, RuleView(max_odd=MAX_ODD))

    variants: list[tuple[str, RuleView]] = [
        ("sem_line_shopping (mediana em vez da melhor odd)",
         RuleView(max_odd=MAX_ODD, use_median=True)),
        ("sem_consenso_minimo (min_books=1)",
         RuleView(max_odd=MAX_ODD, min_books=1)),
    ]
    out: list[dict[str, Any]] = [{
        "scenario": "baseline (banda + consenso + melhor odd)",
        "n_bets_oos": baseline.n_bets_oos,
        "roi": baseline.roi,
        "roi_se": baseline.roi_se,
        "n_windows_valid": baseline.n_windows_valid,
        "delta_vs_baseline": 0.0 if baseline.roi is not None else None,
    }]
    for name, view in variants:
        outcome = run_walk_forward(bets, config, view)
        delta = (
            None if (baseline.roi is None or outcome.roi is None)
            else round(outcome.roi - baseline.roi, 6)
        )
        out.append({
            "scenario": name,
            "n_bets_oos": outcome.n_bets_oos,
            "roi": outcome.roi,
            "roi_se": outcome.roi_se,
            "n_windows_valid": outcome.n_windows_valid,
            "delta_vs_baseline": delta,
        })
    return out


# --------------------------------------------------------------------------
# Cache OOS (escrita e leitura com fingerprint)
# --------------------------------------------------------------------------


def compute_oos_validation(
    config: WalkForwardConfig | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Executa a validação OOS completa sobre o corpus real e devolve o payload.

    Uma UNICA passada pelo corpus (`collect_bets`) alimenta walk-forward,
    robustez e ablação — sem refetch, sem parsing repetido.
    """
    from .football_data_uk import FootballDataClient

    config = config or WalkForwardConfig()
    corpus = FootballDataClient().corpus_signature()
    if progress is not None:
        progress(0, 3, "coletando apostas do corpus…")
    bets = collect_bets(99.0, 1, closing=False, progress=progress)
    if progress is not None:
        progress(1, 3, "walk-forward OOS…")
    wf = run_walk_forward(bets, config)
    if progress is not None:
        progress(2, 3, "robustez e ablação…")
    robustness = robustness_scenarios(bets, config)
    ablation = ablation_scenarios(bets, config)

    fingerprint = oos_cache_fingerprint(config=config, corpus_signature=corpus)
    return {
        "schema": OOS_CACHE_SCHEMA_VERSION,
        "cache_fingerprint": fingerprint,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"),
        "config": {
            "train_days": config.train_days,
            "test_days": config.test_days,
            "gap_days": config.gap_days,
            "candidate_max_odds": list(config.candidate_max_odds),
            "min_books": config.min_books,
            "min_train_bets": config.min_train_bets,
            "min_test_bets": config.min_test_bets,
            "bootstrap_resamples": config.bootstrap_resamples,
            "bootstrap_seed": config.bootstrap_seed,
        },
        "aggregate": {
            "n_windows": wf.n_windows,
            "n_windows_valid": wf.n_windows_valid,
            "n_bets_oos": wf.n_bets_oos,
            "roi": wf.roi,
            "roi_se": wf.roi_se,
            "roi_t": wf.roi_t,
            "wilson_low": wf.wilson_low,
            "wilson_high": wf.wilson_high,
            "bootstrap_low": wf.bootstrap_low,
            "bootstrap_high": wf.bootstrap_high,
            "brier": wf.brier,
            "logloss": wf.logloss,
            "ece": wf.ece,
            "avg_odd": wf.avg_odd,
            "max_drawdown": wf.max_drawdown,
            "embargo_days": wf.embargo_days,
        },
        "windows": [w.to_dict() for w in wf.windows],
        "robustness": robustness,
        "ablation": ablation,
        "oos_rule_bets": wf.oos_rule_bets,
        "oos_market_bets": wf.oos_market_bets,
    }


def save_oos_validation(payload: dict[str, Any]) -> Path:
    path = _oos_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return path


@dataclass(frozen=True)
class OosEvidence:
    """Leitura VALIDADA do cache OOS (fingerprint conferido)."""

    n_windows: int
    n_windows_valid: int
    n_bets_oos: int
    roi: float | None
    roi_se: float | None
    avg_odd: float | None
    max_drawdown: float | None
    embargo_days: int
    oos_rule_bets: tuple[dict, ...]
    oos_market_bets: tuple[dict, ...]
    aggregate: dict[str, Any]

    def promotion_segments(self) -> list:
        """Segmentos (liga x temporada) do promotion gate — SO apostas OOS.

        metrics = regra; baseline_metrics = MERCADO (todas as linhas dos
        mesmos blocos de teste). Nada de full-sample: cada aposta aqui
        saiu de um bloco TEST de janela walk-forward.
        """
        from .models.promotion import SegmentResult

        def _segment_metrics(bets: list[dict]) -> dict[str, float]:
            rets = [_ret(b["odd"], b.get("res", "loss")) for b in bets]
            roi, _se, _t = _roi_stats(rets)
            probs = _prob_metrics(bets)
            return {
                "roi": roi,
                "brier": probs["brier"],
                "logloss": probs["logloss"],
                "ece": probs["ece"],
            }

        rule_by_key: dict[tuple[str, str], list[dict]] = {}
        for b in self.oos_rule_bets:
            rule_by_key.setdefault((str(b.get("lg", "")), str(b["d"])[:4]), []).append(b)
        market_by_key: dict[tuple[str, str], list[dict]] = {}
        for b in self.oos_market_bets:
            market_by_key.setdefault((str(b.get("lg", "")), str(b["d"])[:4]), []).append(b)

        segments: list[SegmentResult] = []
        for key, rule_bets in sorted(rule_by_key.items()):
            market_bets = market_by_key.get(key)
            if not market_bets:
                continue
            segments.append(SegmentResult(
                league=key[0], season=key[1],
                n_matches=len(market_bets),
                metrics=_segment_metrics(rule_bets),
                baseline_metrics=_segment_metrics(market_bets),
            ))
        return segments


def cached_oos_evidence(
    config: WalkForwardConfig | None = None,
) -> OosEvidence | None:
    """Cache OOS LEGIVEL para a configuração atual, ou None.

    Como `cached_validation`: nunca recalcula (o corpus tem ~195 mil
    partidas), nunca serve JSON cru. Fingerprint divergente (corpus novo,
    janela/gap/bandas/bootstrap diferentes) = cache stale = None. Quem
    precisa da evidência nova roda a tool offline.
    """
    from .football_data_uk import FootballDataClient

    config = config or WalkForwardConfig()
    try:
        path = _oos_cache_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != OOS_CACHE_SCHEMA_VERSION:
            return None
    except (OSError, json.JSONDecodeError):
        return None
    expected = oos_cache_fingerprint(
        config=config,
        corpus_signature=FootballDataClient().corpus_signature(),
    )
    if payload.get("cache_fingerprint") != expected:
        return None
    agg = payload.get("aggregate") or {}
    return OosEvidence(
        n_windows=int(agg.get("n_windows", 0)),
        n_windows_valid=int(agg.get("n_windows_valid", 0)),
        n_bets_oos=int(agg.get("n_bets_oos", 0)),
        roi=agg.get("roi"),
        roi_se=agg.get("roi_se"),
        avg_odd=agg.get("avg_odd"),
        max_drawdown=agg.get("max_drawdown"),
        embargo_days=int(agg.get("embargo_days", 0)),
        oos_rule_bets=tuple(payload.get("oos_rule_bets") or ()),
        oos_market_bets=tuple(payload.get("oos_market_bets") or ()),
        aggregate=dict(agg),
    )


# --------------------------------------------------------------------------
# CLV prospectivo do store — evidência para o promotion gate
# --------------------------------------------------------------------------


def prospective_clv_evidence(store=None) -> dict[str, Any]:
    """Resumo do CLV PROSPECTIVO real (entradas FIRST-WINS do store).

    Cada entrada em `clv_entries` foi registrada no instante da decisão
    com guarda PIT (`register_entry`); o CLV só conta quando
    entry < closing < kickoff (`clv_prospective`). n = linhas com CLV
    válido; sem linhas, mean 0 e n 0 — o gate reprova por amostra
    (MIN_CLV_SAMPLE). Nada de CLV retrospectivo aqui.
    """
    from .odds_snapshots import OddsSnapshotStore

    store = store or OddsSnapshotStore()
    pcts: list[float] = []
    for rec in store.clv_entries():
        result = store.clv_prospective(
            rec.match_key, rec.market, rec.outcome,
            entry_odd=rec.entry_odd,
            entry_timestamp=rec.entry_timestamp,
        )
        if result.status == "OK" and result.clv_percentage is not None:
            pcts.append(float(result.clv_percentage))
    n = len(pcts)
    if n == 0:
        return {"mean": 0.0, "n": 0, "prospective": True}
    mean = statistics.fmean(pcts)
    if n >= 2:
        se = statistics.stdev(pcts) / math.sqrt(n)
        ci_low = mean - 1.959963984540054 * se
        ci_high = mean + 1.959963984540054 * se
    else:
        ci_low = ci_high = mean
    return {
        "mean": round(mean, 6),
        "ci_low": round(ci_low, 6),
        "ci_high": round(ci_high, 6),
        "n": n,
        "prospective": True,
    }
