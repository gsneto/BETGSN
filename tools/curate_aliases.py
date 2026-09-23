# Curadoria de aliases: valida rascunho manual contra o corpus FDUK global.
# Uso: ..\BETGSN\.venv\Scripts\python.exe tools\curate_aliases.py
import json
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from betgsn.football_data_uk import FootballDataClient
from betgsn.backtest_sources import normalize_team

# Rascunho curado a mao a partir do confronto provider x corpus (2526).
# Apenas identidades de clube inequivocas.
DRAFT = {
    # Inglaterra
    "E0": {
        "Brighton and Hove Albion": "Brighton",
        "Coventry City": "Coventry",
        "Hull City": "Hull",
        "Ipswich Town": "Ipswich",
        "Leeds United": "Leeds",
        "Manchester City": "Man City",
        "Manchester United": "Man United",
        "Newcastle United": "Newcastle",
        "Nottingham Forest": "Nott'm Forest",
        "Tottenham Hotspur": "Tottenham",
        "West Ham United": "West Ham",
        "Wolverhampton Wanderers": "Wolves",
    },
    "E1": {
        "Birmingham City": "Birmingham",
        "Blackburn Rovers": "Blackburn",
        "Bolton Wanderers": "Bolton",
        "Cardiff City": "Cardiff",
        "Charlton Athletic": "Charlton",
        "Derby County": "Derby",
        "Lincoln City": "Lincoln",
        "Norwich City": "Norwich",
        "Preston North End": "Preston",
        "Queens Park Rangers": "QPR",
        "Stoke City": "Stoke",
        "Swansea City": "Swansea",
        "West Bromwich Albion": "West Brom",
        "Sheffield Wednesday": "Sheffield Weds",
        "West Ham United": "West Ham",
        "Wolverhampton Wanderers": "Wolves",
        "Coventry City": "Coventry",
        "Hull City": "Hull",
        "Ipswich Town": "Ipswich",
        "Leeds United": "Leeds",
    },
    "E2": {
        "Bradford City": "Bradford",
        "Bromley FC": "Bromley",
        "Burton Albion": "Burton",
        "Cambridge United": "Cambridge",
        "Doncaster Rovers": "Doncaster",
        "Huddersfield Town": "Huddersfield",
        "Mansfield Town": "Mansfield",
        "Oxford United": "Oxford",
        "Peterborough United": "Peterboro",
        "Plymouth Argyle": "Plymouth",
        "Stockport County FC": "Stockport",
        "Wycombe Wanderers": "Wycombe",
        "Sheffield Wednesday": "Sheffield Weds",
    },
    "E3": {
        "Accrington Stanley": "Accrington",
        "Bristol Rovers": "Bristol Rvs",
        "Cheltenham Town": "Cheltenham",
        "Colchester United": "Colchester",
        "Crewe Alexandra": "Crewe",
        "Exeter City": "Exeter",
        "Grimsby Town": "Grimsby",
        "Northampton Town": "Northampton",
        "Oldham Athletic": "Oldham",
        "Rotherham United": "Rotherham",
        "Salford City": "Salford",
        "Shrewsbury Town": "Shrewsbury",
        "Swindon Town": "Swindon",
        "Tranmere Rovers": "Tranmere",
    },
    # Escocia
    "SC0": {"Falkirk F.C.": "Falkirk"},
    # Espanha
    "SP1": {
        "Athletic Bilbao": "Ath Bilbao",
        "Atlético Madrid": "Ath Madrid",
        "CA Osasuna": "Osasuna",
        "Celta Vigo": "Celta",
        "Deportivo La Coruña": "La Coruna",
        "Espanyol": "Espanol",
        "Málaga": "Malaga",
        "Rayo Vallecano": "Vallecano",
        "Real Betis": "Betis",
        "Real Racing Club de Santander": "Santander",
        "Real Sociedad": "Sociedad",
    },
    "SP2": {
        "Girona FC": "Girona",
        "Real Sociedad B": "Sociedad B",
        "Real Valladolid CF": "Valladolid",
    },
    # Italia
    "I1": {
        "AS Roma": "Roma",
        "Atalanta BC": "Atalanta",
        "Inter Milan": "Inter",
        "Hellas Verona": "Verona",
    },
    "I2": {
        "US Catanzaro 1929": "Catanzaro",
        "Hellas Verona": "Verona",
    },
    # Alemanha
    "D1": {
        "1. FC Köln": "FC Koln",
        "Bayer Leverkusen": "Leverkusen",
        "Borussia Dortmund": "Dortmund",
        "Borussia Mönchengladbach": "M'gladbach",
        "Borussia Monchengladbach": "M'gladbach",
        "Eintracht Frankfurt": "Ein Frankfurt",
        "FSV Mainz 05": "Mainz",
        "Hamburger SV": "Hamburg",
        "TSG Hoffenheim": "Hoffenheim",
        "VfB Stuttgart": "Stuttgart",
    },
    # Franca
    "F1": {
        "AS Monaco": "Monaco",
        "Paris Saint Germain": "Paris SG",
        "RC Lens": "Lens",
    },
    # Holanda
    "N1": {
        "FC Twente Enschede": "Twente",
        "Fortuna Sittard": "For Sittard",
        "NEC Nijmegen": "Nijmegen",
    },
    # Grecia
    "G1": {
        "AE Kifisia FC": "Kifisia",
        "AEK Athens": "AEK",
        "Aris Thessaloniki": "Aris",
        "Atromitos Athens": "Atromitos",
        "Levadiakos": "Levadeiakos",
        "Olympiakos Piraeus": "Olympiakos",
        "PAOK Thessaloniki": "PAOK",
        "Panetolikos Agrinio": "Panetolikos",
        "Volos FC": "Volos NFC",
    },
    # Austria
    "AUT": {
        "Austria Lustenau": "A. Lustenau",
        "Austria Wien": "Austria Vienna",
        "RB Salzburg": "Salzburg",
        "Rapid Wien": "SK Rapid",
        "Rheindorf Altach": "Altach",
        "WSG Tirol": "Tirol",
    },
    # Dinamarca
    "DNK": {
        "AGF Aarhus": "Aarhus",
        "Brondby IF": "Brondby",
        "OB Odense BK": "Odense",
        "Silkeborg IF": "Silkeborg",
        "Viborg FF": "Viborg",
    },
    # Noruega
    "NOR": {
        "Bodø/Glimt": "Bodo/Glimt",
        "Fredrikstad FK": "Fredrikstad",
        "IK Start": "Start",
        "KFUM": "KFUM Oslo",
        "Kristiansund BK": "Kristiansund",
        "SK Brann": "Brann",
        "Sarpsborg FK": "Sarpsborg 08",
        "Viking FK": "Viking",
    },
    # Irlanda
    "IRL": {
        "Galway United": "Galway",
        "Shelbourne Dublin": "Shelbourne",
    },
    # Mexico
    "MEX": {
        "Atlético San Luis": "Atl. San Luis",
        "Guadalajara": "Guadalajara Chivas",
        "Pumas": "UNAM Pumas",
        "Tigres": "Tigres UANL",
    },
    # Argentina
    "ARG": {
        "Aldosivi Mar del Plata": "Aldosivi",
        "Argentinos Juniors": "Argentinos Jrs",
        "Atlético Huracán": "Huracan",
        "Atlético Tucuman": "Atl. Tucuman",
        "Belgrano de Cordoba": "Belgrano",
        "CA Tigre BA": "Tigre",
        "Deportivo Riestra": "Dep. Riestra",
        "Estudiantes": "Estudiantes L.P.",
        "Estudiantes de Río Cuarto": "Estudiantes Rio Cuarto",
        "Gimnasia La Plata": "Gimnasia L.P.",
        "Independiente Rivadavia": "Ind. Rivadavia",
        "Instituto de Córdoba": "Instituto",
        "Sarmiento de Junin": "Sarmiento Junin",
        "Talleres": "Talleres Cordoba",
        "Union Santa Fe": "Union de Santa Fe",
        "Velez Sarsfield BA": "Velez Sarsfield",
    },
    # Brasil (4 novas; Atletico Mineiro ja existe)
    "BRA": {
        "Atletico Paranaense": "Athletico-PR",
        "Botafogo": "Botafogo RJ",
        "Flamengo": "Flamengo RJ",
        "Vasco da Gama": "Vasco",
    },
    # EUA
    "USA": {
        "Atlanta United FC": "Atlanta Utd",
        "D.C. United": "DC United",
        "LA Galaxy": "Los Angeles Galaxy",
    },
}


