/**
 * Contexto do terminal: tipos, contexto React e hook de acesso.
 *
 * Fica separado de `AppStore.tsx` (que so exporta o componente provider)
 * para preservar o fast refresh e evitar import circular entre provider
 * e consumidores.
 */

import { createContext, useContext } from "react";
import type { SocketState } from "@/hooks/useBetgsnSocket";
import type {
  DashboardSummary,
  ModelConfiguration,
  SystemStatus,
  TabKey,
} from "@/types/api";

/** Configuracao inicial, igual aos defaults da GUI legada. */
export const DEFAULT_CONFIG: ModelConfiguration = {
  bankroll: 1000,
  kelly_fraction: 0.25,
  min_ev: 0.02,
  stake_cap: 0.01,
  max_exposure: 0.25,
  use_xg: true,
  rounds: 3,
};

export interface Toast {
  id: number;
  tone: "success" | "error" | "info";
  message: string;
}

export interface StoreState {
  tab: TabKey;
  config: ModelConfiguration;
  summary: DashboardSummary | null;
  status: SystemStatus | null;
  recalculating: boolean;
  /** incrementa a cada recalculo: as abas reagem e recarregam */
  dataVersion: number;
  error: string | null;
  toasts: Toast[];
}

export interface Store extends StoreState {
  socketState: SocketState;
  setTab: (tab: TabKey) => void;
  setConfig: (patch: Partial<ModelConfiguration>) => void;
  hydrate: (summary: DashboardSummary) => void;
  recalculate: (override?: Partial<ModelConfiguration>) => Promise<void>;
  pushToast: (tone: Toast["tone"], message: string) => void;
  dismissToast: (id: number) => void;
}

export const StoreContext = createContext<Store | null>(null);

export function useStore(): Store {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore precisa estar dentro de AppStoreProvider");
  return ctx;
}
