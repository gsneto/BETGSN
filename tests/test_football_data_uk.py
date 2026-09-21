"""Testes de `betgsn.football_data_uk` — parser dos CSVs historicos.

Nenhum teste toca a rede: os CSVs sao construidos inline no formato real
(cabecalho + linhas), o que permite exercitar o parser com precisao.

O que se prova aqui:
  - datas em dois formatos (dd/mm/yy e dd/mm/yyyy) e invalidas;
  - odds de 1X2, Over/Under e Handicap Asiatico, abertura e fechamento;
  - a Pinnacle usa prefixo diferente por mercado (bug real ja corrigido);
  - colunas agregadas (Max/Avg) nao entram como se fossem casas;
  - estatisticas (cantos, cartoes, chutes) e valores ausentes;
  - casamento de partida entre CSV e corpus.
"""

from __future__ import annotations

import pytest

from betgsn.football_data_uk import (
    ALL_LEAGUES,
    EXTRA_LEAGUES,
    MAIN_DIVISIONS,
    CsvMatch,
    FootballDataClient,
    RealOddsStore,
    UpcomingFixture,
    league_label,
    parse_csv,
    parse_date,
    parse_extra_fixture_row,
    parse_extra_row,
    parse_fixture_row,
    parse_fixtures_csv,
    parse_main_row,
    season_codes,
)
from betgsn.model import HistoricalMatch

# --------------------------------------------------------------------------
# Fixtures: CSVs no formato real
# --------------------------------------------------------------------------

MAIN_HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,"
    "HS,AS,HST,AST,HC,AC,HF,AF,HY,AY,HR,AR,"
    "B365H,B365D,B365A,PSH,PSD,PSA,BFEH,BFED,BFEA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,"
    "B365>2.5,B365<2.5,P>2.5,P<2.5,Max>2.5,Max<2.5,Avg>2.5,Avg<2.5,"
    "AHh,B365AHH,B365AHA,PAHH,PAHA,MaxAHH,MaxAHA,AvgAHH,AvgAHA,"
    "B365CH,B365CD,B365CA,PSCH,PSCD,PSCA,BFECH,BFECD,BFECA,MaxCH,MaxCD,MaxCA,"
    "AvgCH,AvgCD,AvgCA,B365C>2.5,B365C<2.5,PC>2.5,PC<2.5,MaxC>2.5,MaxC<2.5,"
    "AvgC>2.5,AvgC<2.5,AHCh,B365CAHH,B365CAHA,PCAHH,PCAHA,MaxCAHH,MaxCAHA,"
    "AvgCAHH,AvgCAHA"
)

MAIN_ROW = (
    "E0,16/08/2024,20:00,Man United,Fulham,1,0,H,0,0,D,R Jones,"
    "14,10,5,2,7,8,11,9,2,3,0,0,"
    "1.60,4.20,5.25,1.63,4.38,5.30,1.66,4.50,5.60,1.70,4.60,5.75,1.62,4.30,5.40,"
    "1.62,2.30,1.65,2.28,1.68,2.35,1.61,2.25,"
    "-1,2.05,1.85,2.02,1.88,2.10,1.80,2.05,1.85,"
    "1.67,4.10,5.00,1.65,4.23,5.28,1.72,4.20,5.40,1.75,4.40,5.60,"
    "1.66,4.20,5.02,1.62,2.30,1.63,2.38,1.70,2.45,"
    "1.61,2.37,-1,2.05,1.85,2.02,1.88,2.12,1.79,"
    "2.04,1.86"
)

EXTRA_HEADER = (
    "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,"
    "PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,"
    "BFECH,BFECD,BFECA,B365CH,B365CD,B365CA"
)

EXTRA_ROW = (
    "Brazil,Serie A,2024,14/04/2024,16:00,Palmeiras,Flamengo,2,1,H,"
    "1.75,3.86,5.25,1.76,3.87,5.31,1.69,3.50,4.90,"
    "1.72,3.90,5.20,1.70,3.80,5.10"
)


