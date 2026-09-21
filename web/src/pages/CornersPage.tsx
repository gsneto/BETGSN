/**
 * CornersPage — tela ESCANTEIOS.
 *
 * Consome dados reais de GET /api/games (mercado "Escanteios" e ratings de
 * cantos por time). As probabilidades vem do modelo de producao
 * (Poisson/Dixon-Coles sobre cantos); NAO ha preco de mercado de cantos no
 * arquivo de fixtures do football-data.co.uk, entao edge/EV/Kelly ficam
 * explicitamente "Sem preco de mercado" — nunca um zero inventado.
 *
 * As medias de cantos sao da janela de ratings (por temporada). Rolling
 * 5/10/20 nao esta exposto pela API; a tela nao fabrica esse dado.
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

const CORNERS_TOTAL_LINES = ["8.5", "9.5", "10.5"] as const;
const CORNERS_TEAM_LINES = ["4.5", "5.5"] as const;

type SortKey = "match" | "kickoff" | "home_for" | "home_against" | "over_95";

const marketOutcomes = (g: GameAnalysis): Record<string, number> => {
  const entry: MarketProbabilities | undefined = g.markets.find(
    (m) => m.market === "Escanteios",
  );
  return entry?.outcomes ?? {};
};

export default function CornersPage() {
  const { dataVersion } = useStore();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortState<SortKey>>({
    key: "over_95",
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
      home_for: (g) => g.ratings_home.corners_for,
      home_against: (g) => g.ratings_home.corners_against,
      over_95: (g) => marketOutcomes(g)["Cantos Over 9.5"] ?? 0,
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

  if (initialLoading) return <TableSkeleton rows={14} cols={10} />;
  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <Card
        title="Cantos — probabilidades do modelo"
        hint={`${fmtInt(data.length)} partidas · sem preço de mercado de cantos nesta fonte`}
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
            Probabilidades do modelo de produção (Poisson sobre cantos
            for/against). O arquivo de fixtures não traz odds de escanteios, por
            isso edge, EV e Kelly ficam{" "}
            <span className="font-medium text-ink-3">Sem preço de mercado</span>.
            Médias de cantos são da janela de ratings; rolling 5/10/20 não está
            exposto pela API.
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
                    {head("home_for", "Casa CF", 88, "desc", "Cantos a favor (casa)")}
                    {head("home_against", "Casa CA", 88, "desc", "Cantos contra (casa)")}
                    {CORNERS_TOTAL_LINES.map((line) => (
                      <Th key={line} width={96} align="end" title={`Total de cantos over ${line}`}>
                        {`Total >${line}`}
                      </Th>
                    ))}
                    {CORNERS_TEAM_LINES.map((line) => (
                      <Th key={line} width={96} align="end" title={`Cantos da casa over ${line}`}>
                        {`Casa >${line}`}
                      </Th>
                    ))}
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((g) => (
                    <CornersRow key={g.id} game={g} />
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

function CornersRow({ game: g }: { game: GameAnalysis }) {
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
      <Td mono className="text-ink-2">{fmtNum(g.ratings_home.corners_for, 1)}</Td>
      <Td mono className="text-ink-2">{fmtNum(g.ratings_home.corners_against, 1)}</Td>
      {CORNERS_TOTAL_LINES.map((line) => (
        <ProbOverCell key={line} value={oc[`Cantos Over ${line}`]} />
      ))}
      {CORNERS_TEAM_LINES.map((line) => (
        <ProbOverCell key={line} value={oc[`Casa Cantos Over ${line}`]} />
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
    <Td align="end" className="min-w-[96px]">
      <div className="flex items-center gap-2">
        <ProbBar value={value} tone="model" className="flex-1" />
        <span className="num w-[46px] shrink-0 text-end text-[11.5px] text-ink-2">
          {fmtPct(value)}
        </span>
      </div>
    </Td>
  );
}
