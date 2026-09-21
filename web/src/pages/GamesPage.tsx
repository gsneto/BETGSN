/**
 * GamesPage — tela JOGOS.
 *
 * Paridade com a aba JOGOS da GUI legada (`_match_columns`):
 * Jogo, Data, Rodada, λ casa/fora, 1%, X%, 2%, Over 2.5, BTTS,
 * Casa Ct>5.5, Cart >3.5 e Placar provável.
 *
 * Acrescenta duas informacoes que ja existiam no backend mas nao
 * apareciam na grade antiga: nº de mercados com odds e nº de sinais do
 * jogo (dado real de GET /api/games, nao inventado).
 */

import { useMemo, useState } from "react";
import { fetchGames } from "@/api/games";
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
import type { GameAnalysis } from "@/types/api";
import { cn } from "@/utils/cn";
import { fmtDate, fmtInt, fmtNum, fmtPct, fmtScoreline } from "@/utils/format";
import { sortBy, textFilter, toggleSort, type SortState } from "@/utils/table";

type SortKey =
  | "kickoff"
  | "match"
  | "lambda"
  | "prob_home"
  | "prob_draw"
  | "prob_away"
  | "prob_over_25"
  | "prob_btts"
  | "signal_count";

const extractors: Record<SortKey, (g: GameAnalysis) => number | string> = {
  kickoff: (g) => g.kickoff,
  match: (g) => g.match,
  lambda: (g) => g.lambda_home + g.lambda_away,
  prob_home: (g) => g.prob_home,
  prob_draw: (g) => g.prob_draw,
  prob_away: (g) => g.prob_away,
  prob_over_25: (g) => g.prob_over_25,
  prob_btts: (g) => g.prob_btts,
  signal_count: (g) => g.signal_count,
};

