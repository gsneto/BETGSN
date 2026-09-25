"""BETGSN realtime smoke — checa contratos vivos do backend e do frontend.

Uso:

    python tools/realtime_smoke.py --api http://127.0.0.1:8787 --web http://localhost:5180

O script NAO decide, NAO promove estrategia e NAO faz apostas. Apenas
verifica que:

  1. o backend responde nas rotas contratuais (/status, /board, /match,
     /priced-signals, /providers, /signals) com o schema esperado;
  2. o board carrega priced_signals + policy_fingerprint (integracao live);
  3. o SSE /stream emite pelo menos um heartbeat/evento durante a janela
     de escuta (default 10s);
  4. o frontend responde 200 em GET / (Vite dev ou build servido).

Falhas geram exit != 0 com mensagem legivel. Sem rede/entrada -> apenas
constata a ausencia (n=0), NUNCA fabrica dado.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


REQUIRED_BOARD_KEYS = {
    "generated_at", "events", "signals", "priced_signals",
    "policy_fingerprint", "last_moves", "problems", "boot",
}


def _get(url: str, timeout: float = 10.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    if not body:
        return resp.status, {}
    return resp.status, json.loads(body.decode("utf-8"))


def check_backend(api_base: str) -> list[str]:
    """Valida o contrato do backend; devolve lista de falhas."""
    failures: list[str] = []
    try:
        status_code, status = _get(f"{api_base}/api/realtime/status")
    except Exception as exc:
        return [f"/status inacessivel: {exc}"]
    if status_code != 200:
        failures.append(f"/status HTTP {status_code}")
    if "engine" not in status and "boot" not in status:
        failures.append("/status sem chaves 'engine'/'boot'")

    try:
        _, board = _get(f"{api_base}/api/realtime/board")
    except Exception as exc:
        return failures + [f"/board inacessivel: {exc}"]
    missing = REQUIRED_BOARD_KEYS - set(board)
    if missing:
        failures.append(f"/board sem chaves: {sorted(missing)}")
    if not isinstance(board.get("priced_signals"), list):
        failures.append("/board.priced_signals deve ser lista")
    if not isinstance(board.get("policy_fingerprint"), str) or not board["policy_fingerprint"]:
        failures.append("/board.policy_fingerprint ausente ou vazio")

    try:
        _, priced = _get(f"{api_base}/api/realtime/priced-signals")
    except Exception as exc:
        failures.append(f"/priced-signals inacessivel: {exc}")
    else:
        for key in ("generated_at", "policy_fingerprint", "priced_signals",
                    "execution_erosion"):
            if key not in priced:
                failures.append(f"/priced-signals sem '{key}'")

    events = board.get("events") or []
    if events:
        event_key = events[0].get("event_key")
        if event_key:
            try:
                qs = urllib.parse.urlencode({"event_key": event_key})
                _, match = _get(
                    f"{api_base}/api/realtime/match?{qs}"
                )
            except Exception as exc:
                failures.append(f"/match inacessivel: {exc}")
            else:
                for key in ("event", "signals", "priced_signals",
                            "execution_diagnostics", "execution_erosion",
                            "movement_timeline", "clv", "problems"):
                    if key not in match:
                        failures.append(f"/match sem '{key}'")

    try:
        _, providers = _get(f"{api_base}/api/realtime/providers")
    except Exception as exc:
        failures.append(f"/providers inacessivel: {exc}")
    else:
        if "providers" not in providers:
            failures.append("/providers.providers ausente")

    try:
        _, signals = _get(f"{api_base}/api/realtime/signals")
    except Exception as exc:
        failures.append(f"/signals inacessivel: {exc}")
    else:
        if not isinstance(signals.get("signals"), list):
            failures.append("/signals.signals deve ser lista")

    return failures


def check_sse(api_base: str, seconds: float) -> list[str]:
    """Escuta o SSE por N segundos; sucesso se recebeu ao menos 1 chunk.

    Ausencia -> registra ausencia; nao inventa evento. Frame de conexao
    (`event: connected`) ja conta.
    """
    url = f"{api_base}/api/realtime/stream"
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=seconds + 3) as resp:
            if resp.status != 200:
                return [f"/stream HTTP {resp.status}"]
            if "text/event-stream" not in resp.headers.get(
                "content-type", "text/event-stream"
            ):
                return [f"/stream content-type {resp.headers.get('content-type')}"]
            deadline = started + seconds
            saw_chunk = False
            while time.monotonic() < deadline:
                chunk = resp.readline()
                if chunk:
                    saw_chunk = True
                    break
        return [] if saw_chunk else ["/stream nao emitiu chunk dentro da janela"]
    except urllib.error.URLError as exc:
        return [f"/stream falhou: {exc}"]
    except Exception as exc:  # pragma: no cover - defensivo
        return [f"/stream erro: {exc}"]


def check_frontend(web_base: str) -> list[str]:
    try:
        req = urllib.request.Request(web_base)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
    except Exception as exc:
        return [f"frontend inacessivel: {exc}"]
    if not body:
        return ["frontend respondeu vazio"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8787")
    parser.add_argument("--web", default="http://localhost:5180")
    parser.add_argument("--sse-seconds", type=float, default=10.0)
    parser.add_argument("--skip-sse", action="store_true")
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args(argv)

    failures: list[str] = []
    print(f"[backend] {args.api}")
    failures.extend(check_backend(args.api))

    if not args.skip_sse:
        print(f"[sse] escutando {args.sse_seconds}s...")
        failures.extend(check_sse(args.api, args.sse_seconds))

    if not args.skip_web:
        print(f"[web] {args.web}")
        failures.extend(check_frontend(args.web))

    if failures:
        print("\nFALHAS DE SMOKE:")
        for msg in failures:
            print(f"  - {msg}")
        return 2
    print("\nOK — contratos do realtime atendidos.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
