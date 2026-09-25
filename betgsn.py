"""BETGSN — launcher.

Uso:
    python betgsn.py              abre a interface desktop (legada, Tkinter)
    python betgsn.py --api        sobe a API HTTP/WebSocket para a interface React
    python betgsn.py --cli        roda o pipeline e imprime os sinais no console
    python betgsn.py --selftest   valida o pipeline e sai com codigo 0/1
    python betgsn.py --providers  mostra quais fontes reais tem chave
    python betgsn.py --backtest   backtest legado (split unico, resumo no console)
    python betgsn.py --walkforward  backtest rolling point-in-time (motor novo)
    python betgsn.py --import-odds      baixa odds historicas reais (The Odds API)
    python betgsn.py --import-fixtures  baixa temporadas reais (API-Football)
    python betgsn.py --import-fduk      baixa ligas europeias + odds reais (CSV publico)
    python betgsn.py --import-fixtures-live  baixa os JOGOS FUTUROS com odds reais
    python betgsn.py --capture-odds     captura odds ao vivo (vira historico)
    python betgsn.py --sources          estado dos caches de dados historicos

    python betgsn.py --validate-strategy  valida a vantagem (favoritos curtos)
    python betgsn.py --scan-value         procura favoritos curtos ao vivo
    python betgsn.py --staking-plan       alavancagem segura e risco de ruina
"""

from __future__ import annotations

import re
import sys


def run_cli(bankroll: float = 1000.0, min_ev: float = 0.02) -> int:
    from betgsn.pipeline import run
    res = run(bankroll=bankroll, min_ev=min_ev)
    print("=" * 78)
    print("BETGSN — SINAIS")
    print("=" * 78)
    print(f"Jogos analisados: {len(res.analyses)}   "
          f"Sinais: {len(res.report.signals) if res.report else 0}   "
          f"Banca: {bankroll:.2f}")
    if res.report:
        print(f"Exposicao: {res.report.total_exposure():.2f} "
              f"({res.report.total_exposure_pct * 100:.2f}% da banca) | "
              f"Lucro esperado: {res.report.expected_profit:+.2f} "
              f"({res.report.expected_profit_pct * 100:+.2f}% da banca) | "
              f"Perda maxima: {res.report.worst_case_loss:.2f}")
    print("-" * 78)
    for line in res.tips:
        print(line)
    print("-" * 78)
    print("Top placares provaveis por jogo:")
    for a in res.analyses[:5]:
        top = a.top_scorelines[0]
        print(f"  {a.fixture.home} vs {a.fixture.away} ({a.fixture.kickoff}) "
              f"-> {top[0]}-{top[1]} ({top[2] * 100:.1f}%)  "
              f"gols esp. {a.lambdas[0]:.2f}/{a.lambdas[1]:.2f}")
    return 0


def selftest() -> int:
    """Valida o pipeline ponta a ponta sem interface. Retorna 0 se tudo passa."""
    import math
    from betgsn.engine import (Leg, analyse_multiple, evaluate_market, fair_probs,
                               implied_prob, kelly_fraction, overround, scan_arbitrage,
                               stake_plan)
    from betgsn.model import build_score_matrix
    from betgsn.pipeline import run

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    # 1) conversoes
    check("implied_prob(2.0) == 0.5", abs(implied_prob(2.0) - 0.5) < 1e-9)
    check("overround de 1X2 > 1", overround([2.1, 3.4, 3.6]) > 1.0,
          f"{overround([2.1, 3.4, 3.6]):.4f}")
    check("fair_probs soma 1", abs(sum(fair_probs([2.1, 3.4, 3.6])) - 1.0) < 1e-9)

    # 2) Kelly
    check("kelly sem edge == 0", kelly_fraction(0.5, 2.0) == 0.0)
    check("kelly com edge > 0", kelly_fraction(0.6, 2.0) > 0.0,
          f"{kelly_fraction(0.6, 2.0):.4f}")
    plan = stake_plan(1000.0, 0.60, 2.0, fraction=0.25, cap=0.01)
    check("stake progressiva respeita 1% da banca", plan.stake == 10.0,
          f"stake={plan.stake:.2f} pct={plan.recommended_pct * 100:.2f}%")
    plan_bigger = stake_plan(2500.0, 0.60, 2.0, fraction=0.25, cap=0.01)
    check("stake cresce quando a banca cresce", plan_bigger.stake == 25.0,
          f"1000->{plan.stake:.2f}; 2500->{plan_bigger.stake:.2f}")

    # 3) matriz de placar
    m = build_score_matrix(1.6, 1.1)
    s = m.prob_home_win() + m.prob_draw() + m.prob_away_win()
    check("1X2 soma 1 na matriz", abs(s - 1.0) < 1e-6, f"{s:.6f}")
    check("over 2.5 + under 2.5 == 1",
          abs(m.prob_over(2.5) + m.prob_under(2.5) - 1.0) < 1e-9)
    check("time mais forte vence mais", m.prob_home_win() > m.prob_away_win())

    # 4) arbitragem
    books = {"A": {"1": 2.20, "X": 3.30, "2": 3.20},
             "B": {"1": 1.95, "X": 3.60, "2": 3.55},
             "C": {"1": 2.05, "X": 3.10, "2": 4.10}}
    arb = scan_arbitrage(books, 1000.0)
    check("arb detectada em mercado com edge", arb.arbitrage,
          f"margem {arb.margin:.4f}")
    if arb.arbitrage:
        check("stakes de arb somam o total",
              abs(sum(l.stake for l in arb.legs) - 1000.0) < 1.0)
        check("retornos de arb sao iguais",
              max(l.payout for l in arb.legs) - min(l.payout for l in arb.legs) < 1.0)

    # 5) avaliacao de mercado
    lines = evaluate_market(books, {"1": 0.50, "X": 0.27, "2": 0.23})
    check("evaluate_market devolve linhas", len(lines) == 3)
    check("melhor odd de '1' vem da casa A",
          next(l for l in lines if l.outcome == "1").best_book == "A")

    # 6) multipla
    mult = analyse_multiple([Leg("a", 1.8, 0.6), Leg("b", 1.9, 0.55)])
    check("multipla combina odds", abs(mult.combined_odd - 3.42) < 1e-6,
          f"{mult.combined_odd}")

    # 7) fit de ratings recupera a hierarquia
    res = run(bankroll=1000.0, min_ev=0.02)
    top_team = max(res.ratings.values(), key=lambda r: r.strength)
    check("melhor time do fit e forte", top_team.attack > 1.0,
          f"{top_team.name} att={top_team.attack:.2f}")
    check("pipeline gera analises", len(res.analyses) >= 5,
          f"{len(res.analyses)} jogos")
    check("pipeline gera sinais", res.report is not None and len(res.report.signals) >= 1,
          f"{len(res.report.signals) if res.report else 0} sinais")
    if res.report and res.report.signals:
        s0 = res.report.signals[0]
        check("sinal tem EV positivo", s0.ev > 0, f"{s0.ev:.4f}")
        check("sinal tem stake > 0", s0.stake > 0, f"{s0.stake:.2f}")
        check("sinal tem casa de aposta", bool(s0.best_book))

    # 8) odds de exemplo batem com o formato interno
    a0 = res.analyses[0]
    check("odds de exemplo tem mercados", len(a0.odds) >= 3, f"{list(a0.odds)}")
    check("odds de exemplo tem casas", len(next(iter(a0.odds.values()))) >= 5)
    check("stake cap do pipeline e aplicado", max(s.stake_pct for s in res.report.signals) <= 0.01001)
    check("report calcula lucro esperado", res.report.expected_profit >= 0.0)

    passed = sum(1 for _, ok, _ in checks if ok)
    print("=" * 70)
    print(f"BETGSN SELFTEST — {passed}/{len(checks)} checks passaram")
    print("=" * 70)
    for name, ok, detail in checks:
        mark = "OK  " if ok else "FALHA"
        print(f"[{mark}] {name}" + (f"   ({detail})" if detail else ""))
    failed = [c for c in checks if not c[1]]
    if failed:
        print("-" * 70)
        print(f"{len(failed)} FALHA(S): " + ", ".join(c[0] for c in failed))
        return 1
    return 0


