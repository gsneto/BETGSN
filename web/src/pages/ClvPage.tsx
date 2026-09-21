/**
 * ClvPage — tela de CLV (Closing Line Value).
 */

import { useState } from "react";
import { fetchClv } from "@/api/clv";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import {
  EmptyState,
  ErrorPanel,
  KpiCard,
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
import { fmtOdd, fmtPct } from "@/utils/format";

export default function ClvPage() {
  const { dataVersion } = useStore();
  const [filterStatus, setFilterStatus] = useState<string>("all");

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchClv(signal),
    [dataVersion],
  );

  const entries = data ? (
    filterStatus === "all" ? data.entries : data.entries.filter((e) => e.status === filterStatus)
  ) : [];

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

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <KpiCard label="Total de bets" value={String(data.total_bets)} />
        <KpiCard label="Com CLV" value={String(data.bets_with_clv)} tone="accent" />
        <KpiCard label="Taxa positiva" value={data.positive_clv_rate != null ? fmtPct(data.positive_clv_rate) : "—"} />
        <KpiCard label="Avg CLV" value={data.avg_clv_percentage != null ? fmtPct(data.avg_clv_percentage) : "—"} />
      </div>

      <Card
        title="Entradas de CLV"
        hint={`${data.total_bets} bets · coverage ${fmtPct(data.coverage)} · atualizado ${data.generated_at}`}
        padded={false}
        action={
          <div className="flex items-center gap-2">
            {["all", "OK", "NO_CLOSING_ODDS"].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setFilterStatus(s)}
                className={`label-caps px-2 py-1 rounded text-[11.5px] ${
                  filterStatus === s ? "bg-accent-400/8 text-accent-300" : "bg-surface-2 text-ink-3 hover:bg-surface-3"
                }`}
              >
                {s === "all" ? "Todos" : s === "OK" ? "OK" : "Sem fechamento"}
              </button>
            ))}
          </div>
        }
      >
        <div className="p-3">
          {entries.length === 0 ? (
            <EmptyState
              title="Sem entradas de CLV"
              hint="CLV requer odds de fechamento observadas antes do kickoff."
            />
          ) : (
            <TableShell className="max-h-[440px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start">Jogo</Th>
                    <Th align="start">Mercado</Th>
                    <Th align="center">Odd entrada</Th>
                    <Th align="center">Odd fechamento</Th>
                    <Th align="end">CLV %</Th>
                    <Th align="center">Status</Th>
                  </Tr>
                </THead>
                <TBody>
                  {entries.map((e, i) => (
                    <Tr key={`${e.match}-${e.market}-${e.outcome}-${i}`}>
                      <Td align="start" className="font-medium text-ink">{e.match}</Td>
                      <Td mono className="text-ink-3">{e.market}</Td>
                      <Td mono className="text-ink-2">{fmtOdd(e.entry_odd)}</Td>
                      <Td mono className="text-ink-2">
                        {e.closing_odd ? fmtOdd(e.closing_odd) : "—"}
                      </Td>
                      <Td mono className={e.clv_percentage != null && e.clv_percentage > 0 ? "text-pos-400" : "text-ink-2"}>
                        {e.clv_percentage != null ? fmtPct(e.clv_percentage) : "—"}
                      </Td>
                      <Td align="center">
                        <Badge
                          tone={e.status === "OK" ? "accent" : "neutral"}
                          size="sm"
                        >
                          {e.status === "OK" ? "OK" : "Sem fechamento"}
                        </Badge>
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
