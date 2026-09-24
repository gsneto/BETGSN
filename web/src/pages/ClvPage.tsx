/**
 * ClvPage — tela de CLV (Closing Line Value).
 *
 * Contrato: /api/clv. Tudo vem do backend — inclusive a distinção
 * honesta entre os status (OK, sem fechamento, entrada pós-fechamento,
 * sem entrada observada). Nenhum número é fabricado aqui.
 */

import { useMemo, useState } from "react";
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
  Tooltip,
  Tr,
} from "@/components/ui";
import { SearchInput } from "@/components/ui/Input";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import { cn } from "@/utils/cn";
import { fmtDateTime, fmtInt, fmtOdd, fmtPct } from "@/utils/format";

/** Rótulos dos status de CLV — mesmos enums do backend. */
const clvStatusLabel: Record<string, string> = {
  OK: "OK",
  NO_CLOSING_ODDS: "Sem fechamento",
  CLOSING_BEFORE_ENTRY: "Entrada pós-fechamento",
  NO_ENTRY_ODDS: "Sem odd de entrada observada",
};

const clvStatusFilter: Array<{ value: string; label: string }> = [
  { value: "all", label: "Todos" },
  { value: "OK", label: "OK" },
  { value: "NO_CLOSING_ODDS", label: "Sem fechamento" },
  { value: "CLOSING_BEFORE_ENTRY", label: "Pós-fechamento" },
  { value: "NO_ENTRY_ODDS", label: "Sem entrada" },
];