def main() -> int:
    c = FootballDataClient()

    # corpus global: todos os nomes de time de todos os CSVs
    global_norm: dict[str, str] = {}
    for path in sorted(c.main_dir.glob("*.csv")):
        division = path.stem.split("_", 1)[1]
        text = path.read_text(encoding="utf-8")
        for row in csv.DictReader(io.StringIO(text)):
            for col in ("HomeTeam", "AwayTeam"):
                name = (row.get(col) or "").strip()
                if name:
                    global_norm.setdefault(normalize_team(name), name)
    for path in sorted(c.extra_dir.glob("*.csv")):
        code = path.stem
        text = path.read_text(encoding="utf-8")
        for row in csv.DictReader(io.StringIO(text)):
            for col in ("Home", "Away"):
                name = (row.get(col) or "").strip()
                if name:
                    global_norm.setdefault(normalize_team(name), name)

    bad_target = []
    for division, entries in DRAFT.items():
        for provider_name, fduk_name in entries.items():
            target = global_norm.get(normalize_team(fduk_name))
            if target is None:
                bad_target.append((division, provider_name, fduk_name))
    if bad_target:
        print("ALVOS INEXISTENTES NO CORPUS (corrigir antes de gravar):")
        for division, provider_name, fduk_name in bad_target:
            print(f"  {division}: {provider_name!r} -> {fduk_name!r}")
        return 1

    # eventos do fallback: quantos times continuam sem match global?
    payload = json.loads((c.fixtures_dir / "oddsapi.json").read_text(encoding="utf-8"))
    events = payload["events"]
    from betgsn.providers import SPORT_KEY_TO_DIVISIONS

    still_missing = set()
    resolved = 0
    for ev in events:
        divisions = SPORT_KEY_TO_DIVISIONS.get(ev.get("sport_key", ""), [])
        if len(divisions) != 1:
            continue
        division = divisions[0]
        for side in ("home_team", "away_team"):
            name = (ev.get(side) or "").strip()
            if not name:
                continue
            alias = DRAFT.get(division, {}).get(name)
            effective = alias or name
            if normalize_team(effective) in global_norm:
                resolved += 1
            else:
                still_missing.add(f"{division}:{name}")
    print(f"times em eventos : {resolved + len(still_missing)}")
    print(f"resolvidos       : {resolved}")
    print(f"ainda sem match  : {len(still_missing)}")
    for name in sorted(still_missing):
        print(f"  - {name}")

    n_entries = sum(len(v) for v in DRAFT.values())
    print(f"\naliases no rascunho: {n_entries}")

    if "--write" not in sys.argv:
        print("VALIDACAO OK — rode com --write para gravar em team_aliases.json")
        return 0

    from betgsn.team_aliases import load_team_aliases

    aliases_path = Path(__file__).resolve().parent.parent / "betgsn" / "team_aliases.json"
    payload = json.loads(aliases_path.read_text(encoding="utf-8"))
    existing = {
        (e["division"], e["provider"]): e for e in payload.get("aliases", [])
    }

    provenance = (
        "curadoria manual 2026-09-22: import fallback The Odds API "
        "(fixtures futuras 2026-09-23 a 2026-11) confrontado com os CSVs "
        "FDUK 2526; identidade do clube conferida manualmente e alvo "
        "validado contra o corpus"
    )
    added = 0
    for division, entries in DRAFT.items():
        for provider_name, fduk_name in entries.items():
            key = (division, provider_name)
            if key in existing:
                continue
            payload["aliases"].append({
                "division": division,
                "provider": provider_name,
                "fduk": fduk_name,
                "provenance": provenance,
            })
            added += 1

    aliases_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"VALIDACAO OK — {added} aliases novos gravados em {aliases_path.name}")
    # o carregador valida o arquivo inteiro: recusa duplicatas/erros
    merged = load_team_aliases(aliases_path)
    print(f"tabela final carregavel: {len(merged)} aliases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
