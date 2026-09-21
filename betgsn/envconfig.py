"""BETGSN :: envconfig — localizacao segura do arquivo `.env`.

Por que este modulo existe
--------------------------
O `.env` real fica na raiz do repositorio principal e e ignorado pelo Git.
Quando o codigo roda de um *linked worktree* (ex.: `BETGSN-data`), o `.env`
nao existe ali: ele vive no worktree principal (`BETGSN/.env`). Sem isso,
`providers.load_env_file()` nao acha nada e todo provider aparece como
"sem chave" — mesmo com as credenciais configuradas.

A solucao NAO duplica chaves. Ela apenas localiza, em ordem de prioridade:

1. caminho explicito passado pelo chamador;
2. `BETGSN_ENV_FILE` (override do operador);
3. `<raiz do worktree atual>/.env`;
4. diretorios pais de (3), subindo — cobre `monorepo/sub/projeto`;
5. a raiz do worktree PRINCIPAL do Git, descoberta pelo ponteiro `.git`
   de um worktree ligado (`gitdir:` + `commondir`).

Nunca le valores de dentro de `.git`; so resolve caminhos. Nenhuma chave e
impressa ou copiada.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Nome padrao do arquivo local de credenciais.
ENV_FILENAME = ".env"


def repo_root() -> Path:
    """Raiz do pacote `betgsn` (pai do diretorio deste arquivo)."""
    return Path(__file__).resolve().parent.parent


def _linked_worktree_main_root(root: Path) -> Path | None:
    """Raiz do repositorio principal quando `root` e um linked worktree.

    Um linked worktree tem `.git` como ARQUIVO (nao diretorio) contendo
    `gitdir: <caminho>`. Dentro desse `gitdir` existe `commondir`
    apontando para o `.git` compartilhado, cujo pai e a raiz principal.

    Tudo e envolto em try/except: qualquer formato inesperado devolve None
    em vez de quebrar a importacao do pacote.
    """
    marker = root / ".git"
    try:
        if not marker.is_file():
            return None
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if not text.lower().startswith("gitdir:"):
            return None
        raw = text.split(":", 1)[1].strip()
        gitdir = Path(raw)
        if not gitdir.is_absolute():
            gitdir = (root / gitdir).resolve()
        commondir = gitdir / "commondir"
        if not commondir.is_file():
            return None
        common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
        if common.name == ".git":
            return common.parent
    except (OSError, ValueError, IndexError):
        return None
    return None


def env_file_candidates(root: Path | None = None) -> list[Path]:
    """Caminhos candidatos para o `.env`, em ordem de prioridade.

    Inclui o override `BETGSN_ENV_FILE`, a raiz do worktree, seus pais e a
    raiz do worktree principal (se houver). Caminhos duplicados sao
    removidos preservando a ordem.
    """
    base = Path(root).resolve() if root else repo_root()
    ordered: list[Path] = []

    override = os.environ.get("BETGSN_ENV_FILE", "").strip()
    if override:
        ordered.append(Path(override))

    ordered.append(base / ENV_FILENAME)
    ordered.extend(parent / ENV_FILENAME for parent in base.parents)

    main_root = _linked_worktree_main_root(base)
    if main_root is not None:
        ordered.append(main_root / ENV_FILENAME)

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in ordered:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(candidate)
    return unique


def resolve_env_file(root: Path | None = None) -> Path | None:
    """Primeiro `.env` existente entre os candidatos, ou None.

    Nao cria arquivo nenhum: ausencia de `.env` e um estado valido (o
    ambiente do sistema pode carregar as chaves).
    """
    for candidate in env_file_candidates(root):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None