def main_row(**overrides: str) -> dict[str, str]:
    """Linha principal como dict, com campos sobrescritos."""
    cols = MAIN_HEADER.split(",")
    vals = MAIN_ROW.split(",")
    row = dict(zip(cols, vals))
    row.update(overrides)
    return row


def extra_row(**overrides: str) -> dict[str, str]:
    cols = EXTRA_HEADER.split(",")
    vals = EXTRA_ROW.split(",")
    row = dict(zip(cols, vals))
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# Datas
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("16/08/2024", "2024-08-16"),
        ("1/1/25", "2025-01-01"),
        ("31/12/99", "1999-12-31"),
        ("19/05/2012", "2012-05-19"),
    ],
)
def test_parse_date_accepts_both_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "2024-08-16", "32/13/2024", "abc"])
def test_parse_date_rejects_invalid(raw):
    assert parse_date(raw) is None


def test_season_codes():
    assert season_codes(2000, 2002) == ["0001", "0102", "0203"]
    assert season_codes(2024, 2024) == ["2425"]


# --------------------------------------------------------------------------
# Parser de liga principal
# --------------------------------------------------------------------------


def test_parse_main_row_core_fields():
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    assert m.home == "Man United" and m.away == "Fulham"
    assert (m.home_goals, m.away_goals) == (1, 0)
    assert m.date == "2024-08-16" and m.time == "20:00"
    assert m.timezone == "Europe/London"
    assert m.season == "2024/25"
    assert m.referee == "R Jones"


def test_parse_main_row_statistics():
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    assert (m.home_corners, m.away_corners) == (7, 8)
    assert (m.home_cards, m.away_cards) == (2, 3)   # amarelos + vermelhos
    assert (m.home_shots, m.away_shots) == (14, 10)
    assert (m.home_shots_on_target, m.away_shots_on_target) == (5, 2)


def test_parse_main_row_counts_red_cards():
    m = parse_main_row(main_row(HY="1", HR="1", AY="3", AR="0"), "E0")
    assert m is not None
    assert m.home_cards == 2      # 1 amarelo + 1 vermelho
    assert m.away_cards == 3


def test_parse_main_row_missing_cards_is_none():
    """Dado ausente nunca vira zero silencioso."""
    m = parse_main_row(main_row(HY="", HR="", AY="", AR=""), "E0")
    assert m is not None
    assert m.home_cards is None and m.away_cards is None


def test_parse_main_row_closing_odds():
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    one_x_two = m.odds_closing["Resultado Final (1X2)"]
    assert one_x_two["Pinnacle"] == {"1": 1.65, "X": 4.23, "2": 5.28}
    assert one_x_two["Bet365"] == {"1": 1.67, "X": 4.10, "2": 5.00}
    assert one_x_two["Betfair Exchange"] == {"1": 1.72, "X": 4.20, "2": 5.40}


def test_parse_main_row_opening_odds_differ_from_closing():
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    assert m.odds_opening["Resultado Final (1X2)"]["Pinnacle"] == {
        "1": 1.63, "X": 4.38, "2": 5.30
    }
    assert (
        m.odds_opening["Resultado Final (1X2)"]["Pinnacle"]
        != m.odds_closing["Resultado Final (1X2)"]["Pinnacle"]
    )


def test_pinnacle_uses_different_prefix_per_market():
    """Regressao: `P` em over/under e handicap, `PS` em 1X2.

    Misturar os prefixos deixava a Pinnacle de fora de over/under e
    handicap — um bug real que so aparecia ao comparar com o CSV cru.
    """
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    assert "Pinnacle" in m.odds_closing["Resultado Final (1X2)"]
    assert "Pinnacle" in m.odds_closing["Total de Gols"]
    assert "Pinnacle" in m.odds_closing["Handicap Asiatico"]


