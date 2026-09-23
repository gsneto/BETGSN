/**
 * Estado compartilhado do terminal.
 *
 * Decisao de arquitetura: NAO ha Redux nem Zustand aqui. O estado global
 * real e pequeno - configuracao do modelo, snapshot do dashboard, status
 * de conexao, job de recalculo e toasts. Context + useReducer resolve com
 * menos peso e sem dependencia extra. Os dados de cada aba sao carregados
 * sob demanda por `useApiResource` e nao vivem no estado global.
 *
 * O recalculo e ASSINCRONO: o POST apenas dispara o job no backend; o
 * progresso chega pelo WebSocket (`recalculate:progress`) e a conclusao
 * pelo evento `recalculate:done` (que carrega o DashboardSummary). Se o
 * WebSocket cair, um polling de fallback consulta
 * /api/recalculate/status e, no fim, /api/dashboard. Uma falha do job
 * NUNCA descarta o snapshot anterior: a UI continua com os dados validos
 * mais recentes e mostra o motivo do erro.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { ApiError } from "@/api/client";
import {
  fetchDashboard,
  fetchRecalculateStatus,
  postRecalculate,
  postRecalculateCancel,
} from "@/api/system";
import { useBetgsnSocket } from "@/hooks/useBetgsnSocket";
import type {
  DashboardSummary,
  ModelConfiguration,
  RecalculateJobStatus,
  SystemStatus,
  WsMessage,
} from "@/types/api";
import {
  DEFAULT_CONFIG,
  StoreContext,
  type Store,
  type StoreState,
  type Toast,
} from "./context";

type Action =
  | { type: "tab"; tab: StoreState["tab"] }
  | { type: "config"; patch: Partial<ModelConfiguration> }
  | { type: "hydrate"; summary: DashboardSummary }
  | { type: "recalc:start" }
  | { type: "recalc:job"; job: RecalculateJobStatus }
  | { type: "recalc:done"; summary: DashboardSummary; job: RecalculateJobStatus }
  | { type: "recalc:error"; message: string }
  | { type: "status"; status: SystemStatus }
  | { type: "toast:add"; toast: Toast }
  | { type: "toast:remove"; id: number };

const initialState: StoreState = {
  tab: "signals",
  config: DEFAULT_CONFIG,
  summary: null,
  status: null,
  recalculating: false,
  recalcJob: null,
  dataVersion: 0,
  error: null,
  toasts: [],
};

function reducer(state: StoreState, action: Action): StoreState {
  switch (action.type) {
    case "tab":
      return state.tab === action.tab ? state : { ...state, tab: action.tab };
    case "config":
      return { ...state, config: { ...state.config, ...action.patch } };
    case "hydrate":
      // snapshot ja existente no backend: adota sem disparar recalculo
      return {
        ...state,
        summary: action.summary,
        config: action.summary.configuration,
        dataVersion: state.dataVersion + 1,
        error: null,
      };
    case "recalc:start":
      return { ...state, recalculating: true, error: null };
    case "recalc:job":
      return { ...state, recalcJob: action.job };
    case "recalc:done":
      return {
        ...state,
        recalculating: false,
        recalcJob: action.job,
        summary: action.summary,
        config: action.summary.configuration,
        dataVersion: state.dataVersion + 1,
        error: null,
      };
    case "recalc:error":
      // snapshot anterior PRESERVADO: so o estado de erro muda
      return { ...state, recalculating: false, error: action.message };
    case "status":
      return { ...state, status: action.status };
    case "toast:add":
      return { ...state, toasts: [...state.toasts, action.toast] };
    case "toast:remove":
      return {
        ...state,
        toasts: state.toasts.filter((t) => t.id !== action.id),
      };
    default:
      return state;
  }
}

export function AppStoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const toastId = useRef(0);
  const inFlight = useRef(false);
  // leitura do estado atual dentro de callbacks estaveis
  const stateRef = useRef(state);
  stateRef.current = state;

  const dismissToast = useCallback((id: number) => {
    dispatch({ type: "toast:remove", id });
  }, []);

  const pushToast = useCallback((tone: Toast["tone"], message: string) => {
    const id = ++toastId.current;
    dispatch({ type: "toast:add", toast: { id, tone, message } });
    window.setTimeout(() => dispatch({ type: "toast:remove", id }), 5000);
  }, []);

  const setTab = useCallback(
    (tab: StoreState["tab"]) => dispatch({ type: "tab", tab }),
    [],
  );

  const setConfig = useCallback(
    (patch: Partial<ModelConfiguration>) => dispatch({ type: "config", patch }),
    [],
  );

  const hydrate = useCallback(
    (summary: DashboardSummary) => dispatch({ type: "hydrate", summary }),
    [],
  );

  // usa ref para ler a config atual sem recriar a funcao a cada digito
  const configRef = useRef(state.config);
  configRef.current = state.config;

  const onSocketEvent = useCallback((message: WsMessage) => {
    if (message.event === "status") {
      dispatch({ type: "status", status: message.payload as SystemStatus });
      return;
    }
    if (message.event === "recalculate:progress") {
      const job = message.payload as RecalculateJobStatus;
      dispatch({ type: "recalc:job", job });
      if (job.phase === "error" || job.phase === "cancelled") {
        dispatch({ type: "recalc:error", message: job.message });
      }
      return;
    }
    if (message.event === "recalculate:done") {
      const summary = message.payload as DashboardSummary;
      dispatch({
        type: "recalc:done",
        summary,
        job: {
          job_id: null,
          phase: "done",
          progress: 1,
          message: "Concluído.",
          error: null,
          snapshot_generated_at: summary.generated_at,
          has_snapshot: true,
        },
      });
    }
  }, []);

  const { state: socketState } = useBetgsnSocket({ onEvent: onSocketEvent });

  // Fallback: sem WebSocket, o progresso do job chega por polling. Quando
  // o job termina sem evento WS, o dashboard final vem de /api/dashboard.
  useEffect(() => {
    if (!state.recalculating || socketState === "open") return;
    const interval = window.setInterval(async () => {
      try {
        const job = await fetchRecalculateStatus();
        dispatch({ type: "recalc:job", job });
        if (job.phase === "done") {
          const summary = await fetchDashboard();
          dispatch({ type: "recalc:done", summary, job });
        } else if (job.phase === "error" || job.phase === "cancelled") {
          dispatch({ type: "recalc:error", message: job.message });
        }
      } catch {
        /* rede instavel: a proxima iteracao tenta de novo */
      }
    }, 2000);
    return () => window.clearInterval(interval);
  }, [state.recalculating, socketState]);

  const recalculate = useCallback(
    async (override?: Partial<ModelConfiguration>) => {
      if (inFlight.current) return;
      inFlight.current = true;
      const config = { ...configRef.current, ...override };
      dispatch({ type: "recalc:start" });
      try {
        // o POST apenas dispara o job; a conclusao chega via WS/polling
        const job = await postRecalculate(config);
        dispatch({ type: "recalc:job", job });
        if (job.phase === "error" || job.phase === "cancelled") {
          dispatch({ type: "recalc:error", message: job.message });
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.userMessage
            : err instanceof Error
              ? err.message
              : "Falha ao recalcular.";
        dispatch({ type: "recalc:error", message });
        pushToast("error", message);
      } finally {
        inFlight.current = false;
      }
    },
    [pushToast],
  );

  const cancelRecalculate = useCallback(() => {
    void postRecalculateCancel().then((job) => {
      dispatch({ type: "recalc:job", job });
    }).catch(() => {
      /* o job continua: sem WS o polling mostra o estado real */
    });
  }, []);

  const value = useMemo<Store>(
    () => ({
      ...state,
      socketState,
      setTab,
      setConfig,
      hydrate,
      recalculate,
      cancelRecalculate,
      pushToast,
      dismissToast,
    }),
    [
      state,
      socketState,
      setTab,
      setConfig,
      hydrate,
      recalculate,
      cancelRecalculate,
      pushToast,
      dismissToast,
    ],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
