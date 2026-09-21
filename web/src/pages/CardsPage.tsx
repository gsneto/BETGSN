/**
 * CardsPage — tela CARTÕES.
 *
 * Consome dados reais de GET /api/games (mercado "Cartoes" e ratings de
 * cartões por time). Probabilidades do modelo de produção (Poisson sobre
 * cartões for/against). Não há preço de mercado de cartões no arquivo de
 * fixtures, então edge/EV/Kelly ficam "Sem preço de mercado".
 *
 * O árbitro não está presente no snapshot de fixtures (só no histórico
 * CSV), então a tela declara "não coletado" em vez de inventar perfil
 * disciplinar. Rolling 5/10/20 também não está exposto pela API.
 */

import { useMemo, useState } from "react";
import { fetchGames } from "@/api/games";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { SearchInput } from "@/components/ui/Input";
import ProbBar from "@/components/ui/ProbBar";
import {
  EmptyState,
  ErrorPanel,
  Table,
  TableShell,
  TableSkeleton,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import type { GameAnalysis, MarketProbabilities } from "@/types/api";
import { fmtDate, fmtInt, fmtNum, fmtPct } from "@/utils/format";
import { sortBy, textFilter, toggleSort, type SortState } from "@/utils/table";

const CARDS_TOTAL_LINES = ["2.5", "3.5", "4.5"] as const;

type SortKey = "match" | "kickoff" | "home_for" | "home_against" | "over_35";

const marketOutcomes = (g: GameAnalysis): Record<string, number> => {
  const entry: MarketProbabilities | undefined = g.markets.find(
    (m) => m.market === "Cartoes",
  );
  return entry?.outcomes ?? {};
};

export default function CardsPage() {
  const { dataVersion } = useStore();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortState<SortKey>>({
    key: "over_35",
    direction: "desc",
  });

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchGames(signal),
    [dataVersion],
  );

  const rows = useMemo(() => {
    const filtered = textFilter(data ?? [], query, (g) => [
      g.match,
      g.home,
      g.away,
      g.league,
    ]);
    const extractors: Record<SortKey, (g: GameAnalysis) => number | string> = {
      match: (g) => g.match,
      kickoff: (g) => g.kickoff,
      home_for: (g) => g.ratings_home.cards_for,
      home_against: (g) => g.ratings_home.cards_against,
      over_35: (g) => marketOutcomes(g)["Cartoes Over 3.5"] ?? 0,
    };
    return sortBy(filtered, extractors[sort.key], sort.direction);
  }, [data, query, sort]);

  const onSort = (key: SortKey, defaultDir: "asc" | "desc" = "desc") =>
    setSort((cur) => toggleSort(cur, key, defaultDir));

  const head = (
    key: SortKey,
    label: string,
    width: number,
    dir: "asc" | "desc" = "desc",
    title?: string,
  ) => (
    <Th
      width={width}
      sortable
      active={sort.key === key}
      direction={sort.direction}
      onClick={() => onSort(key, dir)}
      title={title}
    >
      {label}
    </Th>
  );

  if (initialLoading) return <TableSkeleton rows={14} cols={9} />;
  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <Card
        title="Cartões — probabilidades do modelo"
        hint={`${fmtInt(data.length)} partidas · sem preço de mercado de cartões nesta fonte`}
        padded={false}
        action={
          <SearchInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClear={() => setQuery("")}
            placeholder="Buscar jogo ou competição…"
            aria-label="Buscar jogos"
            className="w-[320px]"
          />
        }
      >
        <div className="px-4 pb-2 pt-3">
          <p className="text-[12px] leading-snug text-ink-4">
            Probabilidades do modelo de produção (Poisson sobre cartões
            for/against). O arquivo de fixtures não traz odds de cartões, então
            edge, EV e Kelly ficam{" "}
            <span className="font-medium text-ink-3">Sem preço de mercado</span>.
            Perfil de árbitro:{" "}
            <span className="font-medium text-ink-3">não coletado</span> no
            snapshot de fixtures (só existe no histórico CSV). Rolling 5/10/20
            não está exposto pela API.
          </p>
        </div>
        <div className="p-3">
          <TableShell className="max-h-[calc(100vh-300px)]">
            {rows.length === 0 ? (
              <EmptyState
                title="Nenhum jogo encontrado"
                hint="Ajuste a busca ou clique em RECALCULAR para gerar a análise."
              />
            ) : (
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    {head("match", "Jogo", 220, "asc")}
                    {head("kickoff", "Data", 100, "asc")}
                    <Th width={120} align="start">Liga</Th>
                    {head("home_for", "Casa CF", 88, "desc", "Cartões a favor (casa)")}
                    {head("home_against", "Casa CA", 88, "desc", "Cartões contra (casa)")}
                    {CARDS_TOTAL_LINES.map((line) => (
                      <Th key={line} width={100} align="end" title={`Total de cartões over ${line}`}>
                        {`Total >${line}`}
                      </Th>
                    ))}
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((g) => (
                    <CardsRow key={g.id} game={g} />
                  ))}
                </TBody>
              </Table>
            )}
          </TableShell>
        </div>
      </Card>
    </div>
  );
}

function CardsRow({ game: g }: { game: GameAnalysis }) {
  const oc = marketOutcomes(g);
  const hasMarket = Object.keys(oc).length > 0;
  return (
    <Tr>
      <Td align="start" className="max-w-[220px]">
        <span className="block truncate font-semibold text-ink" title={g.match}>
          {g.match}
        </span>
        <span className="block truncate text-[10.5px] leading-tight text-ink-4">
          {g.away} <span className="text-ink-4">·</span>{" "}
          {hasMarket ? "modelo" : "sem dado"}
        </span>
      </Td>
      <Td align="start" mono className="text-ink-3">
        {fmtDate(g.kickoff)}
      </Td>
      <Td align="start" className="truncate text-ink-3">
        {g.league}
      </Td>
      <Td mono className="text-ink-2">{fmtNum(g.ratings_home.cards_for, 1)}</Td>
      <Td mono className="text-ink-2">{fmtNum(g.ratings_home.cards_against, 1)}</Td>
      {CARDS_TOTAL_LINES.map((line) => (
        <ProbOverCell key={line} value={oc[`Cartoes Over ${line}`]} />
      ))}
    </Tr>
  );
}

function ProbOverCell({ value }: { value: number | undefined }) {
  if (value === undefined) {
    return (
      <Td className="text-[11px] text-ink-4" align="end" title="Sem preço de mercado">
        <Badge tone="neutral">n/d</Badge>
      </Td>
    );
  }
  return (
    <Td align="end" className="min-w-[100px]">
      <div className="flex items-center gap-2">
        <ProbBar value={value} tone="model" className="flex-1" />
        <span className="num w-[46px] shrink-0 text-end text-[11.5px] text-ink-2">
          {fmtPct(value)}
        </span>
      </div>
    </Td>
  );
}
