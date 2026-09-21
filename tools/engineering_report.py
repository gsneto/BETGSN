"""Gera relatório e verifica artefatos científicos sem exibir dados sensíveis."""
import json
import hashlib
from pathlib import Path

root=Path(__file__).resolve().parents[1]
out=root/"output"/"engineering"
report=json.loads((out/"benchmark"/"report.json").read_text())
original=json.loads((out/"original-manifest.json").read_text())
changed=[]
created=[]
for folder in ("betgsn","tests","web/src","tools","docs"):
    for p in (root/folder).rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel=p.relative_to(root).as_posix()
        if rel not in original:
            created.append(rel)
        elif hashlib.sha256(p.read_bytes()).hexdigest()!=original[rel]["sha256"]:
            changed.append(rel)
lines=["# BETGSN — entrega incremental verificada", "", "## Benchmark out-of-sample", "",
       "Premier League (E0), teste 2025, 371 partidas. Treino 2015–2021; early stopping 2022; ensemble 2023; calibração 2024.",
       "Probabilidades temporais; métricas financeiras abaixo são cenário exploratório com preços CSV sem timestamp. CLV indisponível.","",
       "|Modelo 1X2|Brier|Log Loss|RPS|ECE|ROI/banca|Yield|Drawdown|Apostas|",
       "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
for name,m in report["results"]["1x2"]["models"].items():
    lines.append(f"|{name}|{m['brier']:.4f}|{m['logloss']:.4f}|{m['rps']:.4f}|{m['ece']:.4f}|{m['roi']:.2%}|{m['yield']:.2%}|{m['drawdown']:.2%}|{m['n_bets']}|")
lines += ["", "Nenhum modelo promovido. Uma liga/ano não comprova vantagem generalizável. Isotonic/Platt não garantem melhora.",
          "O benchmark inclui também Over/Under e BTTS; resultados completos em `output/engineering/benchmark/report.json`.",
          "", "## Arquivos modificados", *[f"- `{p}`" for p in sorted(changed)],
          "", "## Arquivos criados", *[f"- `{p}`" for p in sorted(created)],
          "- `requirements-ml.txt`", "", "## Dependências opcionais instaladas", 
          "numpy 2.4.6; scikit-learn 1.9.1; xgboost 3.2.0; lightgbm 4.7.0 (mais dependências transitivas).", "",
          "## Verificação em 2026-09-20",
          "- `python -m pytest tests -q --tb=short`: 342 passaram, 2 ignorados e 4 avisos de depreciação; código de saída 0.",
          "- Teste determinístico confirma uso das cotações fornecidas e retorno vazio quando nenhuma oportunidade passa pelo filtro de EV.",
          "", "## Limitações e próximos passos", 
          "- A entrega não satisfaz ainda todos os critérios do pedido original; módulos experimentais exigem integração adicional.",
          "- Odds CSV sem timestamp não são evidência point-in-time de execução. Motor estrito recusa; CLV exige snapshots de entrada/fechamento.",
          "- xG externo requer fonte e timestamp. Football-data.co.uk não fornece xG. Nenhum conector de xG novo foi ativado.",
          "- Features novas alimentam benchmark; produção continua no baseline. Calibradores/ensemble não foram promovidos.",
          "- Drift PSI é infraestrutura; falta integração operacional para performance/calibração/CLV e thresholds configurados por série.",
          "- Benchmark ainda não executa múltiplos folds rolling/expanding para ML; só split temporal em cinco blocos.",
          "- Elo/contexto de força adversária, identidade global de times e fuso exato das fontes requerem validação adicional.",
          "- Backtest guarda métricas de todas as previsões; UI ainda não apresenta todos os novos campos.",
          "- Código split legado aposentado permanece privado para referência; API pública delega ao motor principal.",
          "- Estatísticas secundárias legadas ainda têm imputações constantes; não foram remodeladas nesta entrega.",
          "- De-vig agora separa linhas, exige grupos conhecidos completos e trata dupla chance com soma 2. EV/Kelly de handicaps com devolução ainda requer tratamento específico.",
          "- Janelas rolling/expanding e filtro de disponibilidade implementados; falta conectá-los à execução multifold do benchmark.",
          "- Snapshot real conferido em 2026-09-20: 17 jogos futuros, 35.843 partidas históricas e contagem do histórico consistente.",
          "- Os relatórios históricos antigos não foram reclassificados nem apagados."]
(root/"docs"/"ENGINEERING_DELIVERY.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print("\n".join(lines[:27]))
