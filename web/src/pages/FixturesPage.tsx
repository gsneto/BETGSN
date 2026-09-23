/**
 * FixturesPage — tela de FIXTURES.
 *
 * Lista os jogos futuros com disponibilidade de odds e bookmakers.
 * Todos os dados vêm de GET /api/fixtures. Nenhum cálculo local.
 *
 * Kickoff: o backend manda o instante em UTC canônico (`kickoff`) e, quando
 * disponível, o horário local da competição (`kickoff_local` + `timezone`).
 * Exibe-se o horário local com o fuso — nunca a hora local com um "Z".
 */

import { useMemo, useState } from "react";
import { fetchFixtures } from "@/api/fixtures";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import {
  EmptyState,
  ErrorPanel,
  Skeleton,
  Table,
  TableShell,
  TBody,
  Td,
  Th,
  THead,
  Tooltip,
  Tr,
} from "@/components/ui";
import { SearchInput } from "@/components/ui/Input";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import type { FixtureItem } from "@/types/api";
import { cn } from "@/utils/cn";
import { fmtDateTime, fmtInt } from "@/utils/format";

function kickoffLabel(f: FixtureItem): string {
  if (f.kickoff_local) {
    return f.timezone
      ? `${fmtDateTime(f.kickoff_local)} (${f.timezone})`
      : fmtDateTime(f.kickoff_local);
  }
  return fmtDateTime(f.kickoff);
}

/** Chave única: "Casa vs Fora" pode se repetir entre competições. */
function fixtureKey(f: FixtureItem, i: number): string {
  return `${f.league}|${f.match}|${f.kickoff || f.kickoff_local}|${i}`;
}

type SortKey = "kickoff" | "match" | "league" | "n_bookmakers";

export default function FixturesPage() {
  const { dataVersion } = useStore();
  const [filter, setFilter] = useState<"all" | "with_odds" | "no_odds">("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({
    key: "kickoff",
    dir: 1,
  });

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchFixtures(signal),
    [dataVersion],
  );

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    const base = data.fixtures.filter((f) => {
      if (filter === "with_odds" && !f.has_odds) return false;
      if (filter === "no_odds" && f.has_odds) return false;
      if (!q) return true;
      return (
        f.match.toLowerCase().includes(q) ||
        f.league.toLowerCase().includes(q) ||
        (f.round_label ?? "").toLowerCase().includes(q)
      );
    });
    const sorted = [...base];
    sorted.sort((a, b) => {
      let cmp = 0;
      switch (sort.key) {
        case "kickoff":
          cmp = (a.kickoff || a.kickoff_local || "").localeCompare(
            b.kickoff || b.kickoff_local || "",
          );
          break;
        case "match":
          cmp = a.match.localeCompare(b.match);
          break;
        case "league":
          cmp = a.league.localeCompare(b.league);
          break;
        case "n_bookmakers":
          cmp = a.n_bookmakers - b.n_bookmakers;
          break;
      }
      return cmp * sort.dir;
    });
    return sorted;
  }, [data, filter, query, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((s) =>
      s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: 1 },
    );
  };

  const sortIndicator = (key: SortKey) =>
    sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : "";

  if (initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-[52px] w-full" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }

  if (error && !data) {
    return <ErrorPanel message={error} onRetry={reload} />;
  }

  if (!data) return null;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <Card
        title="Fixtures"
        hint={`${fmtInt(data.n_fixtures)} jogos · ${fmtInt(data.n_with_odds)} com odds · fonte ${data.source} · atualizado ${fmtDateTime(data.generated_at)}`}
        padded={false}
        action={
          <div className="flex items-center gap-2">
            <SearchInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onClear={() => setQuery("")}
              placeholder="Buscar jogo, liga ou rodada…"
              aria-label="Buscar fixtures"
              className="w-[260px]"
            />
            <div className="flex items-center gap-2">
              {(["all", "with_odds", "no_odds"] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  className={cn(
                    "label-caps px-2 py-1 rounded text-[11.5px]",
                    filter === f
                      ? "bg-accent-400/8 text-accent-300"
                      : "bg-surface-2 text-ink-3 hover:bg-surface-3",
                  )}
                >
                  {f === "all" ? "Todos" : f === "with_odds" ? "Com odds" : "Sem odds"}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <div className="p-3">
          {filtered.length === 0 ? (
            <EmptyState
              title={
                query
                  ? "Nenhum fixture corresponde à busca"
                  : filter === "all"
                    ? "Nenhum fixture"
                    : `Sem jogos ${filter === "with_odds" ? "com odds" : "sem odds"}`
              }
              hint={
                query
                  ? "Limpe a busca para ver todos os fixtures."
                  : `Fonte atual: ${data.source}. Os fixtures são atualizados pelo importador.`
              }
            />
          ) : (
            <TableShell className="max-h-[480px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th width={230} align="start">
                      <button type="button" onClick={() => toggleSort("match")} className="hover:text-ink">
                        Jogo{sortIndicator("match")}
                      </button>
                    </Th>
                    <Th width={110} align="start">
                      <button type="button" onClick={() => toggleSort("league")} className="hover:text-ink">
                        Liga{sortIndicator("league")}
                      </button>
                    </Th>
                    <Th width={110} align="center">
                      <button type="button" onClick={() => toggleSort("kickoff")} className="hover:text-ink">
                        Kickoff{sortIndicator("kickoff")}
                      </button>
                    </Th>
                    <Th width={80} align="center">Odds</Th>
                    <Th width={90} align="end">
                      <button type="button" onClick={() => toggleSort("n_bookmakers")} className="hover:text-ink">
                        Casas{sortIndicator("n_bookmakers")}
                      </button>
                    </Th>
                    <Th width={110} align="start">Mercados</Th>
                  </Tr>
                </THead>
                <TBody>
                  {filtered.map((f, i) => (
                    <Tr key={fixtureKey(f, i)}>
                      <Td align="start" className="font-medium text-ink">
                        {f.home} vs {f.away}
                        {!f.has_odds && (
                          <Badge tone="neutral" size="sm" className="ms-2">sem odds</Badge>
                        )}
                      </Td>
                      <Td className="text-ink-3">
                        <span className="text-[12.5px]">{f.league}</span>
                        {f.round_label ? (
                          <span className="block text-[11px] text-ink-4">{f.round_label}</span>
                        ) : null}
                      </Td>
                      <Td className="text-ink-2">
                        <span className="text-[12.5px]">{kickoffLabel(f)}</span>
                      </Td>
                      <Td align="center" mono>
                        {f.has_odds ? (
                          <span className="text-accent-300">sim</span>
                        ) : (
                          <span className="text-ink-4">—</span>
                        )}
                      </Td>
                      <Td mono align="end" className="text-ink-2">
                        <Tooltip
                          content={
                            f.bookmakers.length > 0
                              ? f.bookmakers.join(", ")
                              : "Nenhum bookmaker observado"
                          }
                        >
                          <span>{fmtInt(f.n_bookmakers)}</span>
                        </Tooltip>
                      </Td>
                      <Td className="text-ink-3">
                        <span className="text-[12.5px]">
                          {f.markets.slice(0, 2).join(", ")}
                          {f.markets.length > 2 ? ` +${f.markets.length - 2}` : ""}
                        </span>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
          )}
        </div>
      </Card>
    </div>
  );
}