export default function GamesPage() {
  const { dataVersion } = useStore();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState<SortKey>>({
    key: "kickoff",
    direction: "asc",
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
      g.round_label,
      g.kickoff,
    ]);
    return sortBy(filtered, extractors[sort.key], sort.direction);
  }, [data, query, sort]);

  const onSort = (key: SortKey, defaultDir: "asc" | "desc" = "desc") =>
    setSort((cur) => toggleSort(cur, key, defaultDir));

  const head = (
    key: SortKey,
    label: string,
    width: number,
    align: "start" | "end" = "end",
    dir: "asc" | "desc" = "desc",
    title?: string,
  ) => (
    <Th
      width={width}
      align={align}
      sortable
      active={sort.key === key}
      direction={sort.direction}
      onClick={() => onSort(key, dir)}
      title={title}
    >
      {label}
    </Th>
  );

  if (initialLoading) return <TableSkeleton rows={14} cols={12} />;
  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />;
  if (!data) return null;

  const totalSignals = data.reduce((acc, g) => acc + g.signal_count, 0);

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <Card
        title="Probabilidades do modelo por jogo"
        hint={`${fmtInt(data.length)} partidas · λ = gols esperados · ${fmtInt(totalSignals)} sinais associados`}
        padded={false}
        action={
          <SearchInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClear={() => setQuery("")}
            placeholder="Buscar jogo, rodada ou competição…"
            aria-label="Buscar jogos"
            className="w-[320px]"
          />
        }
      >
        <div className="p-3">
          <TableShell className="max-h-[calc(100vh-260px)]">
            {rows.length === 0 ? (
              <EmptyState
                title="Nenhum jogo encontrado"
                hint="Ajuste a busca ou clique em RECALCULAR para gerar a análise."
              />
            ) : (
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    {head("match", "Jogo", 220, "start", "asc")}
                    {head("kickoff", "Data", 100, "start", "asc")}
                    <Th width={110} align="start">
                      Rodada
                    </Th>
                    {head("lambda", "λ casa/fora", 116, "end", "desc", "Gols esperados de cada lado")}
                    {head("prob_home", "1 %", 84, "end", "desc", "Probabilidade de vitória da casa")}
                    {head("prob_draw", "X %", 84, "end", "desc")}
                    {head("prob_away", "2 %", 84, "end", "desc")}
                    {head("prob_over_25", "Over 2.5", 96, "end", "desc")}
                    {head("prob_btts", "BTTS", 84, "end", "desc", "Ambas marcam")}
                    <Th width={110} align="end" title="Casa: cantos over 5.5">
                      Casa Ct&gt;5.5
                    </Th>
                    <Th width={100} align="end" title="Cartões over 3.5">
                      Cart &gt;3.5
                    </Th>
                    <Th width={150} align="start" title="Placar mais provável e sua probabilidade">
                      Placar provável
                    </Th>
                    {head("signal_count", "Sinais", 80, "end", "desc", "Sinais gerados neste jogo")}
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((g) => (
                    <GameRow
                      key={g.id}
                      game={g}
                      expanded={expanded === g.id}
                      onToggle={() =>
                        setExpanded((cur) => (cur === g.id ? null : g.id))
                      }
                    />
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

function GameRow({
  game: g,
  expanded,
  onToggle,
}: {
  game: GameAnalysis;
  expanded: boolean;
  onToggle: () => void;
}) {
  const top = g.top_scorelines[0];
  const best1x2 = Math.max(g.prob_home, g.prob_draw, g.prob_away);

  return (
    <>
      <Tr selected={expanded} onClick={onToggle}>
        <Td align="start" className="max-w-[220px]">
          <span className="block truncate font-semibold text-ink" title={g.match}>
            {g.match}
          </span>
          <span className="block truncate text-[10.5px] leading-tight text-ink-4">
            {g.league}
          </span>
        </Td>
        <Td align="start" mono className="text-ink-3">
          {fmtDate(g.kickoff)}
        </Td>
        <Td align="start" className="text-ink-3">
          {g.round_label || "—"}
        </Td>
        <Td mono className="text-ink">
          {fmtNum(g.lambda_home)} / {fmtNum(g.lambda_away)}
        </Td>
        <ProbCell value={g.prob_home} highlight={g.prob_home === best1x2} />
        <ProbCell value={g.prob_draw} highlight={g.prob_draw === best1x2} />
        <ProbCell value={g.prob_away} highlight={g.prob_away === best1x2} />
        <ProbCell value={g.prob_over_25} />
        <ProbCell value={g.prob_btts} />
        <ProbCell value={g.prob_home_corners_over_55} />
        <ProbCell value={g.prob_cards_over_35} />
        <Td align="start" mono className="text-ink-2">
          {top ? `${fmtScoreline(top.home_goals, top.away_goals)} (${fmtPct(top.prob)})` : "—"}
        </Td>
        <Td mono className={cn("font-semibold", g.signal_count > 0 ? "text-accent-300" : "text-ink-4")}>
          {g.signal_count > 0 ? fmtInt(g.signal_count) : "—"}
        </Td>
      </Tr>

      {expanded ? (
        <tr className="bg-surface-2/70">
          <td colSpan={13} className="border-b border-line px-4 py-3">
            <div className="fade-up grid grid-cols-1 gap-4 lg:grid-cols-3">
              <RatingsBlock title={g.home} team={g.ratings_home} />
              <RatingsBlock title={g.away} team={g.ratings_away} />
              <div className="min-w-0">
                <p className="label-caps mb-2">
                  Placar provável · mercados com odds: {g.n_markets_with_odds}
                </p>
                <ul className="grid grid-cols-3 gap-2">
                  {g.top_scorelines.slice(0, 6).map((s, i) => (
                    <li
                      key={i}
                      className="rounded-md border border-line bg-surface-1 px-2 py-1.5 text-center"
                    >
                      <span className="num block text-body font-semibold text-ink">
                        {fmtScoreline(s.home_goals, s.away_goals)}
                      </span>
                      <span className="num block text-[10.5px] text-ink-4">
                        {fmtPct(s.prob)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 border-t border-line pt-3 md:grid-cols-2 xl:grid-cols-3">
              {g.markets.map((m) => (
                <div key={m.market} className="min-w-0">
                  <p className="label-caps mb-1.5 truncate" title={m.market}>
                    {m.market}
                  </p>
                  <ul className="flex flex-col gap-1">
                    {Object.entries(m.outcomes).map(([oc, p]) => (
                      <li key={oc} className="flex items-center gap-2">
                        <span className="w-[120px] shrink-0 truncate text-[11.5px] text-ink-3" title={oc}>
                          {oc}
                        </span>
                        <ProbBar value={p} tone="model" className="flex-1" />
                        <span className="num w-[46px] shrink-0 text-end text-[11.5px] text-ink-2">
                          {fmtPct(p)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ProbCell({ value, highlight = false }: { value: number; highlight?: boolean }) {
  return (
    <Td mono className={highlight ? "font-semibold text-accent-300" : "text-ink-2"}>
      {fmtPct(value)}
    </Td>
  );
}

function RatingsBlock({
  title,
  team,
}: {
  title: string;
  team: GameAnalysis["ratings_home"];
}) {
  const pairs: [string, string][] = [
    ["Ataque", fmtNum(team.attack)],
    ["Defesa", fmtNum(team.defense)],
    ["Força", fmtNum(team.strength)],
    ["xG criado", fmtNum(team.xg_for)],
    ["xG concedido", fmtNum(team.xg_against)],
    ["Cantos", fmtNum(team.corners_for, 1)],
    ["Cartões", fmtNum(team.cards_for, 1)],
    ["Forma (pts)", fmtNum(team.form_points)],
  ];
  return (
    <div className="min-w-0">
      <p className="label-caps mb-2 truncate" title={title}>
        {title}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        {pairs.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-2 border-b border-line pb-0.5">
            <dt className="truncate text-[11.5px] text-ink-4">{k}</dt>
            <dd className="num text-[12px] font-medium text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