def show_providers() -> int:
    from betgsn.providers import available_providers
    print("Fontes de dados reais configuradas:")
    for name, ok in available_providers().items():
        print(f"  [{'X' if ok else ' '}] {name}")
    print("\nDefina as variaveis de ambiente para habilitar:")
    print("  BETGSN_ODDS_API_KEY        (the-odds-api.com)")
    print("  BETGSN_APIFOOTBALL_KEY     (api-football.com)")
    print("  BETGSN_FOOTBALLDATA_KEY    (football-data.org)")
    return 0


def run_backtest_cli() -> int:
    from betgsn.backtest import run_backtest
    from betgsn.data import build_dataset

    split = 0.7
    bankroll = 1000.0
    min_ev = 0.03
    for a in sys.argv[1:]:
        if a.startswith("--split="):
            split = float(a.split("=", 1)[1])
        elif a.startswith("--bankroll="):
            bankroll = float(a.split("=", 1)[1])
        elif a.startswith("--min-ev="):
            min_ev = float(a.split("=", 1)[1])

    ds = build_dataset()
    res = run_backtest(ds, split=split, bankroll=bankroll, min_ev=min_ev)
    print(res.summary)
    print("\nNota: dataset sintetico. O framework e real; os numeros validam o metodo,")
    print("nao o mercado real. Para odds reais, plugue os providers no pipeline.")
    return 0


def run_api() -> int:
    """Sobe a API que alimenta a interface React (FASE B da migracao)."""
    host = "127.0.0.1"
    port = 8787
    reload = "--reload" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--host="):
            host = a.split("=", 1)[1]
        elif a.startswith("--port="):
            port = int(a.split("=", 1)[1])
    try:
        from betgsn.api.server import serve
    except ModuleNotFoundError as exc:
        print(f"ERRO: dependencia da API ausente ({exc.name}).")
        print("Instale com: pip install -r requirements-api.txt")
        return 1
    print(f"BETGSN API em http://{host}:{port}  (docs em /docs)")
    serve(host=host, port=port, reload=reload)
    return 0


def _arg(args: list[str], name: str, default: str = "") -> str:
    prefix = f"--{name}="
    for a in args:
        if a.startswith(prefix):
            return a.split("=", 1)[1]
    return default


def _flag(args: list[str], name: str) -> bool:
    return f"--{name}" in args


def import_odds_cli(args: list[str]) -> int:
    """Importa odds historicas reais (The Odds API) para o cache local."""
    from betgsn.backtest_sources import HistoricalOddsImporter, OddsHistoryCache
    from betgsn.providers import OddsApiProvider, ProviderError

    provider = OddsApiProvider.from_env()
    if provider is None:
        print("ERRO: BETGSN_ODDS_API_KEY nao configurada.")
        print("A importacao de odds reais exige a chave. O BETGSN nao inventa odds.")
        print("  $env:BETGSN_ODDS_API_KEY = '...'   # the-odds-api.com")
        return 1

    sport = _arg(args, "sport", "soccer_brazil_campeonato")
    start = _arg(args, "start")
    end = _arg(args, "end")
    step = float(_arg(args, "step", "6"))
    max_requests = int(_arg(args, "max-requests", "0")) or None

    if not start or not end:
        print("ERRO: informe --start=YYYY-MM-DD e --end=YYYY-MM-DD.")
        print("Exemplo:")
        print("  python betgsn.py --import-odds --sport=soccer_brazil_campeonato \\")
        print("      --start=2024-04-01 --end=2024-06-30 --step=6")
        return 1

    cache = OddsHistoryCache()
    importer = HistoricalOddsImporter(provider, cache, sleep_seconds=0.2)

    def progress(done: int, total: int, iso: str) -> None:
        print(f"  [{done}/{total}] {iso}", end="\r")

    print(f"Importando odds de {sport} entre {start} e {end} (passo {step}h)...")
    try:
        report = importer.import_window(
            sport, start, end, step_hours=step,
            max_requests=max_requests, progress=progress,
        )
    except ProviderError as exc:
        print(f"\nERRO do provedor: {exc}")
        return 1
    except Exception as exc:
        print(f"\nERRO: {type(exc).__name__}: {exc}")
        return 1

    print()
    print(f"requisicoes : {report.requested}")
    print(f"importados  : {report.imported} snapshots")
    print(f"vazios      : {report.skipped}")
    print(f"falhas      : {report.failed}")
    for err in report.errors[:5]:
        print(f"  ! {err}")
    stats = cache.stats().get(
        re.sub(r"[^a-z0-9_]+", "_", sport.lower()), {}
    )
    if stats:
        print(f"cache       : {stats['snapshots']} snapshots "
              f"({stats['first']} -> {stats['last']})")
    print("\nRode o backtest com odds_source='real_historical' para usa-las.")
    return 0 if report.failed == 0 else 2


