/**
 * FixturesPage — tela de FIXTURES.
 *
 * Lista os jogos futuros com disponibilidade de odds e bookmakers.
 * Todos os dados vêm de GET /api/fixtures. Nenhum cálculo local.
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
  Tr,
} from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import { cn } from "@/utils/cn";
import { fmtDateTime, fmtInt } from "@/utils/format";

export default function FixturesPage() {
  const { dataVersion } = useStore();
  const [filter, setFilter] = useState<"all" | "with_odds" | "no_odds">("all");

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchFixtures(signal),
    [dataVersion],
  );

  const filtered = useMemo(() => {
    if (!data) return [];
    if (filter === "with_odds") return data.fixtures.filter((f) => f.has_odds);
    if (filter === "no_odds") return data.fixtures.filter((f) => !f.has_odds);
    return data.fixtures;
  }, [data, filter]);

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
        hint={`${fmtInt(data.n_fixtures)} jogos · ${fmtInt(data.n_with_odds)} com odds · atualizado ${fmtDateTime(data.generated_at)}`}
        padded={false}
        action={
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
        }
      >
        <div className="p-3">
          {filtered.length === 0 ? (
            <EmptyState
              title={filter === "all" ? "Nenhum fixture" : `Sem jogos ${filter === "with_odds" ? "com odds" : "sem odds"}`}
              hint="Os fixtures são atualizados pelo football-data.co.uk."
            />
          ) : (
            <TableShell className="max-h-[440px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th width={220} align="start">Jogo</Th>
                    <Th width={100} align="start">Liga</Th>
                    <Th width={100} align="center">Kickoff</Th>
                    <Th width={80} align="center">Odds</Th>
                    <Th width={80} align="end">Bookmakers</Th>
                    <Th width={100} align="start">Mercados</Th>
                  </Tr>
                </THead>
                <TBody>
                  {filtered.map((f) => (
                    <Tr key={f.match}>
                      <Td align="start" className="font-medium text-ink">
                        {f.home} vs {f.away}
                        {!f.has_odds && (
                          <Badge tone="neutral" size="sm" className="ms-2">sem odds</Badge>
                        )}
                      </Td>
                      <Td mono className="text-ink-3">{f.league}</Td>
                      <Td mono className="text-ink-2">{fmtDateTime(f.kickoff)}</Td>
                      <Td align="center" mono>
                        {f.has_odds ? (
                          <span className="text-accent-300">sim</span>
                        ) : (
                          <span className="text-ink-4">—</span>
                        )}
                      </Td>
                      <Td mono className="text-ink-2">{fmtInt(f.n_bookmakers)}</Td>
                      <Td mono className="text-ink-3">
                        {f.markets.slice(0, 2).join(", ")}
                        {f.markets.length > 2 ? ` +${f.markets.length - 2}` : ""}
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
