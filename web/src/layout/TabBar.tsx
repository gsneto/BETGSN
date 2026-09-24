/**
 * TabBar — abas do terminal.
 *
 * Adaptado do padrao de tabs do TailAdmin (`components/ui/tabs`), com o
 * comportamento pedido: default em texto secundario, hover em primario,
 * ativo em accent com indicador inferior ambar animado (~180ms). Nao sao
 * botoes gigantes: 42px de altura, coerente com o resto do terminal.
 *
 * As 5 areas do produto sao preservadas.
 */

import { useStore } from "@/store/context";
import { cn } from "@/utils/cn";
import type { TabKey } from "@/types/api";

const TABS: { key: TabKey; label: string }[] = [
  { key: "live", label: "LIVE" },
  { key: "signals", label: "Sinais" },
  { key: "games", label: "Jogos" },
  { key: "odds", label: "Casas / Odds" },
  { key: "stats", label: "Estatísticas" },
  { key: "model", label: "Modelo" },
  { key: "backtest", label: "Backtest" },
  { key: "portfolio", label: "Portfólio" },
  { key: "corners", label: "Cantos" },
  { key: "cards", label: "Cartões" },
  { key: "fixtures", label: "Fixtures" },
  { key: "providers", label: "Providers" },
  { key: "coverage", label: "Coverage" },
  { key: "clv", label: "CLV" },
  { key: "movement", label: "Movimento" },
  { key: "quant", label: "Quant" },
];

export default function TabBar() {
  const { tab, setTab, summary } = useStore();

  return (
    <nav
      role="tablist"
      aria-label="Áreas do BETGSN"
      className="sticky top-[60px] z-90 flex h-[42px] items-stretch gap-1 border-b border-line bg-header/95 px-4 backdrop-blur-md"
    >
      {TABS.map((t) => {
        const active = tab === t.key;
        const count =
          t.key === "signals"
            ? summary?.kpis.total
            : t.key === "games"
              ? summary?.n_games
              : undefined;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => setTab(t.key)}
            className={cn(
              "relative flex items-center gap-2 px-3 text-body font-medium",
              "transition-colors duration-[180ms] ease-[cubic-bezier(.2,.8,.2,1)]",
              "focus-visible:shadow-ring",
              active ? "text-accent-300" : "text-ink-2 hover:text-ink",
            )}
          >
            {t.label}
            {count !== undefined ? (
              <span
                className={cn(
                  "num rounded-full px-1.5 py-px text-[10.5px] tabular-nums transition-colors duration-[180ms]",
                  active
                    ? "bg-accent-400/14 text-accent-300"
                    : "bg-surface-2 text-ink-4",
                )}
              >
                {count}
              </span>
            ) : null}
            {/* indicador inferior ambar */}
            <span
              aria-hidden
              className={cn(
                "absolute inset-x-1 bottom-0 h-[2px] rounded-full bg-accent-400",
                "origin-center transition-[transform,opacity] duration-[180ms] ease-[cubic-bezier(.2,.8,.2,1)]",
                active ? "scale-x-100 opacity-100" : "scale-x-0 opacity-0",
              )}
            />
          </button>
        );
      })}
    </nav>
  );
}