def import_fixtures_cli(args: list[str]) -> int:
    """Importa temporadas reais (API-Football) para o cache local."""
    from betgsn.backtest_sources import HistoricalFixtureImporter, OddsHistoryError
    from betgsn.providers import ApiFootballProvider

    provider = ApiFootballProvider.from_env()
    if provider is None:
        print("ERRO: BETGSN_APIFOOTBALL_KEY nao configurada.")
        print("  $env:BETGSN_APIFOOTBALL_KEY = '...'   # api-football.com")
        print("\nSem a chave o BETGSN continua com o dataset local sintetico.")
        return 1

    league = int(_arg(args, "league", "71"))
    season = int(_arg(args, "season", "2024"))
    with_stats = _flag(args, "with-stats")
    max_fixtures = int(_arg(args, "max-fixtures", "0")) or None

    importer = HistoricalFixtureImporter(provider, sleep_seconds=0.25)

    def progress(done: int, total: int, label: str) -> None:
        print(f"  [{done}/{total}] {label}"[:78].ljust(78), end="\r")

    print(f"Importando liga {league}, temporada {season}"
          f"{' (com estatisticas)' if with_stats else ''}...")
    try:
        report = importer.import_season(
            league, season, with_statistics=with_stats,
            max_fixtures=max_fixtures, progress=progress,
        )
    except OddsHistoryError as exc:
        print(f"\nERRO: {exc}")
        return 1

    print()
    print(f"partidas recebidas : {report.requested}")
    print(f"gravadas           : {report.imported}")
    print(f"descartadas        : {report.skipped} (nao finalizadas / sem placar)")
    if report.errors:
        print(f"avisos             : {len(report.errors)}")
    print(f"arquivo            : {report.detail.get('path')}")
    print("\nRode o backtest com corpus_source='imported' para usar essas partidas.")
    return 0


def capture_odds_cli(args: list[str]) -> int:
    """Captura odds ao vivo e guarda como snapshot point-in-time.

    Odds historicas exigem plano pago. Mas capturar AGORA as odds dos jogos
    futuros cria historico real de graca: quando a partida acontecer, este
    snapshot passa a ser odds historica e o backtest podera usa-la.

    Agende para rodar 1x/dia (ex.: Agendador de Tarefas do Windows).
    """
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache
    from betgsn.odds_registry import default_odds_registry
    from betgsn.odds_snapshots import OddsSnapshotStore
    from betgsn.providers import sport_keys_for_divisions

    # providers de odds via registry (FASE B): apenas os OPERACIONAIS
    # (The Odds API, ParlayAPI, OddsPapi). Providers LEGACY (Odds-API.io,
    # OpticOdds) ficam fora do capture — nao gastam requests nem creditos
    # e nao contaminam o store operacional. Para incluir um legacy numa
    # validacao pontual, use --providers=<nome> explicitamente.
    registry = default_odds_registry()
    providers = registry.operational_providers()
    if not providers:
        print("ERRO: nenhum provider operacional configurado.")
        print("Configure BETGSN_ODDS_API_KEY / BETGSN_PARLAY_API_KEY / "
              "BETGSN_ODDSPAPI_API_KEY no .env para capturar odds.")
        return 1

    # Filtro de providers (--providers=ParlayAPI[,OddsPapi...]): captura
    # controlada — validar um provider novo SEM gastar a quota dos demais.
    # Nome fora do registry e erro explicito com a lista dos disponiveis,
    # nunca silencio (o provider "quebrado" pode ser so um typo).
    raw_providers = _arg(args, "providers", "")
    if raw_providers:
        wanted = [p.strip() for p in raw_providers.split(",") if p.strip()]
        # Selecao EXPLICITA pode incluir um provider LEGACY (validacao
        # pontual): a lista de nomes validos e a de TODOS os configurados,
        # nao so os operacionais. Sem --providers, o default operacional
        # ja exclui os legacy.
        all_configured = registry.available_providers()
        available_names = [name for name, _p in all_configured]
        unknown = [p for p in wanted if p not in available_names]
        if unknown:
            print(f"ERRO: providers desconhecidos: {', '.join(unknown)}")
            print(f"  disponiveis: {', '.join(available_names)}")
            return 1
        legacy_selected = [p for p in wanted if p in registry.legacy_names()]
        if legacy_selected:
            print(f"  AVISO: providers LEGACY selecionados explicitamente: "
                  f"{', '.join(legacy_selected)}")
        providers = [(n, p) for n, p in all_configured if n in wanted]
        print(f"  providers selecionados: {', '.join(n for n, _ in providers)}")

    # Scheduler consciente de quota: nao martela provider EXHAUSTED/RATE_LIMITED
    # /AUTH_ERROR. O health e PERSISTIDO no store, entao a decisao sobrevive
    # entre execucoes (CLI e engine compartilham o mesmo banco). `--force-capture`
    # ignora o cooldown explicitamente (validacao manual consciente).
    from betgsn.odds_snapshots import OddsSnapshotStore as _Store
    from betgsn.quota_scheduler import QuotaScheduler

    store_for_quota = _Store()
    scheduler = QuotaScheduler.from_store(store_for_quota)
    if not _flag(args, "force-capture"):
        attemptable, blocked = scheduler.filter_attemptable(providers)
        if blocked:
            for name, quota in sorted(blocked.items()):
                print(f"  provider bloqueado: {name} [{quota.state}] "
                      f"proxima tentativa {quota.next_attempt_at or 'n/d'}")
        if not attemptable:
            print()
            print("WAITING_FOR_PROVIDER_QUOTA: todos os providers operacionais "
                  "estao em cooldown/exhausted.")
            print("  Nenhuma chamada sera gasta. O store acumulado permanece "
                  "disponivel para replay/analise.")
            return 0
        providers = attemptable

    raw = _arg(args, "sports", "")
    # Os fixtures sao SEMPRE carregados: alem de derivarem os sports keys
    # quando --sports nao vem, eles alimentam a resolucao de identidade
    # (FixtureMatchIndex) — sem eles, eventos com nomes divergentes dos
    # nomes FDUK seriam gravados sob a chave do PROVIDER e a leitura
    # (movement/CLV usam a event_key DO FIXTURE) nunca os encontraria.
    from betgsn.football_data_uk import FootballDataClient

    fixtures = FootballDataClient().load_fixtures()
    if raw:
        sports = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        # Sem --sports, a captura cobre as MESMAS divisoes que o fixture
        # layer conhece (as partidas que a API consome em
        # movement/CLV/coverage). Divisao sem sport key verificada e
        # reportada, nunca inventada (ver DIVISION_TO_SPORT_KEY).
        divisions = sorted(
            {fx.division for fx in fixtures if fx.has_odds and fx.has_kickoff}
        )
        sports, unmapped = sport_keys_for_divisions(divisions)
        if unmapped:
            print(f"  divisoes sem sport key no provider (nao capturadas):")
            for div in unmapped:
                print(f"    - {div}")
        if not sports:
            print("ERRO: nenhuma divisao dos fixtures tem sport key mapeada.")
            print("Rode `python betgsn.py --import-fixtures-live` ou informe --sports.")
            return 1
        print(f"  sports derivados dos fixtures: {', '.join(sports)}")

    regions = _arg(args, "regions", "eu")
    markets = _arg(args, "markets", "h2h,totals,btts")

    # Resolucao de identidade (I-01): fixtures + aliases versionados. O
    # evento do provider casado com um fixture e gravado sob a event_key DO
    # FIXTURE — a mesma chave que a API consulta em movement/CLV/coverage.
    from betgsn.team_aliases import load_team_aliases

    aliases = load_team_aliases()

    cache = OddsHistoryCache()
    # Store canonico das observacoes por linha: e a fonte que a API consome
    # em movement/CLV/coverage. Sem injeta-lo aqui, a captura produziria
    # historico para o backtest mas a operacao leria um banco vazio.
    capture = LiveOddsCapture(
        [p for _name, p in providers], cache, regions=regions, markets=markets,
        store=OddsSnapshotStore(),
        fixtures=fixtures or None,
        aliases=aliases,
    )

    print(f"Capturando odds de {len(sports)} esporte(s)...")
    print(f"  regioes: {regions}  mercados: {markets}")
    report = capture.capture(sports)

    print()
    print(f"  momento da captura : {report.captured_at}")
    print(f"  snapshots gravados : {report.snapshots_saved}")
    print(f"  observacoes (API)  : {report.observations_saved}")
    print(f"  eventos vistos     : {report.events} ({report.events_with_odds} com odds)")
    print(f"  eventos casados    : {report.events_matched} "
          f"(gravados sob a event_key do fixture)")
    if report.events_unmatched:
        print(f"  eventos sem fixture: {report.events_unmatched} "
              f"(preservados sob a chave do provider)")
    if report.events_ambiguous:
        print(f"  eventos ambiguos   : {report.events_ambiguous} (NAO casados)")
    if report.credits_last is not None:
        print(f"  custo desta chamada: {report.credits_last} creditos")
    if report.credits_remaining is not None:
        print(f"  creditos restantes : {report.credits_remaining}")
    for err in report.errors:
        print(f"  ! {err}")

    print()
    print("Esses snapshots viram odds HISTORICAS quando as partidas acontecerem.")
    print("Rode novamente amanha (ou agende diariamente) para acumular serie.")
    return 0 if not report.errors else 2