def test_parse_main_row_over_under():
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    ou = m.odds_closing["Total de Gols"]
    assert ou["Pinnacle"] == {"Over 2.5": 1.63, "Under 2.5": 2.38}
    assert ou["Bet365"] == {"Over 2.5": 1.62, "Under 2.5": 2.30}


def test_parse_main_row_asian_handicap_labels_match_engine():
    """O rotulo precisa casar com markets.market_asian_handicap."""
    from betgsn.markets import market_asian_handicap

    m = parse_main_row(main_row(), "E0")
    assert m is not None
    ah = m.odds_closing["Handicap Asiatico"]["Pinnacle"]
    # AHCh = -1 -> "AH Casa -1" / "AH Fora +1"
    assert set(ah) == {"AH Casa -1", "AH Fora +1"}

    # confere contra os rotulos que o motor gera de fato
    from betgsn.model import build_score_matrix

    matrix = build_score_matrix(1.6, 1.1)
    engine_labels = set(market_asian_handicap(matrix, lines=(-1.0,)))
    assert set(ah) == engine_labels


def test_aggregate_columns_are_not_bookmakers():
    """Max/Avg sao derivados; entrar como casa enviesaria o consenso."""
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    books = set(m.odds_closing["Resultado Final (1X2)"])
    assert "Max" not in books and "Avg" not in books
    assert "Melhor do mercado" not in books
    # mas ficam registrados como referencia
    assert m.market_reference["Melhor do mercado"]["1"] == 1.75


def test_parse_main_row_rejects_incomplete():
    assert parse_main_row(main_row(Date=""), "E0") is None
    assert parse_main_row(main_row(HomeTeam=""), "E0") is None
    assert parse_main_row(main_row(FTHG=""), "E0") is None
    assert parse_main_row(main_row(FTAG=""), "E0") is None
    assert parse_main_row(main_row(Date="invalida"), "E0") is None


def test_parse_main_row_unknown_division():
    assert parse_main_row(main_row(), "XX") is None


def test_odds_below_one_are_treated_as_missing():
    """Odd decimal valida e > 1.0; 0 ou negativo e ausente, nao '0.0'."""
    m = parse_main_row(main_row(PSCH="0", PSCD="0", PSCA="0"), "E0")
    assert m is not None
    assert "Pinnacle" not in m.odds_closing["Resultado Final (1X2)"]


def test_incomplete_book_is_dropped():
    """Casa com so um dos tres resultados nao entra."""
    m = parse_main_row(main_row(B365CH="1.67", B365CD="", B365CA="5.00"), "E0")
    assert m is not None
    assert "Bet365" not in m.odds_closing["Resultado Final (1X2)"]


# --------------------------------------------------------------------------
# Parser de liga extra
# --------------------------------------------------------------------------


def test_parse_extra_row_core():
    m = parse_extra_row(extra_row(), "BRA")
    assert m is not None
    assert m.home == "Palmeiras" and m.away == "Flamengo"
    assert (m.home_goals, m.away_goals) == (2, 1)
    assert m.season == "2024"
    assert m.timezone == "America/Sao_Paulo"
    assert m.league == "Serie A (Brazil)"


def test_parse_extra_row_only_has_1x2():
    """Ligas extras nao trazem over/under nem handicap nem estatisticas."""
    m = parse_extra_row(extra_row(), "BRA")
    assert m is not None
    assert list(m.odds_closing) == ["Resultado Final (1X2)"]
    assert m.home_corners is None
    assert m.home_cards is None


def test_parse_extra_row_odds():
    m = parse_extra_row(extra_row(), "BRA")
    assert m is not None
    one_x_two = m.odds_closing["Resultado Final (1X2)"]
    assert one_x_two["Pinnacle"] == {"1": 1.75, "X": 3.86, "2": 5.25}
    assert one_x_two["Betfair Exchange"] == {"1": 1.72, "X": 3.90, "2": 5.20}


def test_parse_extra_row_unknown_code():
    assert parse_extra_row(extra_row(), "ZZZ") is None


# --------------------------------------------------------------------------
# parse_csv
# --------------------------------------------------------------------------


