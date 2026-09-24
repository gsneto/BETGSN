"""conftest — isolamento global da suíte de testes.

REGRA: nenhum teste pode escrever no ``output/`` de produção.

Este conftest define ``BETGSN_OUTPUT_DIR`` para um diretório temporário
ANTES de qualquer import de ``betgsn`` (pytest carrega o conftest antes
dos módulos de teste). Todos os caminhos default de saída — banco de
backtests, store operacional de odds, caches — são resolvidos de forma
lazy por ``betgsn.config.output_root()`` e, portanto, seguem o override.

Caches SOMENTE LEITURA que os testes de rotas da API leem são copiados
para o diretório temporário (fixtures football-data.co.uk, cache de odds
históricas, cache de validação da estratégia). Bancos de ESCRITA —
``betgsn_backtest.db`` e ``odds_snapshots.db`` — NÃO são copiados: nascem
vazios no diretório temporário.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PROD_OUTPUT = _REPO / "output"

#: Diretório temporário da sessão. `mkdtemp` garante unicidade entre runs.
TMP_OUTPUT = Path(tempfile.mkdtemp(prefix="betgsn-tests-"))
os.environ["BETGSN_OUTPUT_DIR"] = str(TMP_OUTPUT)

#: O REALTIME ENGINE nunca sobe dentro da suíte de testes: o autostart do
#: lifespan chamaria providers REAIS (rede, creditos). Os testes do
#: terminal injetam engines construidos com providers fake.
os.environ["BETGSN_REALTIME"] = "0"

#: Caches de leitura copiados. Tudo que fica de fora nasce vazio — em
#: particular os bancos SQLite de escrita, que nunca devem apontar para
#: o `output/` real durante os testes.
_READ_ONLY_CACHES = (
    "football_data_uk",
    "backtest_cache",
    "value_validation.json",
)


def _copy_read_only_caches() -> None:
    for name in _READ_ONLY_CACHES:
        src = _PROD_OUTPUT / name
        dst = TMP_OUTPUT / name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


_copy_read_only_caches()


def pytest_sessionfinish(session, exitstatus):
    """Remove o diretório temporário da sessão."""
    shutil.rmtree(TMP_OUTPUT, ignore_errors=True)
