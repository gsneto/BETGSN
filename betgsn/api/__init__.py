"""BETGSN :: api — camada HTTP/WebSocket sobre o pipeline existente.

Esta camada NAO calcula nada. Ela apenas:
  - executa o pipeline (betgsn.pipeline.run) com os parametros recebidos;
  - serializa o RunResult em contratos tipados (schemas.py);
  - expoe tudo por HTTP/WebSocket para a interface React.

A fonte de verdade estatistica continua em engine/model/markets/signals.
"""

__all__ = ["schemas", "service", "server"]
