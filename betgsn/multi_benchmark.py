"""BETGSN :: multi_benchmark — o mesmo benchmark temporal, em varias ligas.

Por que este modulo existe
--------------------------
`benchmark.run_benchmark` roda o pipeline completo (train -> early-stop ->
ensemble -> calibracao -> teste) numa UNICA divisao. Um resultado assim
responde "o modelo bateu o baseline na Premier League em 2025?" — que nao
e a pergunta que interessa.

A pergunta que interessa e se a vantagem SOBREVIVE fora da liga onde foi
observada. Um modelo pode vencer no agregado e ainda assim perder na
maioria das ligas individuais: basta um acerto grande num campeonato de
idiossincrasia forte (mando pesado, poucos times, variancia alta) puxar a
media. Isso nao e vantagem, e overfit a uma liga — e o agregado esconde
exatamente isso. Este modulo existe para expor esse caso.

Por isso o relatorio nunca reporta so a media. Reporta media, dispersao
(desvio populacional, minimo, maximo), quantas ligas o modelo de fato
venceu, e `stability` = fracao de ligas em que venceu o baseline em
logloss. Media boa com `stability` baixa e um sinal de alerta, nao de
aprovacao.

Honestidade sobre o gate de promocao
------------------------------------
Os resultados alimentam `models.promotion.evaluate_promotion`, que e
deliberadamente mais duro do que a evidencia que este modulo produz: uma
rodada tem UM ano de teste, entao o criterio `multiplas_temporadas`
(minimo 2) tende a reprovar. Isso e proposital e nao deve ser contornado
aqui. Rodar varias ligas aumenta a largura da evidencia, nao a
profundidade temporal dela.

Limites conhecidos
------------------
- Nao reimplementa modelagem: delega tudo a `run_benchmark`. Se aquele
  pipeline tiver vies, este modulo o replica em N ligas.
- Ligas nao sao independentes (mesmos periodos, mesmo mercado global),
  entao o desvio entre ligas subestima a incerteza real.
- O pre-filtro de amostra conta partidas por ANO CALENDARIO no cache CSV,
  o que e uma aproximacao do tamanho real da janela de teste. A janela
  efetiva e reconferida depois da execucao.
- Metricas financeiras do benchmark continuam exploratorias (odds CSV sem
  timestamp de publicacao) e NAO entram na agregacao nem no gate.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .benchmark import run_benchmark
from .football_data_uk import MAIN_DIVISIONS, FootballDataClient, league_label
from .models.promotion import ModelStatus, SegmentResult, evaluate_promotion

#: Partidas minimas no periodo [start_year, test_year] para a liga entrar.
#: Abaixo disso o pipeline ou quebra nos blocos temporais ou produz
#: estimativas ruidosas demais para contarem como evidencia.
MIN_MATCHES_PER_LEAGUE = 800
#: Partidas minimas no ano de teste. Sem isso a diferenca de logloss entre
#: modelos fica dentro do ruido amostral.
MIN_MATCHES_TEST = 150

#: Metricas probabilisticas agregadas. Financeiro fica de fora de proposito.
AGGREGATED_METRICS = ("logloss", "brier", "rps", "ece")
#: Metrica de decisao: e a que o gate de promocao usa como primaria.
PRIMARY_METRIC = "logloss"
#: Nome do baseline dentro do relatorio de `run_benchmark`.
BASELINE_NAME = "BASELINE_V1"
#: Separador entre mercado e variante na chave de modelo ("1x2|XGBoost").
MODEL_KEY_SEP = "|"


# --------------------------------------------------------------------------
# Estruturas
# --------------------------------------------------------------------------


@dataclass
class LeagueBenchmark:
    """Resultado de UMA liga, ja reduzido ao que a agregacao consome.

    `models` mapeia a chave "mercado|variante" para o dicionario de
    metricas probabilisticas daquela variante no bloco de teste. O baseline
    fica no mesmo dicionario, sob "mercado|BASELINE_V1" — comparar variante
    com o baseline do MESMO mercado e obrigatorio: logloss de 1x2 (3
    classes) e de btts (2 classes) nao sao comparaveis entre si.
    """

    division: str
    n_matches: int
    n_test: int
    models: dict[str, dict[str, float]] = field(default_factory=dict)
    data_version: str = ""
    windows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def league(self) -> str:
        return league_label(self.division)

    def baseline_for(self, model_key: str) -> dict[str, float] | None:
        """Metricas do baseline do mesmo mercado do modelo informado."""
        market = model_key.split(MODEL_KEY_SEP, 1)[0]
        return self.models.get(f"{market}{MODEL_KEY_SEP}{BASELINE_NAME}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "division": self.division,
            "league": self.league,
            "n_matches": self.n_matches,
            "n_test": self.n_test,
            "data_version": self.data_version,
            "windows": self.windows,
            "models": self.models,
        }


# --------------------------------------------------------------------------
# Extracao e pre-filtro
# --------------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    """Float finito ou None. Protege o JSON e as estatisticas de NaN/inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def extract_model_metrics(report: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Reduz o relatorio de `run_benchmark` a {"mercado|variante": metricas}.

    Descarta o bloco financeiro (exploratorio) e o intervalo de confianca
    por bloco mensal — este ultimo e por liga e nao se agrega entre ligas
    sem hipotese adicional que nao temos.
    """
    out: dict[str, dict[str, float]] = {}
    for market, block in (report.get("results") or {}).items():
        for variant, metrics in (block.get("models") or {}).items():
            values = {
                name: value
                for name in AGGREGATED_METRICS
                if (value := _finite(metrics.get(name))) is not None
            }
            if values:
                out[f"{market}{MODEL_KEY_SEP}{variant}"] = values
    return out


def league_sample_sizes(
    division: str,
    start_year: int,
    test_year: int,
    client: FootballDataClient | None = None,
) -> tuple[int, int]:
    """(partidas no periodo, partidas no ano de teste) lendo o cache CSV.

    Pre-voo barato: `run_benchmark` leva minutos por liga, entao a liga
    pequena demais e descartada ANTES de gastar esse tempo. Conta por ano
    calendario, que e aproximacao da janela de teste real do benchmark.
    """
    matches = (client or FootballDataClient()).load_matches([division])
    total = test = 0
    for match in matches:
        try:
            year = int(match.date[:4])
        except (TypeError, ValueError):
            continue
        if start_year <= year <= test_year:
            total += 1
        if year == test_year:
            test += 1
    return total, test


# --------------------------------------------------------------------------
# Agregacao entre ligas
# --------------------------------------------------------------------------


def _dispersion(values: Sequence[float]) -> dict[str, float | None]:
    """Media, desvio, minimo e maximo. Desvio de 1 elemento e None.

    `pstdev` de uma lista unitaria retorna 0.0, o que leria como "sem
    dispersao" quando o certo e "dispersao desconhecida". Uma liga so nao
    mede estabilidade nenhuma.
    """
    data = [float(v) for v in values]
    if not data:
        return {"mean": None, "stdev": None, "min": None, "max": None, "n": 0}
    return {
        "mean": statistics.fmean(data),
        "stdev": statistics.pstdev(data) if len(data) > 1 else None,
        "min": min(data),
        "max": max(data),
        "n": len(data),
    }


def aggregate_across_leagues(
    results: Sequence[LeagueBenchmark],
    model_names: Iterable[str],
) -> dict[str, Any]:
    """Estatisticas de cada modelo ATRAVES das ligas.

    Para cada metrica: media, desvio populacional, minimo e maximo entre
    ligas. Alem disso, e principalmente:

    - `n_leagues_better_than_baseline`: em quantas ligas o modelo teve
      logloss menor que o baseline DAQUELE mercado naquela liga.
    - `stability`: a mesma contagem em fracao. E o numero que desmonta o
      agregado enganoso — media melhor com `stability` <= 0.5 significa que
      a vantagem veio de uma liga, nao do modelo.
    - `mean_relative_improvement`: melhora relativa media vs baseline,
      calculada por liga e depois promediada (nao e a razao das medias).

    Modelos ausentes numa liga simplesmente nao contam ali; `n_leagues`
    informa a base real de cada linha.
    """
    aggregate: dict[str, Any] = {}
    for name in sorted(model_names):
        per_metric: dict[str, dict[str, float | None]] = {}
        for metric in AGGREGATED_METRICS:
            values = [
                league.models[name][metric]
                for league in results
                if metric in league.models.get(name, {})
            ]
            per_metric[metric] = _dispersion(values)

        wins = 0
        compared = 0
        improvements: list[float] = []
        per_league: dict[str, dict[str, float | None]] = {}
        for league in results:
            mine = league.models.get(name, {}).get(PRIMARY_METRIC)
            base = (league.baseline_for(name) or {}).get(PRIMARY_METRIC)
            if mine is None or base is None or base == 0:
                continue
            compared += 1
            better = mine < base
            wins += better
            improvement = (base - mine) / abs(base)
            improvements.append(improvement)
            per_league[league.division] = {
                PRIMARY_METRIC: mine,
                "baseline": base,
                "relative_improvement": improvement,
                "better_than_baseline": better,
            }

        aggregate[name] = {
            "market": name.split(MODEL_KEY_SEP, 1)[0],
            "variant": name.split(MODEL_KEY_SEP, 1)[-1],
            "n_leagues": per_metric[PRIMARY_METRIC]["n"],
            "n_leagues_compared": compared,
            "n_leagues_better_than_baseline": wins,
            "stability": (wins / compared) if compared else None,
            "mean_relative_improvement": (
                statistics.fmean(improvements) if improvements else None
            ),
            "metrics": per_metric,
            "per_league": per_league,
        }
    return aggregate


def _promotion_segments(
    results: Sequence[LeagueBenchmark],
    model_name: str,
    test_year: int,
) -> list[SegmentResult]:
    """Um `SegmentResult` por liga, com o baseline do mesmo mercado.

    `n_matches` e o tamanho da janela de TESTE (nao o corpus inteiro): o
    gate pondera evidencia, e a evidencia esta no teste. `season` e o ano
    de teste — uma rodada so tem um, e o gate vai reprovar por isso.
    """
    segments: list[SegmentResult] = []
    for league in results:
        metrics = league.models.get(model_name)
        baseline = league.baseline_for(model_name)
        if not metrics or not baseline:
            continue
        segments.append(SegmentResult(
            league=league.division,
            season=str(test_year),
            n_matches=league.n_test,
            metrics=dict(metrics),
            baseline_metrics=dict(baseline),
        ))
    return segments


# --------------------------------------------------------------------------
# Orquestrador
# --------------------------------------------------------------------------


def run_multi_benchmark(
    divisions: Sequence[str] = ("E0", "SP1", "I1", "D1", "F1"),
    start_year: int = 2015,
    test_year: int = 2025,
) -> dict[str, Any]:
    """Roda `run_benchmark` em cada divisao e agrega o que sobreviveu.

    Uma liga pode sair do conjunto por tres motivos, e os TRES ficam
    registrados em `skipped` com a razao: codigo desconhecido, amostra
    abaixo do minimo, ou excecao durante a execucao. Nada e descartado em
    silencio — liga que some sem explicacao vira vies de selecao, que e
    justamente o defeito que este modulo deveria denunciar.

    Uma excecao numa liga nao aborta as demais: o traceback e resumido no
    registro e a execucao continua.
    """
    requested = [d.strip() for d in divisions if d and d.strip()]
    client = FootballDataClient()
    results: list[LeagueBenchmark] = []
    skipped: list[dict[str, Any]] = []

    for division in requested:
        if division not in MAIN_DIVISIONS:
            skipped.append({
                "division": division,
                "reason": "codigo fora de MAIN_DIVISIONS",
                "stage": "catalogo",
            })
            continue

        n_matches, n_test = league_sample_sizes(
            division, start_year, test_year, client
        )
        if n_matches < MIN_MATCHES_PER_LEAGUE or n_test < MIN_MATCHES_TEST:
            skipped.append({
                "division": division,
                "league": league_label(division),
                "reason": (
                    f"amostra insuficiente: {n_matches} partidas em "
                    f"{start_year}-{test_year} (minimo {MIN_MATCHES_PER_LEAGUE}) "
                    f"e {n_test} em {test_year} (minimo {MIN_MATCHES_TEST})"
                ),
                "stage": "pre_filtro",
                "n_matches": n_matches,
                "n_test": n_test,
            })
            continue

        print(
            f"[multi] {division}: {n_matches} partidas, {n_test} no teste — rodando",
            flush=True,
        )
        try:
            report, _predictions = run_benchmark(division, start_year, test_year)
        except Exception as exc:  # falha de uma liga nao derruba as outras
            skipped.append({
                "division": division,
                "league": league_label(division),
                "reason": f"{type(exc).__name__}: {exc}",
                "stage": "execucao",
                "traceback": traceback.format_exc(limit=5).splitlines()[-5:],
                "n_matches": n_matches,
                "n_test": n_test,
            })
            print(f"[multi] {division}: FALHOU ({type(exc).__name__}: {exc})", flush=True)
            continue

        windows = report.get("windows") or []
        window_test = windows[-1]["n"] if windows else 0
        models = extract_model_metrics(report)
        if window_test < MIN_MATCHES_TEST:
            skipped.append({
                "division": division,
                "league": league_label(division),
                "reason": (
                    f"janela de teste real com {window_test} partidas "
                    f"(minimo {MIN_MATCHES_TEST})"
                ),
                "stage": "pos_execucao",
                "n_matches": n_matches,
                "n_test": window_test,
            })
            continue
        if not models:
            skipped.append({
                "division": division,
                "league": league_label(division),
                "reason": "relatorio sem metricas utilizaveis",
                "stage": "pos_execucao",
                "n_matches": n_matches,
                "n_test": window_test,
            })
            continue

        results.append(LeagueBenchmark(
            division=division,
            n_matches=sum(int(w.get("n", 0)) for w in windows) or n_matches,
            n_test=window_test,
            models=models,
            data_version=report.get("data_version", ""),
            windows=windows,
        ))

    model_names = sorted({name for league in results for name in league.models})
    aggregate = aggregate_across_leagues(results, model_names)

    promotion: dict[str, Any] = {}
    for name in model_names:
        if name.endswith(MODEL_KEY_SEP + BASELINE_NAME):
            continue  # baseline nao se promove contra si mesmo
        decision = evaluate_promotion(
            model=name,
            segments=_promotion_segments(results, name, test_year),
            current_status=ModelStatus.EXPERIMENTAL,
            primary_metric=PRIMARY_METRIC,
        )
        promotion[name] = decision.to_dict()

    return {
        "kind": "multi_league_benchmark",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "divisions_requested": requested,
        "divisions_used": [league.division for league in results],
        "start_year": start_year,
        "test_year": test_year,
        "criteria": {
            "min_matches_per_league": MIN_MATCHES_PER_LEAGUE,
            "min_matches_test": MIN_MATCHES_TEST,
            "primary_metric": PRIMARY_METRIC,
            "baseline": BASELINE_NAME,
        },
        "leagues": [league.to_dict() for league in results],
        "skipped": skipped,
        "models": aggregate,
        "promotion": promotion,
        "notes": {
            "aggregate_vs_stability": (
                "Media melhor que o baseline com stability <= 0.5 indica que a "
                "vantagem veio de poucas ligas: e idiossincrasia daquela liga, "
                "nao vantagem do modelo. Leia sempre os dois numeros juntos."
            ),
            "promotion_gate": (
                "Uma rodada cobre UM ano de teste, entao o criterio "
                "multiplas_temporadas do gate tende a reprovar. Isso e "
                "esperado: mais ligas ampliam a evidencia em largura, nao em "
                "profundidade temporal."
            ),
            "financial": (
                "ROI/Yield do benchmark por liga sao exploratorios (odds CSV sem "
                "timestamp de publicacao) e ficam FORA da agregacao e do gate."
            ),
            "independence": (
                "Ligas compartilham periodo e mercado global; o desvio entre "
                "ligas subestima a incerteza real."
            ),
        },
    }


# --------------------------------------------------------------------------
# Saida legivel
# --------------------------------------------------------------------------


def _fmt(value: float | None, spec: str = "7.4f") -> str:
    return format(value, spec) if isinstance(value, (int, float)) else "     --"


def format_summary(report: dict[str, Any]) -> str:
    """Tabela de texto com media, dispersao e estabilidade por modelo."""
    lines: list[str] = []
    used = report.get("divisions_used") or []
    lines.append(
        f"Multi-liga | teste {report['test_year']} | "
        f"{len(used)} liga(s): {', '.join(used) if used else 'nenhuma'}"
    )

    for entry in report.get("skipped") or []:
        lines.append(f"  PULADA {entry['division']}: {entry['reason']}")

    header = (
        f"{'modelo':<34}{'ligas':>6}{'logloss':>9}{'stdev':>9}"
        f"{'min':>9}{'max':>9}{'>base':>7}{'estab':>8}  promocao"
    )
    lines.append("")
    lines.append(header)
    lines.append("-" * (len(header) + 12))

    models: dict[str, Any] = report.get("models") or {}
    ordered = sorted(
        models.items(),
        key=lambda kv: (
            kv[1]["market"],
            -(kv[1]["stability"] if kv[1]["stability"] is not None else -1),
            kv[1]["metrics"][PRIMARY_METRIC]["mean"] or 9e9,
        ),
    )
    for name, data in ordered:
        stats = data["metrics"][PRIMARY_METRIC]
        decision = (report.get("promotion") or {}).get(name)
        if decision is None:
            status = "baseline"
        elif decision["promoted"]:
            status = f"-> {decision['recommended_status']}"
        else:
            failures = decision["blocking_failures"]
            status = "mantido" + (f" ({failures[0]})" if failures else "")
        stability = data["stability"]
        lines.append(
            f"{name:<34}{data['n_leagues']:>6}"
            f"{_fmt(stats['mean']):>9}{_fmt(stats['stdev']):>9}"
            f"{_fmt(stats['min']):>9}{_fmt(stats['max']):>9}"
            f"{data['n_leagues_better_than_baseline']:>7}"
            f"{_fmt(stability, '8.2f')}  {status}"
        )

    suspicious = [
        name for name, data in models.items()
        if (data["mean_relative_improvement"] or 0) > 0
        and data["stability"] is not None
        and data["stability"] <= 0.5
        and not name.endswith(MODEL_KEY_SEP + BASELINE_NAME)
    ]
    lines.append("")
    if suspicious:
        lines.append(
            "ALERTA: melhora media positiva com estabilidade <= 0.50 em "
            + ", ".join(sorted(suspicious))
        )
        lines.append(
            "  Vantagem concentrada em poucas ligas: trate como overfit "
            "a idiossincrasia local ate prova em contrario."
        )
    else:
        lines.append("Nenhum modelo com media positiva e estabilidade <= 0.50.")
    lines.append(report["notes"]["promotion_gate"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark temporal replicado em varias ligas, com "
                    "dispersao entre ligas e gate de promocao."
    )
    parser.add_argument(
        "--divisions",
        default="E0,SP1,I1,D1,F1",
        help="codigos separados por virgula (padrao: E0,SP1,I1,D1,F1)",
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--test-year", type=int, default=2025)
    parser.add_argument("--output", default="output/engineering/multi_benchmark")
    args = parser.parse_args()

    report = run_multi_benchmark(
        divisions=args.divisions.split(","),
        start_year=args.start_year,
        test_year=args.test_year,
    )

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(flush=True)
    print(format_summary(report), flush=True)
    print(f"\nrelatorio: {out / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