def import_fduk_cli(args: list[str]) -> int:
    """Baixa os CSVs do football-data.co.uk (ligas europeias + extras).

    Fonte publica, sem chave de API. Traz resultados, estatisticas de
    partida e ODDS REAIS (Pinnacle, Betfair Exchange, Bet365, ...) com
    linha de abertura e fechamento.

    O download respeita rate limit e usa cache: rodar de novo so baixa o
    que falta.
    """
    from betgsn.football_data_uk import (
        EXTRA_LEAGUES,
        MAIN_DIVISIONS,
        FootballDataClient,
        season_codes,
    )

    raw_seasons = _arg(args, "seasons", "2012-2025")
    divisions_arg = _arg(args, "divisions", "")
    extras_arg = _arg(args, "extras", "")

    if "-" in raw_seasons:
        a, b = raw_seasons.split("-", 1)
        seasons = season_codes(int(a), int(b))
    else:
        seasons = [s.strip() for s in raw_seasons.split(",") if s.strip()]

    divisions = (
        [d.strip().upper() for d in divisions_arg.split(",") if d.strip()]
        if divisions_arg
        else list(MAIN_DIVISIONS)
    )
    extras = (
        [e.strip().upper() for e in extras_arg.split(",") if e.strip()]
        if extras_arg
        else list(EXTRA_LEAGUES)
    )

    invalid = [d for d in divisions if d not in MAIN_DIVISIONS]
    if invalid:
        print(f"ERRO: divisoes desconhecidas: {invalid}")
        print(f"validas: {sorted(MAIN_DIVISIONS)}")
        return 1

    total = len(divisions) * len(seasons) + len(extras)
    print("=" * 74)
    print("BETGSN — IMPORTACAO football-data.co.uk")
    print("=" * 74)
    print(f"\ntemporadas : {seasons[0]}..{seasons[-1]} ({len(seasons)})")
    print(f"divisoes   : {len(divisions)} ({', '.join(divisions[:8])}"
          f"{', ...' if len(divisions) > 8 else ''})")
    print(f"extras     : {len(extras)} ({', '.join(extras[:8])}"
          f"{', ...' if len(extras) > 8 else ''})")
    print(f"total      : {total} arquivos")
    print("\nbaixando (rate limit 0.6s; o que ja esta em cache e pulado)...\n")

    client = FootballDataClient()

    def progress(done: int, total_n: int, label: str) -> None:
        pct = done / total_n * 100 if total_n else 0
        print(f"  [{done:4d}/{total_n}] {pct:5.1f}%  {label}"[:78].ljust(78), end="\r")

    report = client.fetch_all(
        seasons=seasons, divisions=divisions, extras=extras, progress=progress
    )

    print()
    print(f"\n  baixados  : {report.downloaded}")
    print(f"  em cache  : {report.cached}")
    print(f"  falhas    : {report.failed}")
    print(f"  total     : {report.bytes_total / 1024 / 1024:.1f} MB")
    for err in report.errors[:5]:
        print(f"  ! {err}")

    inv = client.inventory()
    print(f"\ninventario: {inv['main_files']} principais + {inv['extra_files']} extras "
          f"= {inv['total_mb']} MB")
    print(f"divisoes com dados: {', '.join(inv['divisions'])}")

    # resumo de odds reais disponiveis
    from betgsn.backtest_engine import invalidate_csv_odds_store, csv_odds_store

    invalidate_csv_odds_store()
    print("\nindexando odds reais...")
    store = csv_odds_store(closing=True)
    print(f"  partidas com odds de FECHAMENTO: {store.n_matches}")
    print(f"  bookmakers: {', '.join(store.books())}")
    print(f"  mercados  : {', '.join(store.markets())}")

    print("\nRode o backtest com corpus_source='football_data_uk' e "
          "odds_source='football_data_uk'.")
    return 0 if report.failed == 0 else 2


