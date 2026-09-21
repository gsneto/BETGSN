/**
 * useBetgsnSocket — conexao WebSocket unica da aplicacao.
 *
 * Requisitos atendidos:
 *  - uma unica conexao (ref, nao state: re-render nao reconecta);
 *  - reconexao automatica com backoff exponencial limitado;
 *  - heartbeat para detectar conexao morta sem polling agressivo;
 *  - cleanup completo no unmount (sem vazar socket nem timer).
 *
 * O socket transporta apenas EVENTOS (status, progresso do recalculo).
 * Os dados continuam sendo lidos por HTTP, onde o contrato e tipado.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "@/api/client";
import type { WsMessage } from "@/types/api";

export type SocketState = "connecting" | "open" | "closed";

const RECONNECT_BASE_MS = 800;
const RECONNECT_MAX_MS = 15_000;
const HEARTBEAT_MS = 25_000;

interface Options {
  onEvent?: (message: WsMessage) => void;
  enabled?: boolean;
}

export function useBetgsnSocket({ onEvent, enabled = true }: Options = {}) {
  const [state, setState] = useState<SocketState>("connecting");
  const socketRef = useRef<WebSocket | null>(null);
  const attemptsRef = useRef(0);
  const reconnectTimer = useRef<number | null>(null);
  const heartbeatTimer = useRef<number | null>(null);
  const disposedRef = useRef(false);

  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  const clearTimers = useCallback(() => {
    if (reconnectTimer.current !== null) {
      window.clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (heartbeatTimer.current !== null) {
      window.clearInterval(heartbeatTimer.current);
      heartbeatTimer.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (disposedRef.current) return;
    // nunca abre uma segunda conexao sobre uma existente
    if (
      socketRef.current &&
      (socketRef.current.readyState === WebSocket.OPEN ||
        socketRef.current.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    setState("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl());
    } catch {
      scheduleReconnect();
      return;
    }
    socketRef.current = ws;

    ws.onopen = () => {
      // fechar se a sessao foi descartada ou se este socket ja nao e o ativo
      // (caso do StrictMode: efeito remonta enquanto o handshake acontecia)
      if (disposedRef.current || socketRef.current !== ws) {
        ws.close();
        return;
      }
      attemptsRef.current = 0;
      setState("open");
      heartbeatTimer.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, HEARTBEAT_MS);
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data as string) as WsMessage;
        if (message.event === "pong") return;
        handlerRef.current?.(message);
      } catch {
        /* mensagem fora do contrato: ignora sem quebrar a UI */
      }
    };

    ws.onerror = () => {
      /* onclose sempre segue; a reconexao e tratada la */
    };

    ws.onclose = () => {
      if (heartbeatTimer.current !== null) {
        window.clearInterval(heartbeatTimer.current);
        heartbeatTimer.current = null;
      }
      socketRef.current = null;
      if (disposedRef.current) return;
      setState("closed");
      scheduleReconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (disposedRef.current || reconnectTimer.current !== null) return;
    const attempt = attemptsRef.current++;
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** attempt,
      RECONNECT_MAX_MS,
    );
    reconnectTimer.current = window.setTimeout(() => {
      reconnectTimer.current = null;
      connect();
    }, delay);
  }, [connect]);

  useEffect(() => {
    if (!enabled) return;
    disposedRef.current = false;
    connect();
    return () => {
      disposedRef.current = true;
      clearTimers();
      const ws = socketRef.current;
      socketRef.current = null;
      if (!ws) return;
      // Se ainda esta CONNECTING, fechar agora dispara o aviso
      // "WebSocket is closed before the connection is established".
      // Nesse caso mantemos o handler onopen, que fecha ao conectar.
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      if (ws.readyState === WebSocket.OPEN) {
        ws.onopen = null;
        ws.close();
      }
    };
  }, [enabled, connect, clearTimers]);

  return { state };
}
