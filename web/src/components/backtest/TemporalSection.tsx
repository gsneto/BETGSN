/**
 * TemporalSection — evolucao da simulacao e drawdown ao longo do tempo.
 *
 * Objetivo principal: descobrir se o desempenho esta concentrado em uma
 * janela pequena ou se sustenta em periodos diferentes.
 *
 * Deixa explicito que e SIMULACAO HISTORICA, nao previsao de lucro.
 */

import { useMemo, useState } from "react";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { ChartLegend, LineChart, type LineSeries } from "@/components/ui/Charts";
import SegmentedControl, { type Segment } from "@/components/ui/SegmentedControl";
import { Table, TableShell, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { EmptyState } from "@/components/ui/States";
import type { Simulation, TemporalBucket, TemporalGranularity } from "@/types/backtest";
import { cn } from "@/utils/cn";
import { fmtInt, fmtNum, fmtPct, fmtPctSigned, signedColorClass } from "@/utils/format";

const GRANULARITY: Segment<TemporalGranularity>[] = [
  { value: "day", label: "Dia" },
  { value: "week", label: "Semana" },
  { value: "month", label: "Mês" },
];

export default function TemporalSection({
  simulation,
  temporal,
}: {
  simulation: Simulation;
  temporal: Record<TemporalGranularity, TemporalBucket[]>;
}) {
  const [granularity, setGranularity] = useState<TemporalGranularity>("week");
  const series = temporal[granularity] ?? [];

  const equitySeries = useMemo<LineSeries[]>(
    () => [
      {
        name: "Capital virtual",
        values: simulation.equity.map((p) => p.bankroll),
        tone: "accent",
      },
    ],
    [simulation.equity],
  );

  const drawdownSeries = useMemo<LineSeries[]>(
    () => [
      {
        name: "Drawdown",
        values: simulation.equity.map((p) => -p.drawdown),
        tone: "negative",
      },
    ],
    [simulation.equity],
  );

  const labels = simulation.equity.map((p) => p.day);

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card
          title="Evolução da simulação"
          hint="Capital virtual ao longo do período testado"
          action={
            <Badge tone="warning" size="sm">
              SIMULAÇÃO HISTÓRICA
            </Badge>
          }
        >
          {simulation.equity.length === 0 ? (
            <EmptyState title="Sem curva de capital" hint="Nenhum sinal liquidado no período." />
          ) : (
            <>
              <LineChart
                series={equitySeries}
                labels={labels}
                height={220}
                referenceValue={simulation.initial_bankroll}
                formatValue={(v) => fmtNum(v, 0)}
              />
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <ChartLegend series={equitySeries} />
                <span className="text-[11px] text-ink-4">
                  linha tracejada = banca inicial ({fmtNum(simulation.initial_bankroll)})
                </span>
              </div>
            </>
          )}
        </Card>

        <Card
          title="Drawdown"
          hint="Queda em relação ao topo da curva"
          action={
            <Badge tone="negative" size="sm">
              máx. {fmtPct(simulation.max_drawdown)}
            </Badge>
          }
        >
          {simulation.equity.length === 0 ? (
            <EmptyState title="Sem drawdown" hint="Nenhum sinal liquidado." />
          ) : (
            <>
              <LineChart
                series={drawdownSeries}
                labels={labels}
                height={220}
                formatValue={(v) => fmtPct(v, 0)}
              />
              <p className="mt-2 text-[11px] text-ink-4">
                Pior dia: {simulation.max_drawdown_day || "—"}
              </p>
            </>
          )}
        </Card>
      </div>

      <Card
        title="Performance temporal"
        hint="Acerto e retorno por janela — revela concentração em poucos períodos"
        padded={false}
        action={
          <SegmentedControl
            ariaLabel="Granularidade temporal"
            segments={GRANULARITY}
            value={granularity}
            onChange={setGranularity}
          />
        }
      >
        <div className="p-3">
          {series.length === 0 ? (
            <EmptyState
              title="Nenhuma janela com sinais"
              hint="O período testado não produziu sinais liquidados."
            />
          ) : (
            <TableShell className="max-h-[360px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start" width={130}>
                      Janela
                    </Th>
                    <Th align="end" width={80}>
                      Sinais
                    </Th>
                    <Th align="end" width={100}>
                      Taxa
                    </Th>
                    <Th align="end" width={100}>
                      EV médio
                    </Th>
                    <Th align="end" width={120}>
                      Retorno
                    </Th>
                    <Th align="end" width={120}>
                      Lucro
                    </Th>
                    <Th align="end" width={120}>
                      Capital
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {series.map((t) => (
                    <Tr key={t.label}>
                      <Td align="start" mono className="text-ink-2">
                        {t.label}
                      </Td>
                      <Td mono className="text-ink">
                        {fmtInt(t.n)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtPct(t.hit_rate)}
                      </Td>
                      <Td mono className="text-accent-300">
                        {fmtPctSigned(t.avg_ev)}
                      </Td>
                      <Td mono className={cn("font-semibold", signedColorClass(t.realized_return))}>
                        {fmtPctSigned(t.realized_return)}
                      </Td>
                      <Td mono className={signedColorClass(t.profit)}>
                        {fmtNum(t.profit)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {t.bankroll_end === null ? "—" : fmtNum(t.bankroll_end, 0)}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
          )}
          <p className="mt-2.5 text-[11px] leading-relaxed text-ink-4">
            Janelas com poucos sinais produzem taxas instáveis. Um desempenho
            concentrado em poucas janelas é sinal de fragilidade, mesmo que o
            total pareça bom.
          </p>
        </div>
      </Card>
    </div>
  );
}