def validate_strategy_cli(args: list[str]) -> int:
    """Valida historicamente a unica estrategia com vantagem confirmada.

    Regra: apostar em favoritos curtos (odd < 1.30), pegando a melhor odd
    entre as casas. NAO usa o modelo — o modelo foi testado e nao adiciona
    informacao ao mercado.
    """
    from betgsn.value_strategy import MAX_ODD, MIN_BOOKS, validate

    max_odd = float(_arg(args, "max-odd", str(MAX_ODD)))

    print("=" * 74)
    print("VALIDACAO — favoritos curtos com a melhor odd")
    print("=" * 74)
    print(f"\nregra : odd < {max_odd:.2f}, melhor preco entre >= {MIN_BOOKS} casas")
    print("fonte : cache do football-data.co.uk (38 competicoes, 2000-2026)")
    print("modelo: NAO usado (a vantagem e estrutural, nao preditiva)\n")
    print("percorrendo o cache...")

    def progress(done: int, total: int) -> None:
        print(f"  {done}/{total} partidas", end="\r")

    try:
        v = validate(max_odd=max_odd, use_cache=False, progress=progress)
    except Exception as exc:
        print(f"\nERRO: {type(exc).__name__}: {exc}")
        print("Baixe os dados primeiro: python betgsn.py --import-fduk")
        return 1

    print()
    print(f"\n  apostas         : {v.n_bets}")
    print(f"  odd media       : {v.avg_odd:.3f}")
    print(f"  ROI             : {v.roi * 100:+.2f}%")
    print(f"  t-estatistico   : {v.t:+.2f}")
    print(f"  IC95% (bootstrap): [{v.ci_low * 100:+.2f}%, {v.ci_high * 100:+.2f}%]")
    print(f"  anos positivos  : {v.positive_years}/{v.total_years}")
    print(f"  ligas positivas : {v.positive_leagues}/{v.total_leagues}")

    if v.significant:
        print("\n  VANTAGEM CONFIRMADA — o IC95% nao cruza zero.")
    else:
        print("\n  SEM significancia estatistica neste recorte.")

    print("\n  por ano:")
    for y, roi in v.by_year.items():
        mark = "+" if roi > 0 else "-"
        print(f"    {y}  ROI {roi * 100:+6.2f}%  {mark}")

    print("\n  por liga (>= 60 apostas):")
    for lg, roi in sorted(v.by_league.items(), key=lambda kv: -kv[1]):
        print(f"    {lg[:38]:38} {roi * 100:+6.2f}%")

    anos = max(1, v.total_years)
    por_ano = v.n_bets / anos
    print("\n  projecao (banca 1.000, 1% por aposta):")
    print(f"    apostas/ano : {por_ano:.0f}")
    print(f"    lucro/ano   : R$ {por_ano * 10 * v.roi:+.0f} "
          f"({por_ano * 10 * v.roi / 1000 * 100:+.2f}% da banca)")

    print("\n  LIMITES: ROI pequeno, variancia grande, exige varias casas.")
    print("  Nao e previsao de futebol e nao e garantia.")
    return 0


def scan_value_cli(args: list[str]) -> int:
    """Procura favoritos curtos nas odds AO VIVO, com a melhor odd disponivel."""
    from betgsn.providers import ProviderError
    from betgsn.value_strategy import MAX_ODD, MIN_BOOKS, scan_live

    raw = _arg(args, "sports", "soccer_brazil_campeonato,soccer_epl,"
                               "soccer_spain_la_liga,soccer_italy_serie_a,"
                               "soccer_germany_bundesliga,soccer_france_ligue_one")
    sports = [s.strip() for s in raw.split(",") if s.strip()]
    max_odd = float(_arg(args, "max-odd", str(MAX_ODD)))

    print("=" * 74)
    print("SCANNER — favoritos curtos com a melhor odd ao vivo")
    print("=" * 74)
    print(f"\nregra : odd < {max_odd:.2f}, melhor preco entre >= {MIN_BOOKS} casas")
    print(f"fontes: {len(sports)} competicao(oes)\n")

    try:
        ops, meta = scan_live(sports, max_odd=max_odd)
    except ProviderError as exc:
        print(f"ERRO: {exc}")
        print("\nConfigure a chave no .env:  BETGSN_ODDS_API_KEY=...")
        return 1

    print("  por competicao:")
    for sport, info in meta["sports"].items():
        if "error" in info:
            print(f"    {sport[:38]:38} ERRO: {info['error'][:40]}")
        else:
            print(f"    {sport[:38]:38} {info['events']:3d} jogos  "
                  f"{info['opportunities']:2d} oportunidades")
    if meta.get("credits_remaining"):
        print(f"\n  creditos restantes: {meta['credits_remaining']}")

    print()
    if not ops:
        print("  Nenhum favorito curto agora.")
        print("  (normal: a regra e seletiva, poucos jogos por dia se qualificam)")
        return 0

    print(f"  {len(ops)} oportunidade(s):\n")

    # ROI historico por faixa, para dar contexto a cada linha
    from betgsn.value_strategy import ODD_BANDS, validate

    bands: dict[str, float] = {}
    try:
        v = validate()   # usa cache se existir
        bands = v.by_band
    except Exception:
        pass

    def band_of(odd: float) -> str:
        for lo, hi in ODD_BANDS:
            if lo <= odd < hi:
                return f"{lo:.2f}-{hi:.2f}" if hi < 90 else f">= {lo:.2f}"
        return "?"

    print(f"  {'jogo':32} {'ap':>4} {'odd':>6} {'casa':>13} {'casas':>5} "
          f"{'faixa':>11} {'ROI hist':>9}")
    print("  " + "-" * 86)
    for o in ops:
        band = band_of(o.best_odd)
        roi_band = bands.get(band)
        roi_txt = f"{roi_band * 100:+6.2f}%" if roi_band is not None else "      —"
        mark = " *" if o.best_odd < MAX_ODD else "  "
        print(f"  {o.match[:32]:32} {o.outcome:>4} {o.best_odd:6.2f} "
              f"{o.best_book[:13]:>13} {o.n_books:5d} {band:>11} {roi_txt:>9}{mark}")

    print("\n  * = dentro da faixa validada (odd < 1.30)")
    print("  'ROI hist' = retorno medio daquela faixa em 195 mil jogos (2000-2026)")
    print("\n  POR QUE NAO MOSTRO 'EDGE vs JUSTO':")
    print("  o consenso de mercado JA e enviesado a favor do favorito. Comparar")
    print("  o melhor preco contra esse consenso da edge negativo em quase tudo —")
    print("  e mesmo assim a faixa curta da lucro, porque o proprio consenso")
    print("  subestima favoritos. O que vale e o ROI historico da faixa.")
    print("\n  COMO USAR: aposte em TODAS da faixa curta. A vantagem e a media.")
    print("  Uma aposta isolada e sorte; ~260 por ano viram vantagem.")
    return 0