def test_parse_csv_full_document():
    text = f"{MAIN_HEADER}\n{MAIN_ROW}\n{MAIN_ROW}\n"
    matches = parse_csv(text, "E0")
    assert len(matches) == 2
    assert all(isinstance(m, CsvMatch) for m in matches)


def test_parse_csv_skips_bad_rows():
    text = f"{MAIN_HEADER}\n{MAIN_ROW}\n,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n"
    matches = parse_csv(text, "E0")
    assert len(matches) == 1


def test_parse_csv_empty():
    assert parse_csv(MAIN_HEADER + "\n", "E0") == []
    assert parse_csv("", "E0") == []


def test_parse_csv_routes_by_league_type():
    assert len(parse_csv(f"{EXTRA_HEADER}\n{EXTRA_ROW}\n", "BRA")) == 1
    assert len(parse_csv(f"{EXTRA_HEADER}\n{EXTRA_ROW}\n", "E0")) == 0


# --------------------------------------------------------------------------
# Conversao para o corpus e casamento
# --------------------------------------------------------------------------


def test_to_historical_preserves_everything():
    m = parse_main_row(main_row(), "E0")
    assert m is not None
    h = m.to_historical()
    assert isinstance(h, HistoricalMatch)
    assert h.home == m.home and h.away == m.away
    assert (h.home_goals, h.away_goals) == (1, 0)
    assert h.kickoff == "2024-08-16 20:00"
    assert h.timezone == "Europe/London"
    assert h.league == m.league and h.season == m.season
    assert h.home_corners == 7 and h.away_corners == 8


def test_season_derived_from_date():
    """Temporada europeia comeca em julho."""
    assert parse_main_row(main_row(Date="16/08/2024"), "E0").season == "2024/25"
    assert parse_main_row(main_row(Date="01/03/2024"), "E0").season == "2023/24"
    assert parse_main_row(main_row(Date="30/06/2024"), "E0").season == "2023/24"
    assert parse_main_row(main_row(Date="01/07/2024"), "E0").season == "2024/25"


def test_match_key_is_stable_across_name_variants():
    a = parse_main_row(main_row(HomeTeam="São Paulo"), "E0")
    b = parse_main_row(main_row(HomeTeam="Sao Paulo FC"), "E0")
    assert a is not None and b is not None
    assert a.match_key.split("|")[1] == b.match_key.split("|")[1]


# --------------------------------------------------------------------------
# Store de odds reais
# --------------------------------------------------------------------------


def test_store_indexes_and_retrieves():
    matches = [parse_main_row(main_row(), "E0")]
    store = RealOddsStore(closing=True)
    assert store.add(matches) == 1
    assert len(store) == 1

    hm = matches[0].to_historical()
    odds = store.odds_for(hm)
    assert "Resultado Final (1X2)" in odds
    assert odds["Resultado Final (1X2)"]["Pinnacle"]["1"] == 1.65


def test_store_opening_vs_closing():
    matches = [parse_main_row(main_row(), "E0")]
    close = RealOddsStore(closing=True)
    open_ = RealOddsStore(closing=False)
    close.add(matches)
    open_.add(matches)

    hm = matches[0].to_historical()
    assert close.odds_for(hm)["Resultado Final (1X2)"]["Pinnacle"]["1"] == 1.65
    assert open_.odds_for(hm)["Resultado Final (1X2)"]["Pinnacle"]["1"] == 1.63


def test_store_returns_empty_for_unknown_match():
    store = RealOddsStore()
    store.add([parse_main_row(main_row(), "E0")])
    other = HistoricalMatch(
        home="Time X", away="Time Y", home_goals=1, away_goals=0,
        kickoff="2030-01-01 15:00", timezone="Europe/London",
    )
    assert store.odds_for(other) == {}


