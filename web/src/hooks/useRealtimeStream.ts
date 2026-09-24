/**
 * useRealtimeStream — conexao SSE com o backend em tempo real.
 *
 * Responsabilidades DELIBERADAMENTE limitadas:
 *  - manter a conexao viva com reconexao automatica (EventSource nativo
 *    reconecta sozinho; alem disso, o backend encerra o stream com
 *    `event: CLOSE` apos max_seconds e o EventSource reabre);
 *  - expor o estado da conexao: CONNECTED / RECONNECTING / OFFLINE;
 *  - avisar "algo mudou" via `onChange` (com debounce) — os DADOS sao
 *    buscados dos endpoints tipados, nunca parseados do payload aqui;
 *  - deduplicar: eventos com o mesmo event_id nao redisparam onChange.
 *
 * Nada de estado de mercado aqui: isso evita fontes concorrentes de
 * verdade no frontend.
 */
import { useEffect, useRef, useState } from "react";

export type StreamConnection = "connecting" | "open" | "reconnecting" | "offline";

const CHANGE_DEBOUNCE_MS = 750;

export function useRealtimeStream(
  url: string,
  onChange: () => void,
): StreamConnection {
  const [connection, setConnection] = useState<StreamConnection>("connecting");
  const seenRef = useRef<Set<string>>(new Set());
  const timerRef = useRef<number | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (typeof window === "undefined" || !("EventSource" in window)) {
      setConnection("offline");
      return;
    }

    const source = new EventSource(url);
    let everConnected = false;

    const scheduleChange = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        onChangeRef.current();
      }, CHANGE_DEBOUNCE_MS);
    };

    source.addEventListener("open", () => {
      everConnected = true;
      setConnection("open");
    });

    source.addEventListener("error", () => {
      if (source.readyState === EventSource.CLOSED) {
        setConnection(everConnected ? "reconnecting" : "offline");
      } else {
        setConnection("reconnecting");
      }
    });

    const handle = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(String(event.data)) as {
          event_id?: string;
        };
        const id = parsed?.event_id ?? String(event.data);
        if (seenRef.current.has(id)) return;
        seenRef.current.add(id);
        if (seenRef.current.size > 2000) {
          seenRef.current = new Set(Array.from(seenRef.current).slice(-1000));
        }
      } catch {
        // payload sem event_id: ainda conta como mudanca
      }
      setConnection("open");
      scheduleChange();
    };

    const EVENTS = [
      "ODDS_UPDATE",
      "MOVEMENT",
      "SIGNAL_CREATED",
      "SIGNAL_UPDATED",
      "SIGNAL_EXPIRED",
      "PROVIDER_STATUS",
      "HEALTH_UPDATE",
      "DATA_QUALITY",
      "MATCH_STATUS",
    ] as const;
    for (const type of EVENTS) {
      source.addEventListener(type, handle as EventListener);
    }

    source.addEventListener("CLOSE", () => {
      // encerramento explicito do backend: o EventSource reconecta sozinho
      setConnection("reconnecting");
    });

    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      source.close();
    };
    // a URL muda apenas entre builds; onChange vive em ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return connection;
}