def staking_plan_cli(args: list[str]) -> int:
    """Mostra a alavancagem possivel, o teto matematico e o risco de ruina.

    Responde: "quanto posso apostar para crescer mais rapido sem quebrar?"
    A resposta tem um teto duro (Kelly), e acima do dobro dele apostar
    mais faz perder mais rapido.
    """
    import math

    from betgsn.staking import (
        PLANS,
        annual_growth,
        flat_plan,
        full_kelly,
        kelly_table,
        recommend,
        simulate,
        win_prob,
    )
    from betgsn.value_strategy import (
        BETS_PER_YEAR,
        EDGE_ODD,
        EDGE_ROI,
        EDGE_SE,
    )

    years = float(_arg(args, "years", "3"))
    paths = int(_arg(args, "paths", "20000"))
    banca = float(_arg(args, "bankroll", "1000"))

    # A vantagem e da ESTRATEGIA validada (value_strategy); o staking e
    # generico e recebe os parametros explicitamente (FASE A).
    p = win_prob(EDGE_ROI, EDGE_ODD)
    fk = full_kelly(EDGE_ROI, EDGE_ODD)

    # O plano "Kelly cheio" depende da vantagem: construido aqui, com a
    # evidencia da estrategia — nao nasce pronto no core de staking.
    plans = dict(PLANS)
    plans["kelly"] = flat_plan("Kelly cheio", fk)

    print("=" * 82)
    print("ALAVANCAGEM — quanto apostar, quanto cresce, quanto arrisca")
    print("=" * 82)
    print()
    print(f"  vantagem validada : ROI {EDGE_ROI * 100:+.2f}% a odd media {EDGE_ODD}")
    print(f"  prob. de acerto   : {p * 100:.2f}%")
    print(f"  apostas por ano   : {BETS_PER_YEAR}")
    print(f"  KELLY COMPLETO    : {fk * 100:.2f}% da banca por aposta")

    print()
    print("-" * 82)
    print("A CURVA: quanto apostar -> quanto cresce -> quanto arrisca")
    print("-" * 82)
    print()
    print(f"  {'% da banca':>11}  {'x Kelly':>7}  {'cresc. ano':>11}  "
          f"{'P(metade)':>10}  {'P(perda 90%)':>12}")
    print("  " + "-" * 74)
    for row in kelly_table(EDGE_ROI, EDGE_ODD, BETS_PER_YEAR):
        tag = ""
        if row["kelly_multiple"] == 1.0:
            tag = "  <- otimo"
        elif row["kelly_multiple"] > 1.0:
            tag = "  <- cresce MENOS"
        print(f"  {row['fraction'] * 100:10.2f}%  {row['kelly_multiple']:7.2f}  "
              f"{row['annual_pct']:10.2f}%  {row['p_halve'] * 100:9.2f}%  "
              f"{row['p_ruin'] * 100:11.3f}%{tag}")

    # onde o crescimento vira negativo
    f, neg = 0.01, None
    while f < 0.5:
        if annual_growth(f, EDGE_ROI, EDGE_ODD, BETS_PER_YEAR) < 0:
            neg = f
            break
        f += 0.005
    print()
    print(f"  TETO: em {fk * 100:.2f}% por aposta o crescimento e maximo "
          f"({math.expm1(annual_growth(fk, EDGE_ROI, EDGE_ODD, BETS_PER_YEAR)) * 100:.1f}%/ano).")
    if neg:
        print(f"  Acima de {neg * 100:.1f}% por aposta o crescimento vira NEGATIVO:")
        print("  voce nao esta alavancando, esta perdendo mais rapido.")

    print()
    print("-" * 82)
    print(f"SIMULACAO — {paths:,} bancas, {years:.0f} anos".replace(",", "."))
    print("-" * 82)
    print()
    print(f"  {'plano':36} {'mediana':>8} {'ano':>7} {'lucro':>7} "
          f"{'2x':>6} {'metade':>7} {'ruina':>7}")
    print("  " + "-" * 88)
    for key in ("conservador", "moderado", "agressivo", "kelly", "sobrekelly",
                "faseado", "faseado_disjuntor"):
        r = simulate(plans[key], years=years, n_paths=paths,
                     bets_per_year=BETS_PER_YEAR, edge=EDGE_ROI,
                     edge_se=EDGE_SE, odd=EDGE_ODD)
        print(f"  {r.plan[:36]:36} {r.median_multiple:7.2f}x "
              f"{r.median_annual_pct:+6.1f}% {r.p_profit * 100:6.1f}% "
              f"{r.p_double * 100:5.1f}% {r.p_halve * 100:6.1f}% "
              f"{r.p_ruin * 100:6.2f}%")

    print()
    plan = recommend()
    r = simulate(plan, years=years, n_paths=paths,
                 bets_per_year=BETS_PER_YEAR, edge=EDGE_ROI,
                 edge_se=EDGE_SE, odd=EDGE_ODD)
    print("-" * 82)
    print(f"PLANO RECOMENDADO — {plan.name}")
    print("-" * 82)
    print()
    for ph in plan.phases:
        lim = "sem limite" if ph.until_multiple > 1e8 else f"{ph.until_multiple:.1f}x"
        print(f"    banca abaixo de {lim:>10}  ->  {ph.fraction * 100:.1f}% por aposta")
    print(f"    disjuntor : queda de {plan.drawdown_cut * 100:.0f}% do topo "
          f"-> metade da fracao")
    print(f"    parada    : banca abaixo de {plan.stop_loss * 100:.0f}% "
          f"-> para e reavalia")
    print()
    print(f"    mediana {years:.0f} anos : {r.median_multiple:.2f}x "
          f"({r.median_annual_pct:+.1f}%/ano)")
    print(f"    faixa 5-95%     : {r.p5:.2f}x a {r.p95:.2f}x")
    print(f"    chance de lucro : {r.p_profit * 100:.1f}%")
    print(f"    risco de metade : {r.p_halve * 100:.1f}%")
    print(f"    risco de ruina  : {r.p_ruin * 100:.2f}%")

    print()
    print(f"    banca {banca:.0f} -> {banca * r.median_multiple:.0f} "
          f"em {years:.0f} anos (mediana)")

    print()
    print("-" * 82)
    print("LIMITES — leia antes de usar")
    print("-" * 82)
    print("""
  - A vantagem e PEQUENA (+1,6%) e a variancia e grande. 9 de 27 anos
    fecharam negativos nos dados historicos.
  - A vantagem estimada tem erro. Se a verdadeira for a borda inferior do
    intervalo (+0,5%), apostar Kelly cheio vira sobre-aposta e quebra.
    Por isso o plano recomendado usa ~metade do Kelly.
  - Exige varias casas: a vantagem E o line shopping. Com uma casa so,
    o ROI vira negativo.
  - O disjuntor e a parada sao obrigatorios: sem eles o pior caso e a
    ruina, nao um ano ruim.
  - Retorno de 100% ao mes nao existe. O teto matematico desta vantagem
    e ~17% ao ANO no Kelly completo, e o Kelly completo tem 50% de chance
    de cortar a banca pela metade.
""")
    return 0