def test_store_reports_books_and_markets():
    store = RealOddsStore()
    store.add([parse_main_row(main_row(), "E0")])
    assert "Pinnacle" in store.books()
    assert "Max" not in store.books()
    assert set(store.markets()) >= {
        "Resultado Final (1X2)", "Total de Gols", "Handicap Asiatico"
    }


#: Colunas de ODDS DE FECHAMENTO (o "C" e o marcador de closing).
CLOSING_COLUMNS = {
    # 1X2
    "B365CH", "B365CD", "B365CA", "PSCH", "PSCD", "PSCA",
    "BFECH", "BFECD", "BFECA", "MaxCH", "MaxCD", "MaxCA",
    "AvgCH", "AvgCD", "AvgCA",
    # over/under 2.5
    "B365C>2.5", "B365C<2.5", "PC>2.5", "PC<2.5",
    "MaxC>2.5", "MaxC<2.5", "AvgC>2.5", "AvgC<2.5",
    # handicap asiatico
    "AHCh", "B365CAHH", "B365CAHA", "PCAHH", "PCAHA",
    "MaxCAHH", "MaxCAHA", "AvgCAHH", "AvgCAHA",
}

#: Colunas de odds de ABERTURA.
OPENING_COLUMNS = {
    "B365H", "B365D", "B365A", "PSH", "PSD", "PSA",
    "BFEH", "BFED", "BFEA", "MaxH", "MaxD", "MaxA",
    "AvgH", "AvgD", "AvgA",
    "B365>2.5", "B365<2.5", "P>2.5", "P<2.5",
    "Max>2.5", "Max<2.5", "Avg>2.5", "Avg<2.5",
    "AHh", "B365AHH", "B365AHA", "PAHH", "PAHA",
    "MaxAHH", "MaxAHA", "AvgAHH", "AvgAHA",
}


def test_fixture_covers_every_odds_column():
    """Garante que as listas acima acompanham o cabecalho."""
    cols = set(MAIN_HEADER.split(","))
    assert CLOSING_COLUMNS <= cols, CLOSING_COLUMNS - cols
    assert OPENING_COLUMNS <= cols, OPENING_COLUMNS - cols
    assert not (CLOSING_COLUMNS & OPENING_COLUMNS)


def test_store_skips_matches_without_odds():
    """Partida sem NENHUMA odd nao entra no indice."""
    blanks = {col: "" for col in CLOSING_COLUMNS}
    sem = parse_main_row(main_row(**blanks), "E0")
    assert sem is not None
    assert sem.odds_closing == {}, f"ainda tem: {list(sem.odds_closing)}"

    store = RealOddsStore(closing=True)
    assert store.add([sem]) == 0
    assert len(store) == 0


def test_store_keeps_match_with_partial_odds():
    """Basta UM mercado com odds para a partida entrar."""
    only_1x2 = {
        col: ""
        for col in CLOSING_COLUMNS
        if col not in {"B365CH", "B365CD", "B365CA", "PSCH", "PSCD", "PSCA",
                       "BFECH", "BFECD", "BFECA"}
    }
    parcial = parse_main_row(main_row(**only_1x2), "E0")
    assert parcial is not None
    assert list(parcial.odds_closing) == ["Resultado Final (1X2)"]

    store = RealOddsStore(closing=True)
    assert store.add([parcial]) == 1


# --------------------------------------------------------------------------
# Catalogo de ligas
# --------------------------------------------------------------------------


def test_league_catalog_is_consistent():
    assert set(MAIN_DIVISIONS) & set(EXTRA_LEAGUES) == set()
    assert len(ALL_LEAGUES) == len(MAIN_DIVISIONS) + len(EXTRA_LEAGUES)
    for code, lg in ALL_LEAGUES.items():
        assert lg.code == code
        assert lg.name and lg.country and lg.timezone
    # ligas principais tem estatisticas; extras nao
    assert all(lg.has_stats for lg in MAIN_DIVISIONS.values())
    assert not any(lg.has_stats for lg in EXTRA_LEAGUES.values())


