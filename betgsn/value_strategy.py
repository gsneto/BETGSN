"""BETGSN :: value_strategy — a vantagem que sobreviveu ao teste.

O que este modulo e
-------------------
A unica estrategia com vantagem ESTATISTICA confirmada nos 195.672 jogos
do cache (38 competicoes, 2000-2026):

    apostar em favoritos MUITO curtos, pegando a MELHOR odd entre as casas.

Nao usa o modelo do BETGSN. O modelo foi testado e nao adiciona informacao
ao mercado (coeficiente z=+0,59 em 80 mil observacoes — indistinguivel de
zero). Esta estrategia usa a ESTRUTURA do mercado, nao previsao.

Por que funciona
----------------
Favourite-longshot bias: o mercado paga favoritos um pouco abaixo do justo
e longshots um pouco acima. A distorcao e pequena por aposta, mas pegar a
melhor odd entre ~10 casas recupera mais do que a margem cobrada.

A prova esta na forma da curva. O ROI cai monotonicamente conforme a odd
sobe (regressao de ROI sobre log(odd): inclinacao -0,034, t=-3,65):

    odd < 1.20   n=2227   ROI +2,42%   t=+3,27   IC95% [+0,94%, +3,85%]
    odd < 1.25   n=4366   ROI +1,51%   t=+2,47   IC95% [+0,28%, +2,68%]
    odd < 1.30   n=7079   ROI +1,62%   t=+3,06   IC95% [+0,58%, +2,62%]
    odd 2.00-3.00 n=120222 ROI -3,22%  t=-10,18
    odd > 6.00   n=31452  ROI -9,24%   t=-6,85

O que este modulo NAO promete
-----------------------------
- Nao e previsao de futebol. E exploracao de um vies de precificacao.
- O ROI e pequeno (+1,6%) e a variancia e grande. 9 de 27 anos fecharam
  negativos. Um ano ruim e normal, nao sinal de que quebrou.
- Exige conta em VARIAS casas: a vantagem esta em pegar a melhor odd, e
  com uma casa so o ROI vira negativo.
- A vantagem morre se todo mundo fizer o mesmo.

Numeros praticos (odd < 1,30): ~262 apostas/ano, +1,62% de ROI. Com stake
de 1% da banca isso da ~+4,2% de banca por ano. Nao e um negocio: e uma
vantagem real e modesta.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .backtest_data import MatchResult, settle_outcome
from .odds_normalize import group_by_event, group_by_market
from .odds_provider import OddsFetchRequest, divisions_for
from .odds_registry import default_odds_registry
from .providers import ProviderError
from .strategy import StrategyEvidence

#: Identificacao da estrategia no StrategyRegistry (FASE A).
STRATEGY_NAME = "value_short_favourites"

#: Odd maxima da regra validada.
MAX_ODD = 1.30
#: Exige consenso de pelo menos N casas (evita preco de uma casa so).
MIN_BOOKS = 3
#: Abaixo disso o consenso e marcado como limitado, mesmo se `min_books`
#: for reduzido explicitamente. Com 2 casas nunca se inventa a terceira.
MIN_CONSENSUS_BOOKS = 3
#: Mercado com validacao estatistica. "Total de Gols" nao teve amostra.
MARKETS = ("Resultado Final (1X2)",)

# --------------------------------------------------------------------------
# Parametros VALIDADOS da regra (snapshot da ultima validacao historica)
# --------------------------------------------------------------------------
#
# Estes numeros pertencem a ESTRATEGIA, nao ao core de staking: sao a
# fotografia constante da medicao cuja forma viva e o
# `StrategyValidation` em cache (fingerprint I-14). O caminho de decisao
# consome-os via `ValueStrategy.evidence()` — nunca como conhecimento
# permanente de `staking`.

#: ROI por aposta, validado em 195.672 jogos (2000-2026).
EDGE_ROI = 0.0160
#: Erro-padrao do ROI (t = +2,97 em 6.748 apostas).
EDGE_SE = 0.0054
#: Odd media da regra (favoritos curtos).
EDGE_ODD = 1.21
#: Apostas por ano que a regra gera (38 competicoes).
BETS_PER_YEAR = 250

#: Faixas usadas para reportar o ROI historico por odd.
#: O ROI cai monotonicamente conforme a odd sobe — e essa a assinatura do
#: favourite-longshot bias.
ODD_BANDS: tuple[tuple[float, float], ...] = (
    (1.00, 1.15), (1.15, 1.20), (1.20, 1.25), (1.25, 1.30),
    (1.30, 1.40), (1.40, 1.60), (1.60, 2.00), (2.00, 3.00), (3.00, 99.0),
)

def _validation_cache_path() -> Path:
    """Caminho do cache de validacao (respeita BETGSN_OUTPUT_DIR)."""
    from .config import output_root

    return output_root() / "value_validation.json"

#: Versao do ESQUEMA do cache (nao do modelo): muda quando os campos
#: persistidos ou a semantica do fingerprint mudam. Caches sem essa
#: versao sao invalidados — nunca lidos como se fossem atuais.
#: v3 (Fase D): o fingerprint passa a incluir os parametros que
#: determinam materialmente os valores persistidos (bootstrap e faixas
#: de odd); caches do schema v2 sao invalidados uma unica vez.
CACHE_SCHEMA_VERSION = 3

BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 424242


def validation_cache_fingerprint(
    *,
    max_odd: float,
    min_books: int,
    markets: Sequence[str] = MARKETS,
    closing: bool = False,
    corpus_signature: str = "",
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    odd_bands: Sequence[tuple[float, float]] = ODD_BANDS,
) -> str:
    """Fingerprint deterministico do que a validacao mede (I-14).

    O cache so pode ser reaproveitado quando TUDO o que define a
    medicao e o mesmo:

    - `max_odd`/`min_books`: parametros da regra;
    - `markets`: mercados validados;
    - `closing`: odds de abertura (False) ou fechamento (True);
    - `corpus_signature`: assinatura do corpus (arquivos/mtime via
      `FootballDataClient.corpus_signature`) — muda quando um CSV novo
      chega ou um existente e atualizado;
    - `bootstrap_resamples`/`bootstrap_seed`: determinam os ICs
      persistidos (`ci_low`/`ci_high`) — outro bootstrap e OUTRA
      medicao (Fase D);
    - `odd_bands`: as faixas de `by_band`/`band_counts` persistidas —
      mudar os limites muda o conteudo do cache (Fase D).

    Sem o corpus na chave, uma validacao feita sobre 2000-2025 seria
    servida como se valesse para um corpus que ganhou a temporada 2026:
    cache misturando corpora. Sem os mercados/fechamento, mudancas de
    configuracao silenciosas reutilizariam numeros de outra medicao.
    Sem o bootstrap/faixas, editar esses parametros serviria ICs e
    bandas de outra configuracao como se fossem atuais.
    """
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "max_odd": float(max_odd),
        "min_books": int(min_books),
        "markets": list(markets),
        "closing": bool(closing),
        "corpus": corpus_signature,
        "bootstrap_resamples": int(bootstrap_resamples),
        "bootstrap_seed": int(bootstrap_seed),
        "odd_bands": [[float(lo), float(hi)] for lo, hi in odd_bands],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


@dataclass
class StrategyValidation:
    """Resultado da validacao historica da regra."""

    max_odd: float
    min_books: int
    n_bets: int
    roi: float
    se: float
    t: float
    ci_low: float
    ci_high: float
    avg_odd: float
    positive_years: int
    total_years: int
    positive_leagues: int
    total_leagues: int
    by_year: dict[str, float] = field(default_factory=dict)
    by_league: dict[str, float] = field(default_factory=dict)
    #: ROI por faixa de odd (a prova visual do bias)
    by_band: dict[str, float] = field(default_factory=dict)
    band_counts: dict[str, int] = field(default_factory=dict)
    generated_at: str = ""
    #: Fingerprint do cache (I-14): identifica corpus + parametros da
    #: medicao. Vazio em resultados antigos — cache assim e invalido.
    cache_fingerprint: str = ""

    @property
    def significant(self) -> bool:
        """True quando o IC95% nao cruza zero."""
        return self.ci_low > 0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "StrategyValidation":
        return cls(**payload)


# --------------------------------------------------------------------------
# Validacao historica
# --------------------------------------------------------------------------


def _stats(rets: Sequence[float]) -> tuple[float, float, float]:
    """(ROI, erro-padrao, t)."""
    n = len(rets)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(var / n) if n > 1 else 0.0
    return mean, se, (mean / se if se > 0 else 0.0)


def _bootstrap_ci(rets: Sequence[float]) -> tuple[float, float]:
    """IC 95% por bootstrap percentil, com seed fixa."""
    if len(rets) < 2:
        return 0.0, 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(rets)
    means = sorted(
        sum(rng.choices(rets, k=n)) / n for _ in range(BOOTSTRAP_RESAMPLES)
    )
    lo = means[int(0.025 * BOOTSTRAP_RESAMPLES)]
    hi = means[int(0.975 * BOOTSTRAP_RESAMPLES)]
    return lo, hi


def collect_bets(
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
    markets: Sequence[str] = MARKETS,
    closing: bool = False,
    progress: Any = None,
) -> list[dict]:
    """Percorre o cache do football-data.co.uk e devolve as apostas da regra.

    Cada aposta e um dicionario com data, liga, odd e retorno (stake 1).
    `closing=False` usa odds de ABERTURA (o preco que existia quando a
    rodada foi publicada); `closing=True` usa as de fechamento.
    """
    from .backtest_engine import csv_odds_store
    from .football_data_uk import FootballDataClient

    matches = FootballDataClient().load_matches()
    store = csv_odds_store(closing=closing)

    bets: list[dict] = []
    for i, m in enumerate(matches, 1):
        odds = store.odds_for(m)
        if not odds:
            continue
        result = MatchResult.from_match(m)
        for market, books in odds.items():
            if market not in markets:
                continue
            outcomes: set[str] = set()
            for b in books.values():
                outcomes.update(b)
            for oc in outcomes:
                vals = [b[oc] for b in books.values() if b.get(oc)]
                if len(vals) < min_books:
                    continue
                best = max(vals)
                if best >= max_odd:
                    continue
                res = settle_outcome(market, oc, result)
                if res is None:
                    continue
                bets.append({
                    "d": m.kickoff[:10],
                    "lg": m.league,
                    "mkt": market,
                    "odd": best,
                    "ret": (best - 1.0) if res == "win" else (0.0 if res == "push" else -1.0),
                })
        if progress is not None and i % 40000 == 0:
            progress(i, len(matches))
    return bets


def validate(
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
    use_cache: bool = True,
    closing: bool = False,
    progress: Any = None,
) -> StrategyValidation:
    """Valida a regra sobre o cache historico inteiro.

    O cache (I-14) so e reaproveitado quando o fingerprint da medicao —
    parametros da regra + mercados + tipo de odd + assinatura do corpus
    — bate com o da validacao pedida. Qualquer mudanca relevante e cache
    miss: a validacao roda de novo em vez de servir numeros de outro
    corpus/configuracao.
    """
    from .football_data_uk import FootballDataClient

    corpus = FootballDataClient().corpus_signature()
    fingerprint = validation_cache_fingerprint(
        max_odd=max_odd, min_books=min_books, markets=MARKETS,
        closing=closing, corpus_signature=corpus,
        bootstrap_resamples=BOOTSTRAP_RESAMPLES,
        bootstrap_seed=BOOTSTRAP_SEED, odd_bands=ODD_BANDS,
    )
    cache_path = _validation_cache_path()
    if use_cache and cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if payload.get("cache_fingerprint") == fingerprint:
                return StrategyValidation.from_json(payload)
        except (json.JSONDecodeError, TypeError):
            pass

    bets = collect_bets(99.0, min_books, closing=closing, progress=progress)
    rets = [b["ret"] for b in bets if b["odd"] < max_odd]
    roi, se, t = _stats(rets)
    lo, hi = _bootstrap_ci(rets)

    by_year: dict[str, list[float]] = {}
    by_league: dict[str, list[float]] = {}
    for b in bets:
        if b["odd"] >= max_odd:
            continue
        by_year.setdefault(b["d"][:4], []).append(b["ret"])
        by_league.setdefault(b["lg"], []).append(b["ret"])

    # ROI por faixa — cobre TODAS as odds, nao so a regra
    band_roi: dict[str, float] = {}
    band_counts: dict[str, int] = {}
    for blo, bhi in ODD_BANDS:
        name = f"{blo:.2f}-{bhi:.2f}" if bhi < 90 else f">= {blo:.2f}"
        band = [b["ret"] for b in bets if blo <= b["odd"] < bhi]
        band_counts[name] = len(band)
        if band:
            band_roi[name] = sum(band) / len(band)

    year_roi = {y: sum(v) / len(v) for y, v in by_year.items()}
    league_roi = {
        lg: sum(v) / len(v)
        for lg, v in by_league.items()
        if len(v) >= 60
    }

    result = StrategyValidation(
        max_odd=max_odd,
        min_books=min_books,
        n_bets=len(rets),
        roi=roi,
        se=se,
        t=t,
        ci_low=lo,
        ci_high=hi,
        avg_odd=sum(b["odd"] for b in bets if b["odd"] < max_odd) / len(rets) if rets else 0.0,
        positive_years=sum(1 for v in year_roi.values() if v > 0),
        total_years=len(year_roi),
        positive_leagues=sum(1 for v in league_roi.values() if v > 0),
        total_leagues=len(league_roi),
        by_year={y: round(v, 6) for y, v in sorted(year_roi.items())},
        by_league={k: round(v, 6) for k, v in sorted(league_roi.items())},
        by_band={k: round(v, 6) for k, v in band_roi.items()},
        band_counts=band_counts,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        cache_fingerprint=fingerprint,
    )
    try:
        cache_path = _validation_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(result.to_json(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass
    return result


def cached_validation(
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
    closing: bool = False,
) -> StrategyValidation | None:
    """Cache de validacao LEGIVEL para a regra pedida, ou None.

    Diferente de `validate`, nunca roda a validacao (percorrer 195 mil
    partidas nao e lugar para um request HTTP). Tambem diferente de ler
    o JSON cru: o cache so e devolvido quando o fingerprint bate com a
    regra ATUAL sobre o corpus ATUAL — um cache de outro corpus ou de
    outros parametros nao e evidencia desta regra e volta como None.
    """
    from .football_data_uk import FootballDataClient

    try:
        cache_path = _validation_cache_path()
        if not cache_path.exists():
            return None
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        val = StrategyValidation.from_json(payload)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    expected = validation_cache_fingerprint(
        max_odd=max_odd, min_books=min_books, markets=MARKETS,
        closing=closing,
        corpus_signature=FootballDataClient().corpus_signature(),
        bootstrap_resamples=BOOTSTRAP_RESAMPLES,
        bootstrap_seed=BOOTSTRAP_SEED, odd_bands=ODD_BANDS,
    )
    if val.cache_fingerprint != expected:
        return None
    return val


# --------------------------------------------------------------------------
# Scanner ao vivo
# --------------------------------------------------------------------------


@dataclass
class Opportunity:
    """Um favorito curto com preco melhor que o consenso."""

    match: str
    commence_time: str
    outcome: str
    best_odd: float
    best_book: str
    fair_odd: float
    median_odd: float
    n_books: int
    edge: float          # best_odd / fair_odd - 1
    is_home: bool
    #: Numero de casas que sustentam a linha (o mesmo que n_books, explicito).
    book_count: int = 0
    #: True quando o consenso tem menos casas que o minimo confiavel.
    consensus_limited: bool = False

    @property
    def label(self) -> str:
        """Rotulo em ASCII puro: o console do Windows usa cp1252."""
        return f"{self.match} -> {self.outcome} @ {self.best_odd:.2f} ({self.best_book})"


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _opportunities_from_books(
    books: dict[str, dict[str, dict[str, float]]],
    home: str,
    away: str,
    commence_time: str,
    max_odd: float,
    min_books: int,
    markets: Sequence[str],
) -> list[Opportunity]:
    """Favoritos curtos num evento ja no formato interno do BETGSN.

    `books` e `{mercado: {casa: {resultado: odd}}}` — o MESMO formato que
    `providers.odds_event_to_internal` produzia e que `group_by_market`
    produz das quotes normalizadas. Um unico algoritmo para os dois
    caminhos (legado e contrato): nao existe uma segunda versao da
    regra divergindo com o tempo.
    """
    out: list[Opportunity] = []
    for market, by_book in books.items():
        if market not in markets:
            continue

        outcomes: set[str] = set()
        for b in by_book.values():
            outcomes.update(b)

        # mediana por resultado (consenso robusto a uma casa atrasada)
        medians: dict[str, float] = {}
        counts: dict[str, int] = {}
        for oc in outcomes:
            vals = [b[oc] for b in by_book.values() if b.get(oc)]
            if not vals:
                continue
            medians[oc] = _median(vals)
            counts[oc] = len(vals)

        # remove o vig do consenso: normaliza as implicitas das medianas.
        # Sem isso a "odd justa" carregaria a margem da casa e toda
        # comparacao sairia artificialmente favoravel.
        implied = {oc: 1.0 / od for oc, od in medians.items() if od > 0}
        total = sum(implied.values()) or 1.0
        fair = {oc: total / p for oc, p in implied.items() if p > 0}

        for oc, med in medians.items():
            if counts[oc] < min_books:
                continue
            vals = [b[oc] for b in by_book.values() if b.get(oc)]
            best = max(vals)
            if best >= max_odd:
                continue
            best_book = next(b for b, m in by_book.items() if m.get(oc) == best)
            fair_odd = fair.get(oc, med)
            out.append(Opportunity(
                match=f"{home} vs {away}",
                commence_time=commence_time,
                outcome=oc,
                best_odd=best,
                best_book=best_book,
                fair_odd=fair_odd,
                median_odd=med,
                n_books=counts[oc],
                edge=(best / fair_odd - 1.0) if fair_odd > 0 else 0.0,
                is_home=(oc == "1"),
                book_count=counts[oc],
                consensus_limited=counts[oc] < MIN_CONSENSUS_BOOKS,
            ))
    return out


def scan_events(
    events: Sequence[dict],
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
    markets: Sequence[str] = MARKETS,
) -> list[Opportunity]:
    """Encontra favoritos curtos nos eventos ao vivo de uma esportes.

    Reutiliza `providers.odds_event_to_internal` — o mesmo parser do
    Scanner — para nao existir uma segunda conversao divergindo.
    Caminho LEGADO: eventos crus que ja existem em maos (o scanner ao
    vivo, `scan_live`, hoje consome quotes do contrato de providers).
    """
    from .providers import odds_event_to_internal

    out: list[Opportunity] = []
    for event in events:
        books = odds_event_to_internal(event)
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence = event.get("commence_time", "")
        out.extend(_opportunities_from_books(
            books, home, away, commence, max_odd, min_books, markets,
        ))
    # mais curto primeiro: o ROI historico e melhor nas odds menores
    out.sort(key=lambda o: o.best_odd)
    return out


def _utcnow() -> str:
    """Instante da observacao em chave canonica UTC (timeutil.UTC_FORMAT)."""
    from .timeutil import now_utc

    return now_utc()


def _opportunities_from_quotes(
    quotes: Sequence,
    max_odd: float,
    min_books: int,
    markets: Sequence[str] = MARKETS,
) -> list[Opportunity]:
    """Opportunities a partir de quotes JA normalizadas, por evento.

    Nao parseia payload cru e nao fabrica jogo: mandante, visitante e
    kickoff vem da propria quote (o adapter ja garantiu que existe).
    """
    out: list[Opportunity] = []
    for _event_id, event_quotes in group_by_event(quotes).items():
        first = event_quotes[0]
        books = group_by_market(event_quotes)
        out.extend(_opportunities_from_books(
            books, first.home_team, first.away_team, first.kickoff,
            max_odd, min_books, markets,
        ))
    return out


def scan_live(
    sport_keys: Sequence[str],
    max_odd: float = MAX_ODD,
    min_books: int = MIN_BOOKS,
) -> tuple[list[Opportunity], dict[str, Any]]:
    """Busca oportunidades nas odds ao vivo (providers do registry, FASE B).

    Cada esporte e atendido pelo primeiro provider CONFIGURADO que devolve
    odds utilizaveis — o mesmo fallback do OddsService, sem health tracker
    (o scanner e uma leitura pontual, nao uma coleta persistida). Provider
    que falha ou nao cobre o esporte registra e segue para o proximo; sem
    NENHUM provider configurado, o erro explicito de sempre.
    """
    providers = default_odds_registry().available_providers()
    if not providers:
        raise ProviderError(
            "BETGSN_ODDS_API_KEY nao configurada. O scanner ao vivo precisa "
            "da chave (plano gratuito serve). Configure no .env."
        )

    found: list[Opportunity] = []
    meta: dict[str, Any] = {"sports": {}, "credits_remaining": None}
    for sport in sport_keys:
        fetched_at = _utcnow()
        sport_error: str | None = None
        served_events = 0
        sport_ops: list[Opportunity] = []
        for _name, provider in providers:
            request = OddsFetchRequest(
                divisions=divisions_for(provider, sport),
                markets=MARKETS,
                fetched_at=fetched_at,
            )
            try:
                fetched = provider.fetch_odds(request)
            except ProviderError as exc:
                sport_error = str(exc)
                continue
            # saldo do contrato quando informado; None permanece None
            if fetched.credits is not None and fetched.credits.remaining is not None:
                meta["credits_remaining"] = fetched.credits.remaining
            if fetched.no_coverage:
                continue
            quotes = [q for q in fetched.quotes if q.usable]
            if not quotes:
                continue
            sport_ops = _opportunities_from_quotes(quotes, max_odd, min_books)
            served_events = len({q.event_id for q in quotes})
            found.extend(sport_ops)
            break

        if sport_ops or served_events:
            meta["sports"][sport] = {
                "events": served_events,
                "opportunities": len(sport_ops),
            }
        elif sport_error is not None:
            meta["sports"][sport] = {"error": sport_error}
        else:
            # respondeu sem cobertura utilizavel: ausencia, nao doenca
            meta["sports"][sport] = {"events": 0, "opportunities": 0}

    found.sort(key=lambda o: o.best_odd)
    return found, meta


# --------------------------------------------------------------------------
# Contrato Strategy (FASE A)
# --------------------------------------------------------------------------


class ValueStrategy:
    """A regra de favoritos curtos como estrategia do contrato `Strategy`.

    Esta classe NAO decide nada e NAO produz `BetDecision`: expoe a
    identificacao e a EVIDENCIA validada da regra para o runner
    (`strategy_runner.run_strategy_decision`), que leva ao decision gate
    (`staking.decide_bet`, o unico produtor). A decisao continua tendo
    um caminho so.
    """

    name = STRATEGY_NAME
    markets = MARKETS
    description = (
        "Favoritos curtos (odd < 1,30) com a melhor odd entre casas: "
        "exploracao do favourite-longshot validada em 195.672 jogos."
    )

    def evidence(self) -> StrategyEvidence:
        """Parametros validados da regra para o decision gate.

        Vantagem medida: usa a validacao em cache QUANDO o fingerprint
        bate com a regra atual sobre o corpus atual (I-14); sem cache
        valido, as constantes validadas deste modulo. Nao roda a
        validacao aqui: percorrer 195 mil partidas num request HTTP
        nao e lugar para isso. Um cache de outro corpus ou de outros
        parametros NAO e evidencia desta regra: volta para as
        constantes, nunca para numeros de outra medicao.
        """
        val = cached_validation()
        if val is not None:
            return StrategyEvidence(
                roi=val.roi, roi_se=val.se,
                odd=val.avg_odd or EDGE_ODD, n_bets=val.n_bets,
            )
        return StrategyEvidence(roi=EDGE_ROI, roi_se=EDGE_SE, odd=EDGE_ODD)


#: Instancia da estrategia (imutavel e sem estado — uma so por processo).
value_strategy = ValueStrategy()
