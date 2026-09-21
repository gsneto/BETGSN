/**
 * App — shell do terminal BETGSN.
 *
 * Arquitetura escolhida: HEADER + CONTROLES + TABS + CONTEUDO + STATUS.
 * NAO ha sidebar: o produto tem 5 areas e o espaco horizontal e o
 * recurso mais caro para uma tabela de 16 colunas. Adaptar o TailAdmin
 * nao significa importar a sidebar dele.
 *
 * A troca de aba NAO desmonta o header nem os controles: apenas o
 * conteudo da pagina muda, mantendo o estado dos parametros.
 */

import { useEffect, useRef } from "react";
import { fetchDashboard } from "@/api/system";
import DataProvenance from "@/components/DataProvenance";
import BacktestPage from "@/pages/BacktestPage";
import CardsPage from "@/pages/CardsPage";
import CornersPage from "@/pages/CornersPage";
import GamesPage from "@/pages/GamesPage";
import ModelPage from "@/pages/ModelPage";
import OddsPage from "@/pages/OddsPage";
import PortfolioPage from "@/pages/PortfolioPage";
import SignalsPage from "@/pages/SignalsPage";
import StatsPage from "@/pages/StatsPage";
import AppHeader from "@/layout/AppHeader";
import ControlBar from "@/layout/ControlBar";
import StatusBar from "@/layout/StatusBar";
import TabBar from "@/layout/TabBar";
import { ErrorPanel, ToastStack } from "@/components/ui";
import { AppStoreProvider } from "@/store/AppStore";
import { useStore } from "@/store/context";
import type { TabKey } from "@/types/api";

const PAGES: Record<TabKey, () => React.JSX.Element | null> = {
  signals: SignalsPage,
  games: GamesPage,
  odds: OddsPage,
  stats: StatsPage,
  model: ModelPage,
  backtest: BacktestPage,
  portfolio: PortfolioPage,
  corners: CornersPage,
  cards: CardsPage,
};

function Terminal() {
  const { tab, summary, recalculate, recalculating, hydrate } = useStore();
  // guarda a promessa (nao um booleano): sobrevive ao duplo efeito do
  // StrictMode sem descartar o resultado do primeiro fetch
  const bootRef = useRef<Promise<void> | null>(null);

  // primeiro marco funcional: garante um snapshot do backend ao abrir.
  // Se ja existir, adota o snapshot existente (sem recalcular); se nao,
  // dispara o calculo uma unica vez.
  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = fetchDashboard()
      .then((dash) => {
        hydrate(dash);
      })
      .catch(() => {
        void recalculate();
      });
  }, [hydrate, recalculate]);

  const Page = PAGES[tab];

  return (
    <div className="flex min-h-screen flex-col bg-app text-ink">
      <AppHeader />
      <TabBar />
      <ControlBar />

      <main className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        {summary && <DataProvenance data={summary.provenance} updated={summary.generated_at} />}
        {!summary && !recalculating ? (
          <ErrorPanel
            message="Nenhum snapshot disponível. Clique em RECALCULAR para executar o pipeline no backend."
            onRetry={() => void recalculate()}
          />
        ) : (
          <Page />
        )}
      </main>

      <StatusBar />
      <ToastStack />
    </div>
  );
}

export default function App() {
  return (
    <AppStoreProvider>
      <Terminal />
    </AppStoreProvider>
  );
}