def test_league_label_format():
    assert league_label("E0") == "Premier League (England)"
    assert league_label("BRA") == "Serie A (Brazil)"
    assert league_label("ZZZ") == "ZZZ"


def test_all_brazilian_and_european_leagues_present():
    """As ligas que o usuario pediu: europeias + extras mundiais."""
    for code in ("E0", "SP1", "I1", "D1", "F1", "N1", "P1", "T1", "G1", "B1"):
        assert code in MAIN_DIVISIONS, f"falta {code}"
    assert "BRA" in EXTRA_LEAGUES
    for code in ("ARG", "JPN", "USA", "MEX"):
        assert code in EXTRA_LEAGUES, f"falta {code}"


# --------------------------------------------------------------------------
# Cliente (sem rede)
# --------------------------------------------------------------------------


def test_client_inventory_on_empty_dir(tmp_path):
    client = FootballDataClient(root=tmp_path)
    inv = client.inventory()
    assert inv["main_files"] == 0
    assert inv["extra_files"] == 0
    assert inv["total_mb"] == 0


def test_client_load_matches_from_cache(tmp_path):
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    (tmp_path / "extra" / "BRA.csv").write_text(
        f"{EXTRA_HEADER}\n{EXTRA_ROW}\n", encoding="utf-8"
    )
    matches = client.load_matches()
    assert len(matches) == 2
    assert {m.division for m in matches} == {"E0", "BRA"}

    only_main = client.load_matches(divisions=["E0"])
    assert len(only_main) == 1
    assert only_main[0].division == "E0"


def test_client_uses_cache_instead_of_redownloading(tmp_path, monkeypatch):
    """Arquivo em cache nao pode gerar nova requisicao."""
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )

    def explode(*_a, **_k):
        raise AssertionError("nao deveria baixar de novo")

    monkeypatch.setattr(client, "_get", explode)
    from betgsn.football_data_uk import DownloadReport

    report = DownloadReport()
    path = client.fetch_main("E0", "2425", report)
    assert path is not None
    assert report.cached == 1
    assert report.downloaded == 0


# --------------------------------------------------------------------------
# Indice em disco (resumo do corpus)
# --------------------------------------------------------------------------


def test_corpus_summary_builds_and_caches_index(tmp_path):
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    (tmp_path / "extra" / "BRA.csv").write_text(
        f"{EXTRA_HEADER}\n{EXTRA_ROW}\n", encoding="utf-8"
    )

    summary = client.corpus_summary()
    assert summary["n_matches"] == 2
    assert summary["n_with_closing_odds"] == 2
    assert "Pinnacle" in summary["books"]
    assert "Premier League (England)" in summary["competitions"]
    assert client.index_path.exists()


def test_corpus_summary_uses_index_without_reparsing(tmp_path, monkeypatch):
    """Com indice valido, nenhum CSV e lido de novo."""
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    client.corpus_summary()

    def explode(*_a, **_k):
        raise AssertionError("nao deveria reparsear os CSVs")

    monkeypatch.setattr(client, "load_matches", explode)
    summary = client.corpus_summary()
    assert summary["n_matches"] == 1


