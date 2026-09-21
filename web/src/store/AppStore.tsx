/**
 * Estado compartilhado do terminal.
 *
 * Decisao de arquitetura: NAO ha Redux nem Zustand aqui. O estado global
 * real e pequeno — configuracao do modelo, snapshot do dashboard, status
 * de conexao e toasts. Context + useReducer resolve com menos peso e sem
 * dependencia extra. Os dados de cada aba sao carregados sob demanda por
 * `useApiResource` e nao vivem no estado global.
 */

import {
  useCallback,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { ApiError } from "@/api/client";
import { postRecalculate } from "@/api/system";
import { useBetgsnSocket } from "@/hooks/useBetgsnSocket";
import type { DashboardSummary, ModelConfiguration, SystemStatus, WsMessage } from "@/types/api";
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
  | { type: "recalc:done"; summary: DashboardSummary }
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
    case "recalc:done":
      return {
        ...state,
        recalculating: false,
        summary: action.summary,
        config: action.summary.configuration,
        dataVersion: state.dataVersion + 1,
        error: null,
      };
    case "recalc:error":
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

  const recalculate = useCallback(
    async (override?: Partial<ModelConfiguration>) => {
      if (inFlight.current) return;
      inFlight.current = true;
      const config = { ...configRef.current, ...override };
      dispatch({ type: "recalc:start" });
      try {
        const summary = await postRecalculate(config);
        dispatch({ type: "recalc:done", summary });
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

  const onSocketEvent = useCallback((message: WsMessage) => {
    if (message.event === "status") {
      dispatch({ type: "status", status: message.payload as SystemStatus });
    }
  }, []);

  const { state: socketState } = useBetgsnSocket({ onEvent: onSocketEvent });

  const value = useMemo<Store>(
    () => ({
      ...state,
      socketState,
      setTab,
      setConfig,
      hydrate,
      recalculate,
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
      pushToast,
      dismissToast,
    ],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