export default function ClvPage() {
  const { dataVersion } = useStore();
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [query, setQuery] = useState("");

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchClv(signal),
    [dataVersion],
  );

  const entries = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.entries.filter((e) => {
      if (filterStatus !== "all" && e.status !== filterStatus) return false;
      if (!q) return true;
      return (
        e.match.toLowerCase().includes(q) ||
        e.market.toLowerCase().includes(q) ||
        e.outcome.toLowerCase().includes(q)
      );
    });
  }, [data, filterStatus, query]);

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

  const marketRows = Object.entries(data.by_market ?? {});

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <KpiCard label="Total de bets" value={String(data.total_bets)} />
        <KpiCard label="Com CLV" value={String(data.bets_with_clv)} tone="accent" />
        <KpiCard label="Taxa positiva" value={data.positive_clv_rate != null ? fmtPct(data.positive_clv_rate) : "—"} />
        <KpiCard
          label="Avg CLV"
          value={data.avg_clv_percentage != null ? fmtPct(data.avg_clv_percentage) : "—"}
          context={data.median_clv_percentage != null ? `mediana ${fmtPct(data.median_clv_percentage)}` : undefined}
        />
      </div>

      {marketRows.length > 0 ? (
        <Card
          title="CLV por mercado"
          hint="Média de CLV% por mercado — apenas linhas com fechamento válido entram na média"
          padded={false}
        >
          <div className="p-3">
            <TableShell className="max-h-[240px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start">Mercado</Th>
                    <Th align="end" title="Linhas apostáveis do snapshot">Linhas</Th>
                    <Th align="end" title="Linhas com fechamento válido observado">Com CLV</Th>
                    <Th align="end" title="Média de CLV% das linhas com fechamento">Avg CLV %</Th>
                  </Tr>
                </THead>
                <TBody>
                  {marketRows.map(([market, m]) => (
                    <Tr key={market}>
                      <Td mono align="start" className="text-ink-2">{market}</Td>
                      <Td mono align="end">{fmtInt(m.n)}</Td>
                      <Td mono align="end">{fmtInt(m.with_clv)}</Td>
                      <Td
                        mono
                        align="end"
                        className={
                          m.avg_clv_percentage != null && m.avg_clv_percentage > 0
                            ? "text-pos-400"
                            : "text-ink-2"
                        }
                      >
                        {m.avg_clv_percentage != null ? fmtPct(m.avg_clv_percentage) : "—"}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
          </div>
        </Card>
      ) : null}

      <Card
        title="Entradas de CLV"
        hint={`${fmtInt(data.total_bets)} bets · coverage ${
          data.coverage != null ? fmtPct(data.coverage) : "não medido"
        } · gerado em ${fmtDateTime(data.generated_at)}${
          data.lifecycle
            ? ` · ciclo de vida: PENDING ${data.lifecycle.PENDING ?? 0} · NO_CLOSE ${
                data.lifecycle.NO_CLOSE ?? 0
              } · CLOSED ${data.lifecycle.CLOSED ?? 0} · INVALID ${
                data.lifecycle.INVALID ?? 0
              } · MISMATCH ${data.lifecycle.MISMATCH ?? 0}`
            : ""
        }`}
        padded={false}
        action={
          <div className="flex items-center gap-2">
            <SearchInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onClear={() => setQuery("")}
              placeholder="Buscar jogo, mercado ou resultado…"
              aria-label="Buscar entradas de CLV"
              className="w-[280px]"
            />
            <div className="flex items-center gap-1.5">
              {clvStatusFilter.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setFilterStatus(f.value)}
                  className={cn(
                    "label-caps px-2 py-1 rounded text-[11.5px]",
                    filterStatus === f.value
                      ? "bg-accent-400/8 text-accent-300"
                      : "bg-surface-2 text-ink-3 hover:bg-surface-3",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <div className="p-3">
          {entries.length === 0 ? (
            <EmptyState
              title="Sem entradas de CLV"
              hint={
                data.total_bets === 0
                  ? "Sem linhas apostáveis observadas: não há população para medir CLV."
                  : "CLV exige odd de entrada observada antes da decisão e fechamento observado antes do kickoff."
              }
            />
          ) : (
            <TableShell className="max-h-[440px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start">Jogo</Th>
                    <Th align="start">Mercado</Th>
                    <Th align="center">Odd entrada</Th>
                    <Th align="start" title="Timestamp real da observação de entrada (nunca o instante da previsão)">Entrada em</Th>
                    <Th align="center">Odd fechamento</Th>
                    <Th align="start" title="Casa da odd de fechamento observada">Fechou em</Th>
                    <Th align="end">CLV %</Th>
                    <Th align="center">Status</Th>
                  </Tr>
                </THead>
                <TBody>
                  {entries.map((e, i) => (
                    <Tr key={`${e.match}-${e.market}-${e.outcome}-${i}`}>
                      <Td align="start" className="font-medium text-ink">{e.match}</Td>
                      <Td mono className="text-ink-3">{e.market}</Td>
                      <Td mono className="text-ink-2">
                        {e.entry_odd != null ? fmtOdd(e.entry_odd) : "—"}
                      </Td>
                      <Td className="text-ink-3">
                        {e.entry_timestamp ? fmtDateTime(e.entry_timestamp) : "—"}
                      </Td>
                      <Td mono className="text-ink-2">
                        <Tooltip
                          content={
                            e.closing_timestamp
                              ? `Fechamento observado em ${fmtDateTime(e.closing_timestamp)}`
                              : "Sem timestamp de fechamento observado"
                          }
                        >
                          <span>{e.closing_odd != null ? fmtOdd(e.closing_odd) : "—"}</span>
                        </Tooltip>
                      </Td>
                      <Td className="text-ink-3">{e.closing_bookmaker ?? "—"}</Td>
                      <Td mono className={e.clv_percentage != null && e.clv_percentage > 0 ? "text-pos-400" : "text-ink-2"}>
                        {e.clv_percentage != null ? fmtPct(e.clv_percentage) : "—"}
                      </Td>
                      <Td align="center">
                        <Badge
                          tone={e.status === "OK" ? "accent" : "neutral"}
                          size="sm"
                        >
                          {clvStatusLabel[e.status] ?? e.status}
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