def import_live_fixtures_cli(args: list[str]) -> int:
    """Baixa os JOGOS FUTUROS com odds reais (football-data.co.uk).

    Estes sao os jogos que a tela SINAIS usa. Sem eles, a tela cai no
    dataset sintetico — que tem datas fixas no codigo e odds geradas pelo
    proprio modelo, portanto inuteis para decidir aposta.

    FALLBACK: o football-data.co.uk publica fixtures.csv apenas da rodada
    corrente (atualiza sex/ter). Na janela entre rodadas o arquivo nao
    tem NENHUM jogo futuro; nesse caso as fixtures futuras sao buscadas
    na The Odds API (odds multi-casa reais) e gravadas no cache local.
    Fonte primaria nao tem jogo futuro != cobertura total: o fallback
    nunca e consultado quando o CSV ainda cobre.
    """
    from betgsn.football_data_uk import FootballDataClient
    from betgsn.fixtures_odds_api import fetch_odds_api_fixtures, has_future_fixture

    print("=" * 74)
    print("JOGOS FUTUROS REAIS — football-data.co.uk")
    print("=" * 74)
    print("\nbaixando fixtures.csv e new_league_fixtures.csv...")

    client = FootballDataClient()
    _paths, report = client.fetch_fixtures()

    if report.failed:
        for err in report.errors[:3]:
            print(f"  ! {err}")
        if report.downloaded == 0:
            print("\nERRO: nao foi possivel baixar. Verifique a conexao.")
            return 1

    inv = client.fixtures_inventory()
    print(f"\n  jogos futuros   : {inv['n_fixtures']}")
    print(f"  com odds        : {inv['with_odds']}")
    print(f"  competicoes     : {len(inv['competitions'])}")
    print(f"  periodo         : {inv['first_date']} a {inv['last_date']}")

    fixtures = client.load_fixtures()

    # ------------------------------------------------------------------
    # Fallback: CSV sem NENHUM jogo futuro (janela entre rodadas)
    # ------------------------------------------------------------------
    if not has_future_fixture(fixtures):
        print("\nSem jogos futuros no CSV (janela entre rodadas do site).")
        from betgsn.providers import OddsApiProvider

        if OddsApiProvider.from_env() is None:
            print(
                "AVISO: fallback The Odds API indisponivel "
                "(sem BETGSN_ODDS_API_KEY no .env)."
            )
            print(
                "O modo REAL continua SEM cobertura ate o site atualizar "
                "o CSV (sextas e tercas). Nenhum dado foi fabricado."
            )
        else:
            print("Buscando fallback na The Odds API (h2h, regiao eu)...")
            fb = fetch_odds_api_fixtures(client)
            for err in fb.errors[:3]:
                print(f"  ! {err}")
            if fb.wrote_cache:
                print(f"  ligas respondidas: {fb.fetched_sports}")
                print(f"  eventos          : {fb.events}")
                print(f"  fixtures criadas : {fb.fixtures}")
                if fb.credits.get("remaining") is not None:
                    print(
                        f"  creditos restantes: {fb.credits['remaining']} "
                        f"(usados: {fb.credits.get('used', '?')})"
                    )
                if fb.unresolved_teams:
                    print(
                        f"  times sem alias  : {len(fb.unresolved_teams)} "
                        "(matching so por normalizacao de nome; sem "
                        "garantia de rating — veja team_aliases.json)"
                    )
                fixtures = client.load_fixtures()
            else:
                print(
                    "AVISO: o fallback falhou (chave/quota/rede). O modo REAL "
                    "continua sem cobertura; nada foi fabricado."
                )

    if fixtures:
        # a amostra mostra jogos FUTUROS (o que o fluxo REAL consome);
        # jogos passados do CSV da rodada anterior ficam de fora da lista
        from betgsn.fixtures_odds_api import has_future_fixture as _has_future

        com_odds = [
            f for f in fixtures
            if f.has_odds and f.has_kickoff and _has_future([f])
        ]
        if com_odds:
            print(f"\n  proximos jogos futuros com odds ({len(com_odds)} no total):")
            for f in com_odds[:12]:
                best = f.best_odds.get("Resultado Final (1X2)", {})
                if best:
                    odd_txt = "  ".join(
                        f"{oc}={best[oc]:.2f}({f.best_books['Resultado Final (1X2)'][oc]})"
                        for oc in ("1", "X", "2") if oc in best
                    )
                else:
                    odd_txt = "—"
                origem = " [fallback]" if f.source == "the_odds_api" else ""
                print(f"    {f.date} {f.time}  {f.match[:40]:40} {odd_txt}{origem}")

    # invalida o cache do servico para a tela pegar os dados novos
    from betgsn.real_signals import real_signals_service

    real_signals_service.invalidate()

    print("\nPronto. A tela SINAIS passa a usar esses jogos (fonte: real).")
    print("Os arquivos CSV sao atualizados pelo site as sextas e as tercas;")
    print("na janela entre rodadas o fallback The Odds API cobre os jogos futuros.")
    return 0


