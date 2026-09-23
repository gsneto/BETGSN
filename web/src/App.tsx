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
import ClvPage from "@/pages/ClvPage";
import CornersPage from "@/pages/CornersPage";
import CoveragePage from "@/pages/CoveragePage";
import FixturesPage from "@/pages/FixturesPage";
import GamesPage from "@/pages/GamesPage";
import ModelPage from "@/pages/ModelPage";
import MovementPage from "@/pages/MovementPage";
import OddsPage from "@/pages/OddsPage";
import PortfolioPage from "@/pages/PortfolioPage";
import ProvidersPage from "@/pages/ProvidersPage";
import SignalsPage from "@/pages/SignalsPage";
import StatsPage from "@/pages/StatsPage";
import AppHeader from "@/layout/AppHeader";
import ControlBar from "@/layout/ControlBar";
import StatusBar from "@/layout/StatusBar";
import TabBar from "@/layout/TabBar";
import { ErrorPanel, ToastStack } from "@/components/ui";
import { AppStoreProvider } from "@/store/AppStore";
import { useStore } from "@/store/context";
import type { RecalculateJobStatus, TabKey } from "@/types/api";

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
  fixtures: FixturesPage,
  providers: ProvidersPage,
  coverage: CoveragePage,
  clv: ClvPage,
  movement: MovementPage,
};

function Terminal() {
  const { tab, summary, recalcJob, recalculate, recalculating, hydrate } = useStore();
  // guarda a promessa (nao um booleano): sobrevive ao duplo efeito do
  // StrictMode sem descartar o resultado do primeiro fetch
  const bootRef = useRef<Promise<void> | null>(null);

  // primeiro marco funcional: garante um snapshot do backend ao abrir.
  // Se ja existir, adota o snapshot existente (sem recalcular); se nao,
  // dispara o JOB assincrono uma unica vez e mostra o progresso real.
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
  const coldBoot = !summary && recalculating;

  return (
    <div className="flex min-h-screen flex-col bg-app text-ink">
      <AppHeader />
      <TabBar />
      <ControlBar />

      <main className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        {summary && <DataProvenance data={summary.provenance} updated={summary.generated_at} />}
        {coldBoot ? (
          <BootProgress job={recalcJob} />
        ) : !summary && !recalculating ? (
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

/** Progresso do primeiro calculo: fases REAIS do pipeline no backend. */
function BootProgress({ job }: { job: RecalculateJobStatus | null }) {
  const pct = job ? Math.round(job.progress * 100) : 0;
  return (
    <section className="flex flex-1 flex-col items-center justify-center gap-4 py-16">
      <div className="flex flex-col items-center gap-2 text-center">
        <span className="label-caps text-accent-300">Pipeline em execução</span>
        <p className="max-w-md text-sm text-ink-3">
          {job?.message ?? "Iniciando pipeline…"}
        </p>
      </div>
      <div className="h-1.5 w-72 overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-accent-400 transition-[width] duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="num text-xs text-ink-4">{pct}%</span>
    </section>
  );
}

export default function App() {
  return (
    <AppStoreProvider>
      <Terminal />
    </AppStoreProvider>
  );
}