def test_index_is_invalidated_when_data_changes(tmp_path):
    """CSV novo muda a assinatura e forca a reconstrucao do indice."""
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    assert client.corpus_summary()["n_matches"] == 1

    # novo arquivo -> assinatura muda -> indice invalidado
    (tmp_path / "main" / "2425_SP1.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    assert client.cached_summary() is None
    assert client.corpus_summary()["n_matches"] == 2


def test_corpus_summary_refresh_forces_rebuild(tmp_path):
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    client.corpus_summary()
    assert client.corpus_summary(refresh=True)["n_matches"] == 1


def test_sample_books_is_cheap_and_finds_books(tmp_path):
    """A lista de casas nao pode exigir parsear o cache inteiro."""
    client = FootballDataClient(root=tmp_path)
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(
        f"{MAIN_HEADER}\n{MAIN_ROW}\n", encoding="utf-8"
    )
    books = client.sample_books()
    assert "Pinnacle" in books
    assert "Bet365" in books
    # colunas agregadas nunca aparecem como casa
    assert "Max" not in books and "Avg" not in books


# ==========================================================================
# JOGOS FUTUROS (fixtures.csv) — a fonte da tela SINAIS
# ==========================================================================

FIXTURES_HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,Referee,"
    "B365H,B365D,B365A,BFDH,BFDD,BFDA,BVH,BVD,BVA,BWH,BWD,BWA,PPH,PPD,PPA,"
    "SKBH,SKBD,SKBA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,BFEH,BFED,BFEA,"
    "B365>2.5,B365<2.5,Max>2.5,Max<2.5,Avg>2.5,Avg<2.5,BFE>2.5,BFE<2.5,"
    "AHh,B365AHH,B365AHA,MaxAHH,MaxAHA,AvgAHH,AvgAHA,BFEAHH,BFEAHA,"
    "B365CH,B365CD,B365CA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA"
)

FIXTURES_ROW = (
    "E0,20/09/2026,14:00,Arsenal,Chelsea,M Oliver,"
    "1.60,4.20,5.25,1.62,4.10,5.00,1.58,4.30,5.40,1.65,4.00,5.10,"
    "1.55,4.40,5.60,1.57,4.25,5.30,1.68,4.50,5.75,1.61,4.20,5.20,"
    "1.63,4.15,5.15,1.62,2.30,1.60,2.35,1.61,2.32,1.64,2.28,"
    "-0.75,1.95,1.85,1.98,1.82,1.94,1.86,1.97,1.84,"
    "1.60,4.20,5.25,1.68,4.50,5.75,1.61,4.20,5.20"
)

EXTRA_FIXTURES_HEADER = (
    "Country,League,Date,Time,Home,Away,"
    "PSH,PSD,PSA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,BFEH,BFED,BFEA,"
    "B365H,B365D,B365A"
)

EXTRA_FIXTURES_ROW = (
    "Brazil,Serie A,21/09/2026,16:00,Palmeiras,Flamengo,"
    "2.10,3.30,3.40,2.15,3.35,3.50,2.12,3.30,3.45,2.18,3.40,3.55,"
    "2.05,3.25,3.35"
)


def fixtures_row(**overrides: str) -> dict[str, str]:
    cols = FIXTURES_HEADER.split(",")
    vals = FIXTURES_ROW.split(",")
    row = dict(zip(cols, vals))
    row.update(overrides)
    return row


def test_parse_fixture_row_core():
    fx = parse_fixture_row(fixtures_row())
    assert fx is not None
    assert fx.home == "Arsenal" and fx.away == "Chelsea"
    assert fx.date == "2026-09-20" and fx.time == "14:00"
    assert fx.league == "Premier League (England)"
    assert fx.timezone == "Europe/London"
    assert fx.referee == "M Oliver"
    assert fx.match == "Arsenal vs Chelsea"
    assert fx.kickoff == "2026-09-20 14:00"


def test_parse_fixture_row_has_no_score():
    """Jogo futuro nao tem placar — e o que o distingue de CsvMatch."""
    fx = parse_fixture_row(fixtures_row())
    assert fx is not None
    assert not hasattr(fx, "home_goals")


def test_parse_fixture_row_odds_and_best():
    fx = parse_fixture_row(fixtures_row())
    assert fx is not None
    assert fx.has_odds
    one_x_two = fx.odds["Resultado Final (1X2)"]
    # PP = Paddy Power nos jogos futuros (nao Pinnacle)
    assert "Paddy Power" in one_x_two
    assert "Bet365" in one_x_two
    assert "Betfred" in one_x_two
    assert "BetVictor" in one_x_two
    assert "Bet&Win" in one_x_two
    assert "SkyBet" in one_x_two
    assert "Betfair Exchange" in one_x_two
    # melhor odd e a casa responsavel
    best = fx.best_odds["Resultado Final (1X2)"]
    who = fx.best_books["Resultado Final (1X2)"]
    assert best["1"] == max(one_x_two[b]["1"] for b in one_x_two)
    assert who["1"] in one_x_two


