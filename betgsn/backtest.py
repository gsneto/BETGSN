"""BETGSN :: backtest — valida o modelo contra resultado real (out-of-sample).

Dois niveis de verdade, sem lookahead:

1. CALIBRACAO (independente de odds): as probabilidades do modelo sao
   honestas? Mede log-loss, Brier e a curva de calibracao (prob prevista
   vs frequencia empirica por decil).

2. APOSTA (depende de odds): apostar nos sinais de EV >= minimo teria dado
   lucro? Mede ROI, hit rate, lucro, drawdown e o gap entre EV previsto e
   retorno realizado.

Metodo: split temporal (treina nos primeiros N% dos jogos, testa no resto).
O "mercado" adversario e um modelo INGENUO que ignora a forca dos times
(so usa a media da liga e a vantagem de casa) + margem + erro. Se o nosso
modelo tem valor real, ele bate esse mercado nos jogos desequilibrados.

Isso NAO prova lucro no mercado real — prova que o modelo agrega
informacao sobre um baseline que nao olha forca de time. O dataset e
sintetico. O framework, nao.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import log

from .data import LeagueDataset, synthesize_odds
from .engine import kelly_fraction, stake
from .markets import market_1x2, market_btts, market_totals
from .model import build_score_matrix, fit_ratings

OUTCOME_1X2 = "Resultado Final (1X2)"
OUTCOME_OU = "Total de Gols"
OUTCOME_BTTS = "Ambas Marcam"


# --------------------------------------------------------------------------
# helpers de resultado
# --------------------------------------------------------------------------


def outcome_1x2(hg: int, ag: int) -> str:
    return "1" if hg > ag else ("X" if hg == ag else "2")


def outcome_ou(hg: int, ag: int, line: float = 2.5) -> str:
    return f"Over {line}" if hg + ag > line else f"Under {line}"


def outcome_btts(hg: int, ag: int) -> str:
    return "BTTS Sim" if (hg > 0 and ag > 0) else "BTTS Nao"


def naive_market_probs(league_goals: float, home_advantage: float) -> dict[str, dict[str, float]]:
    """Mercado ingenua: ignora a forca dos times. Mesma prob pra todo jogo."""
    base = league_goals / (1.0 + home_advantage)
    m = build_score_matrix(base * home_advantage, base)
    return {
        OUTCOME_1X2: market_1x2(m),
        OUTCOME_OU: market_totals(m, lines=(2.5,)),
        OUTCOME_BTTS: market_btts(m),
    }


# --------------------------------------------------------------------------
# metricas de calibracao
# --------------------------------------------------------------------------


@dataclass
class Calibration:
    logloss: float
    brier: float
    n: int
    accuracy: float          # taxa de acerto do "top pick" (1X2)
    bins: list[tuple[float, float, int]] = field(default_factory=list)  # (pred, empirico, n)


def _bin_calibration(confs: list[float], wins: list[int], n_bins: int = 10) -> list[tuple[float, float, int]]:
    """Agrupa previsoes por decil de confianca e mede a frequencia empirica."""
    pairs = sorted(zip(confs, wins))
    size = max(1, len(pairs) // n_bins)
    out: list[tuple[float, float, int]] = []
    for i in range(0, len(pairs), size):
        chunk = pairs[i:i + size]
        pred = sum(p for p, _ in chunk) / len(chunk)
        emp = sum(w for _, w in chunk) / len(chunk)
        out.append((round(pred, 3), round(emp, 3), len(chunk)))
    return out


def calibrate_1x2(preds: list[dict[str, float]], actuals: list[str]) -> Calibration:
    """Calibracao do mercado 1X2 (3 resultados).

    Confiabilidade correta para multiclasse: agrupa TODOS os pares
    (resultado, prob_prevista) de todos os jogos e compara, por faixa de
    prob, a frequencia com que aquele resultado realmente aconteceu.
    'Quando o modelo diz 30%, acontece 30% das vezes.'
    """
    n = len(actuals)
    if n == 0:
        return Calibration(0.0, 0.0, 0, 0.0)
    outcomes = ["1", "X", "2"]
    ll = 0.0
    brier = 0.0
    confs: list[float] = []
    wins: list[int] = []
    correct = 0
    for p, a in zip(preds, actuals):
        # log-loss e Brier sobre os 3 resultados
        ll += sum(-log(max(1e-9, min(1.0 - 1e-9, p.get(o, 0.0))))
                  for o in outcomes if o == a)  # so o resultado ocorrido contribui
        brier += sum((p.get(o, 0.0) - (1.0 if o == a else 0.0)) ** 2 for o in outcomes)
        # top pick
        top = max(outcomes, key=lambda o: p.get(o, 0.0))
        correct += 1 if a == top else 0
        # confiabilidade: pool de todos os resultados
        for o in outcomes:
            confs.append(p.get(o, 0.0))
            wins.append(1 if o == a else 0)
    return Calibration(
        logloss=ll / n,
        brier=brier / n,
        n=n,
        accuracy=correct / n,
        bins=_bin_calibration(confs, wins),
    )


# --------------------------------------------------------------------------
# simulacao de aposta
# --------------------------------------------------------------------------


@dataclass
class BetRecord:
    match: str
    outcome: str
    market: str
    prob: float
    odd: float
    book: str
    ev: float
    stake: float
    won: bool
    profit: float


@dataclass
class Betting:
    bankroll_start: float
    bankroll_end: float
    n_bets: int
    n_wins: int
    hit_rate: float
    total_staked: float
    profit: float
    roi: float               # profit / total_staked
    ev_mean_pred: float      # EV medio previsto
    return_mean_real: float  # retorno medio realizado por unidade
    max_drawdown: float
    records: list[BetRecord] = field(default_factory=list)


def _best_odds(books: dict[str, dict[str, float]]) -> dict[str, tuple[float, str]]:
    best: dict[str, tuple[float, str]] = {}
    for book, outcomes in books.items():
        for oc, odd in outcomes.items():
            if odd and (oc not in best or odd > best[oc][0]):
                best[oc] = (odd, book)
    return best


# --------------------------------------------------------------------------
# backtest principal
# --------------------------------------------------------------------------


@dataclass
class BacktestResult:
    split: float
    n_train: int
    n_test: int
    calibration: Calibration
    betting: Betting
    summary: str = ""


def _retired_split_backtest(
    dataset: LeagueDataset,
    split: float = 0.7,
    bankroll: float = 1000.0,
    min_ev: float = 0.03,
    kelly_frac: float = 0.25,
    markets: tuple[str, ...] = (OUTCOME_1X2, OUTCOME_OU, OUTCOME_BTTS),
) -> BacktestResult:
    """Walk-forward simples: treina no passado, testa no futuro."""
    history = dataset.history
    n = len(history)
    cut = int(n * split)
    if cut < 10 or n - cut < 10:
        raise ValueError(f"split invalido: train={cut} test={n - cut} (precisa >= 10 cada)")

    train, test = history[:cut], history[cut:]
    ratings = fit_ratings(train, dataset.teams, home_advantage=dataset.home_advantage)

    # --- prepara predicoes do modelo e odds do mercado ingenuo ------------
    preds_1x2: list[dict[str, float]] = []
    actuals_1x2: list[str] = []
    naive_truth: dict[str, dict[str, dict[str, float]]] = {}
    model_by_match: dict[str, dict[str, dict[str, float]]] = {}

    base_naive = naive_market_probs(dataset.league_goals, dataset.home_advantage)

    for m in test:
        hr = ratings.get(m.home)
        ar = ratings.get(m.away)
        if hr is None or ar is None:
            continue
        from .model import expected_goals
        lam_h, lam_a = expected_goals(hr, ar, dataset.league_goals,
                                      home_advantage=dataset.home_advantage,
                                      attack_blend=0.5)
        mx = build_score_matrix(lam_h, lam_a)
        model_probs = {
            OUTCOME_1X2: market_1x2(mx),
            OUTCOME_OU: market_totals(mx, lines=(2.5,)),
            OUTCOME_BTTS: market_btts(mx),
        }
        key = f"{m.home} vs {m.away}"
        model_by_match[key] = model_probs
        naive_truth[key] = base_naive
        a1 = outcome_1x2(m.home_goals, m.away_goals)
        preds_1x2.append(model_probs[OUTCOME_1X2])
        actuals_1x2.append(a1)

    cal = calibrate_1x2(preds_1x2, actuals_1x2)

    # odds do mercado ingenuo
    odds_by_match = synthesize_odds(naive_truth)

    # --- simulacao de aposta ----------------------------------------------
    b = bankroll
    peak = bankroll
    max_dd = 0.0
    total_staked = 0.0
    wins = 0
    evs: list[float] = []
    returns: list[float] = []
    records: list[BetRecord] = []

    for m in test:
        key = f"{m.home} vs {m.away}"
        model_probs = model_by_match.get(key)
        match_odds = odds_by_match.get(key)
        if not model_probs or not match_odds:
            continue
        for market in markets:
            probs = model_probs.get(market)
            books = match_odds.get(market)
            if not probs or not books:
                continue
            best = _best_odds(books)
            for outcome, prob in probs.items():
                if outcome not in best:
                    continue
                odd, book = best[outcome]
                ev = prob * odd - 1.0
                if ev < min_ev:
                    continue
                st = stake(b, prob, odd, fraction=kelly_frac, cap=0.05)
                if st < 0.01:
                    continue
                # resolve
                if market == OUTCOME_1X2:
                    hit = outcome == outcome_1x2(m.home_goals, m.away_goals)
                elif market == OUTCOME_OU:
                    hit = outcome == outcome_ou(m.home_goals, m.away_goals)
                elif market == OUTCOME_BTTS:
                    hit = outcome == outcome_btts(m.home_goals, m.away_goals)
                else:
                    continue
                profit = st * (odd - 1.0) if hit else -st
                b += profit
                total_staked += st
                wins += 1 if hit else 0
                evs.append(ev)
                returns.append((odd - 1.0) if hit else -1.0)
                if b > peak:
                    peak = b
                dd = (peak - b) / peak if peak > 0 else 0.0
                if dd > max_dd:
                    max_dd = dd
                records.append(BetRecord(key, outcome, market, prob, odd, book,
                                         ev, st, hit, round(profit, 2)))

    n_bets = len(records)
    betting = Betting(
        bankroll_start=bankroll,
        bankroll_end=round(b, 2),
        n_bets=n_bets,
        n_wins=wins,
        hit_rate=wins / n_bets if n_bets else 0.0,
        total_staked=round(total_staked, 2),
        profit=round(b - bankroll, 2),
        roi=(b - bankroll) / total_staked if total_staked > 0 else 0.0,
        ev_mean_pred=sum(evs) / len(evs) if evs else 0.0,
        return_mean_real=sum(returns) / len(returns) if returns else 0.0,
        max_drawdown=max_dd,
        records=records,
    )

    summary = _summary(n_train=cut, n_test=len(test), cal=cal, betting=betting,
                       min_ev=min_ev)
    return BacktestResult(split=split, n_train=cut, n_test=len(test),
                          calibration=cal, betting=betting, summary=summary)


def _summary(n_train: int, n_test: int, cal: Calibration, betting: Betting,
             min_ev: float) -> str:
    lines = [
        "=" * 70,
        "BETGSN — BACKTEST (split temporal, sem lookahead)",
        "=" * 70,
        f"treino: {n_train} jogos   teste: {n_test} jogos   (min_ev = {min_ev * 100:.0f}%)",
        "",
        "--- CALIBRACAO (1X2) ---",
        f"log-loss : {cal.logloss:.4f}   (uniforme = 1.0986; menor = melhor)",
        f"Brier    : {cal.brier:.4f}   (uniforme = 0.6667; menor = melhor)",
        f"acerto do top pick: {cal.accuracy * 100:.1f}%  (chute = 33.3%)",
        "decil (prob prevista -> frequencia real):",
    ]
    for pred, emp, cnt in cal.bins:
        bar = "#" * int(round(emp * 40))
        lines.append(f"  {pred:4.2f} -> {emp:4.2f}  ({cnt:3d})  {bar}")
    lines += [
        "",
        "--- APOSTA (mercado ingenuo = ignora forca dos times) ---",
        f"banca inicial : {betting.bankroll_start:8.2f}",
        f"banca final   : {betting.bankroll_end:8.2f}",
        f"apostas       : {betting.n_bets}   acertos: {betting.n_wins}  "
        f"(hit rate {betting.hit_rate * 100:.1f}%)",
        f"total apostado: {betting.total_staked:8.2f}",
        f"lucro         : {betting.profit:+8.2f}   ROI {betting.roi * 100:+.1f}%",
        f"EV previsto medio : {betting.ev_mean_pred * 100:+.1f}%",
        f"retorno medio real: {betting.return_mean_real * 100:+.1f}%",
        f"max drawdown      : {betting.max_drawdown * 100:.1f}%",
    ]
    gap = betting.ev_mean_pred - betting.return_mean_real
    lines.append(
        f"gap EV vs realizado: {gap * 100:+.1f}pp  "
        f"({'modelo subestima' if gap < 0 else 'modelo superestima' if gap > 0.02 else 'bem calibrado'})"
    )
    return "\n".join(lines)


# Contrato público preservado; execução exclusivamente pelo motor walk-forward.
from .backtest_compat import run_legacy as run_backtest