def show_sources_cli() -> int:
    """Mostra o estado dos caches de dados historicos importados."""
    from betgsn.backtest_sources import (
        OddsHistoryCache,
        load_imported_matches,
        read_manifest,
    )
    from betgsn.providers import available_providers

    print("=" * 70)
    print("BETGSN — FONTES DE DADOS HISTORICOS")
    print("=" * 70)
    print("\nChaves de API configuradas:")
    for name, ok in available_providers().items():
        print(f"  [{'X' if ok else ' '}] {name}")

    print("\nCache de odds historicas:")
    stats = OddsHistoryCache().stats()
    if not stats:
        print("  (vazio) — importe com --import-odds")
    for sport, info in stats.items():
        print(f"  {sport}: {info['snapshots']} snapshots "
              f"({info['first']} -> {info['last']})")

    print("\nTemporadas importadas:")
    matches = load_imported_matches()
    if not matches:
        print("  (vazio) — importe com --import-fixtures")
    else:
        from betgsn.backtest_data import HistoricalCorpus

        st = HistoricalCorpus(matches).stats()
        print(f"  {st.n_matches} partidas de {st.first_kickoff[:10]} a {st.last_kickoff[:10]}")
        print(f"  competicoes: {', '.join(st.competitions) or '—'}")
        print(f"  temporadas : {', '.join(st.seasons) or '—'}")

    manifest = read_manifest()
    imports = manifest.get("imports", [])
    if imports:
        print(f"\nUltimas importacoes ({len(imports)} no total):")
        for entry in imports[-5:]:
            print(f"  {entry.get('finished_at')} · {entry.get('kind')} · "
                  f"{entry.get('imported')} registros · janela {entry.get('window')}")
    return 0


def run_walkforward_cli() -> int:
    """Backtest rolling point-in-time (motor novo), com metricas no console.

    Reutiliza o MESMO motor da API e da tela BACKTEST. Para cada partida,
    o contexto e cortado no kickoff: nada posterior participa do calculo.
    """
    from betgsn.backtest_data import HistoricalCorpus
    from betgsn.backtest_engine import BacktestConfig, run_backtest, simulate_bankroll
    from betgsn.backtest_metrics import compute_metrics
    from betgsn.data import build_dataset

    kwargs: dict = {}
    for a in sys.argv[1:]:
        if a.startswith("--min-ev="):
            kwargs["min_ev"] = float(a.split("=", 1)[1]) / 100.0
        elif a.startswith("--min-history="):
            kwargs["min_history"] = int(a.split("=", 1)[1])
        elif a.startswith("--bankroll="):
            kwargs["bankroll"] = float(a.split("=", 1)[1])
        elif a.startswith("--start="):
            kwargs["start_date"] = a.split("=", 1)[1]
        elif a.startswith("--end="):
            kwargs["end_date"] = a.split("=", 1)[1]

    config = BacktestConfig(**kwargs)
    corpus = HistoricalCorpus(build_dataset().history)

    def progress(phase: str, done: int, total: int, message: str) -> None:
        if total and (done % 50 == 0 or done == total):
            print(f"  [{phase}] {message}")

    print("=" * 74)
    print("BETGSN — BACKTEST ROLLING (point-in-time, sem look-ahead)")
    print("=" * 74)
    run = run_backtest(corpus, config, progress=progress)
    sim = simulate_bankroll(run.signals, config)
    metrics = compute_metrics(run, sim)
    a = metrics.aggregate

    print(f"partidas no periodo : {run.n_matches_in_period}")
    print(f"partidas avaliadas  : {run.n_matches_evaluated} "
          f"(puladas: {run.n_matches_skipped} {dict(run.skipped_reasons)})")
    print(f"duracao             : {run.duration_ms:.0f} ms")
    print(f"config hash         : {run.config_hash}   modelo v{run.model_version}")
    print("-" * 74)
    print(f"sinais              : {a.n_signals}  (liquidados {a.n_settled}, "
          f"push {a.n_pushes}, sem liquidar {a.n_unsettled})")
    print(f"acertos / erros     : {a.n_wins} / {a.n_losses}")
    print(f"taxa de acerto      : {a.hit_rate * 100:.2f}%  "
          f"IC95% [{a.hit_rate_ci[0] * 100:.2f}, {a.hit_rate_ci[1] * 100:.2f}]")
    print(f"odd media           : {a.avg_odd:.3f}")
    print(f"EV medio previsto   : {a.avg_ev * 100:+.2f}%")
    print(f"retorno medio real  : {a.avg_realized_return * 100:+.2f}%  "
          f"(gap {a.ev_gap * 100:+.2f}pp)")
    print(f"Brier / Log-loss    : {a.brier:.4f} / {a.logloss:.4f}")
    print("-" * 74)
    print("CALIBRACAO (previsto -> observado)")
    for b in metrics.calibration:
        flag = "" if b.sufficient else "  [amostra insuficiente]"
        print(f"  {b.label:>10}  prev {b.avg_predicted * 100:5.1f}%  "
              f"real {b.observed_rate * 100:5.1f}%  n={b.n:5d}{flag}")
    print("-" * 74)
    print("POR FAIXA DE EV")
    for b in metrics.ev_buckets:
        print(f"  {b.label:>7}  EV {b.avg_ev * 100:5.1f}%  "
              f"retorno real {b.avg_realized_return * 100:+6.1f}%  n={b.n:5d}")
    print("-" * 74)
    print("SIMULACAO DE BANCA (historica, nao e previsao)")
    print(f"  capital  : {sim.initial_bankroll:.2f} -> {sim.final_bankroll:.2f} "
          f"({sim.return_pct * 100:+.1f}%)   ROI {sim.roi * 100:+.1f}%")
    print(f"  drawdown : max {sim.max_drawdown * 100:.1f}% em {sim.max_drawdown_day}")
    print(f"  streaks  : {sim.longest_win_streak} vitorias / "
          f"{sim.longest_loss_streak} derrotas seguidas")
    print("-" * 74)
    print("AVISO: o mercado padrao e um baseline sintetico que ignora forca de")
    print("time. Os numeros medem se o modelo agrega informacao sobre esse")
    print("baseline — nao sao evidencia de lucro no mercado real.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--import-odds" in args:
        return import_odds_cli(args)
    if "--import-fixtures" in args:
        return import_fixtures_cli(args)
    if "--import-fduk" in args:
        return import_fduk_cli(args)
    if "--import-fixtures-live" in args:
        return import_live_fixtures_cli(args)
    if "--capture-odds" in args:
        return capture_odds_cli(args)
    if "--validate-strategy" in args:
        return validate_strategy_cli(args)
    if "--scan-value" in args:
        return scan_value_cli(args)
    if "--staking-plan" in args:
        return staking_plan_cli(args)
    if "--sources" in args:
        return show_sources_cli()
    if "--walkforward" in args:
        return run_walkforward_cli()
    if "--api" in args:
        return run_api()
    if "--backtest" in args:
        return run_backtest_cli()
    if "--cli" in args:
        return run_cli()
    if "--selftest" in args:
        return selftest()
    if "--providers" in args:
        return show_providers()
    from betgsn.gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