def test_parse_fixture_row_markets():
    fx = parse_fixture_row(fixtures_row())
    assert fx is not None
    assert "Total de Gols" in fx.odds
    assert "Handicap Asiatico" in fx.odds
    ou = fx.odds["Total de Gols"]
    assert ou["Bet365"] == {"Over 2.5": 1.62, "Under 2.5": 2.30}
    ah = fx.odds["Handicap Asiatico"]
    assert any(k.startswith("AH Casa") for k in next(iter(ah.values())))


def test_parse_fixture_row_rejects_incomplete():
    assert parse_fixture_row(fixtures_row(Date="")) is None
    assert parse_fixture_row(fixtures_row(HomeTeam="")) is None
    assert parse_fixture_row(fixtures_row(AwayTeam="")) is None
    assert parse_fixture_row(fixtures_row(Div="XX")) is None


def test_parse_fixture_row_rejects_without_odds():
    sem = {c: "" for c in FIXTURES_HEADER.split(",")
           if c not in ("Div", "Date", "Time", "HomeTeam", "AwayTeam", "Referee")}
    assert parse_fixture_row(fixtures_row(**sem)) is None


def test_parse_extra_fixture_row():
    cols = EXTRA_FIXTURES_HEADER.split(",")
    vals = EXTRA_FIXTURES_ROW.split(",")
    row = dict(zip(cols, vals))
    fx = parse_extra_fixture_row(row)
    assert fx is not None
    assert fx.home == "Palmeiras" and fx.away == "Flamengo"
    assert fx.league == "Serie A (Brazil)"
    assert fx.timezone == "America/Sao_Paulo"
    assert "Pinnacle" in fx.odds["Resultado Final (1X2)"]


def test_parse_extra_fixture_unknown_country():
    cols = EXTRA_FIXTURES_HEADER.split(",")
    vals = EXTRA_FIXTURES_ROW.split(",")
    row = dict(zip(cols, vals))
    row["Country"] = "Narnia"
    assert parse_extra_fixture_row(row) is None


def test_parse_fixtures_csv_full():
    text = f"{FIXTURES_HEADER}\n{FIXTURES_ROW}\n{FIXTURES_ROW}\n"
    out = parse_fixtures_csv(text, extra=False)
    assert len(out) == 2


def test_parse_fixtures_csv_handles_bom():
    """Os arquivos reais vem com BOM: sem tratar, tudo vira zero linhas.

    Este e um bug real que passou silenciosamente: o BOM transforma a
    primeira coluna em `\\ufeffDiv`, e `row.get("Div")` devolve None sem
    levantar erro nenhum.
    """
    text = f"\ufeff{FIXTURES_HEADER}\n{FIXTURES_ROW}\n"
    out = parse_fixtures_csv(text, extra=False)
    assert len(out) == 1, "BOM nao tratado: o parser perdeu todas as linhas"


def test_parse_csv_historical_handles_bom():
    """A protecao do BOM tambem vale para os arquivos historicos."""
    text = f"\ufeff{MAIN_HEADER}\n{MAIN_ROW}\n"
    matches = parse_csv(text, "E0")
    assert len(matches) == 1


def test_fixture_best_odds_are_the_maximum():
    fx = parse_fixture_row(fixtures_row())
    assert fx is not None
    for market, books in fx.odds.items():
        best = fx.best_odds[market]
        for oc, odd in best.items():
            assert odd == max(
                b[oc] for b in books.values() if oc in b
            ), f"{market}/{oc}: melhor odd incorreta"


def test_fixture_n_books_counts_1x2_only():
    fx = parse_fixture_row(fixtures_row())
    assert fx is not None
    assert fx.n_books == len(fx.odds["Resultado Final (1X2)"])
    assert fx.n_books >= 7
