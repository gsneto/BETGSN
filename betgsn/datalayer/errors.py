"""BETGSN :: datalayer.errors — taxonomia de falhas de fonte de dados.

Uma fonte externa falha de várias formas: 401, 403, 404, 429, timeout,
conexão caiu, 5xx, quota esgotada, cobertura inexistente, resposta
incompleta. Tratar tudo como "erro genérico" leva a dois extremos ruins:
retentar o que nunca vai funcionar (401) ou desistir do que se recuperaria
(429/timeout).

Aqui a falha é classificada em `ErrorKind`, e cada tipo sabe se é
retentável. Nenhuma mensagem carrega credencial — o redactor de
`providers`/`backtest_sources` já limpa a origem, e `SourceError` ainda
passa a mensagem por uma segunda limpeza defensiva.
"""

from __future__ import annotations

import re
from enum import Enum

__all__ = [
    "ErrorKind",
    "SourceError",
    "classify_status",
    "classify_exception",
    "classify_message",
    "is_retryable",
]


class ErrorKind(str, Enum):
    """Tipo de falha. Determina a política de retry e de fallback."""

    AUTH = "AUTH"                # 401 — chave ausente/inválida
    FORBIDDEN = "FORBIDDEN"      # 403 — plano sem acesso ao recurso
    NOT_FOUND = "NOT_FOUND"      # 404 — recurso inexistente
    RATE_LIMIT = "RATE_LIMIT"    # 429 — curto prazo, retentável com espera
    TIMEOUT = "TIMEOUT"          # socket demorou demais
    CONNECTION = "CONNECTION"    # rede caiu / DNS / SSL
    SERVER = "SERVER"            # 5xx — falha do provedor, retentável
    QUOTA = "QUOTA"              # cota esgotada (diária/mensal), não retentável hoje
    NO_COVERAGE = "NO_COVERAGE"  # a fonte simplesmente não cobre esse dado
    INCOMPLETE = "INCOMPLETE"    # 2xx mas resposta vazia/truncada/malformada
    UNKNOWN = "UNKNOWN"


#: Só estes tipos justificam nova tentativa automática e limitada.
_RETRYABLE = frozenset(
    {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.CONNECTION, ErrorKind.SERVER}
)

_HTTP_RE = re.compile(r"HTTP\s+(\d{3})", re.IGNORECASE)

_QUOTA_HINTS = (
    "quota",
    "out of requests",
    "requests limit",
    "no credits",
    "credit limit",
    "daily limit",
    "monthly limit",
)
_RATE_HINTS = ("rate limit", "too many requests", "slow down")
_COVERAGE_HINTS = (
    "not supported",
    "not available for",
    "no coverage",
    "unsupported",
    "não suportado",
)
_INCOMPLETE_HINTS = ("empty response", "truncated", "malformed", "resposta curta")


def is_retryable(kind: "ErrorKind") -> bool:
    return kind in _RETRYABLE


def classify_status(status_code: int) -> ErrorKind:
    """Mapeia um status HTTP para `ErrorKind`."""
    if status_code == 401:
        return ErrorKind.AUTH
    if status_code == 403:
        return ErrorKind.FORBIDDEN
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 429:
        return ErrorKind.RATE_LIMIT
    if 500 <= status_code <= 599:
        return ErrorKind.SERVER
    if 400 <= status_code <= 499:
        # 400/422 costumam ser pedido malformado ou mercado não suportado.
        return ErrorKind.INCOMPLETE
    return ErrorKind.UNKNOWN


def classify_message(message: str, default: ErrorKind = ErrorKind.UNKNOWN) -> ErrorKind:
    """Extrai o tipo de falha de uma mensagem de erro textual.

    Usado porque `providers.ProviderError` chega como string
    ("HTTP 429 em <url>"). A ordem importa: cota antes de rate limit,
    cobertura antes de incompleto.
    """
    text = (message or "").lower()
    match = _HTTP_RE.search(text)
    if match:
        return classify_status(int(match.group(1)))
    if any(h in text for h in _QUOTA_HINTS):
        return ErrorKind.QUOTA
    if any(h in text for h in _RATE_HINTS):
        return ErrorKind.RATE_LIMIT
    if any(h in text for h in _COVERAGE_HINTS):
        return ErrorKind.NO_COVERAGE
    if any(h in text for h in _INCOMPLETE_HINTS):
        return ErrorKind.INCOMPLETE
    return default


class SourceError(Exception):
    """Falha de uma fonte de dados, já classificada e sanitizada."""

    def __init__(
        self,
        kind: ErrorKind,
        source: str,
        message: str,
        *,
        status_code: int | None = None,
        detail: str = "",
    ) -> None:
        self.kind = kind
        self.source = source
        self.message = _sanitize(message)
        self.status_code = status_code
        self.detail = _sanitize(detail)
        self.retryable = is_retryable(kind)
        super().__init__(self._render())

    def _render(self) -> str:
        code = f" (HTTP {self.status_code})" if self.status_code else ""
        return f"[{self.source}] {self.kind.value}{code}: {self.message}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self._render()


#: Segunda linha de defesa: nunca deixar credencial sair numa mensagem.
_CREDENTIAL_RE = re.compile(r"(api_?key\s*[=:]\s*)[^\s&\"',}]+", re.IGNORECASE)


def _sanitize(text: str) -> str:
    return _CREDENTIAL_RE.sub(r"\1***", text or "")


def classify_exception(exc: BaseException, source: str = "?") -> SourceError:
    """Converte qualquer exceção numa `SourceError` classificada.

    Exceções que já são `SourceError` passam inalteradas (idempotente).
    """
    if isinstance(exc, SourceError):
        return exc

    status_code: int | None = None
    kind: ErrorKind | None = None

    # urllib.error.HTTPError tem .code; evita import pesado no topo.
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        status_code = code
        kind = classify_status(code)

    if kind is None:
        name = type(exc).__name__.lower()
        if isinstance(exc, TimeoutError) or "timeout" in name:
            kind = ErrorKind.TIMEOUT
        elif isinstance(exc, (ConnectionError, OSError)) or "urlerror" in name:
            kind = ErrorKind.CONNECTION
        elif "json" in name and "decode" in name:
            kind = ErrorKind.INCOMPLETE
        else:
            kind = classify_message(str(exc))

    detail = str(exc)
    return SourceError(kind, source, detail, status_code=status_code)
