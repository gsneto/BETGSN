/**
 * CalibrationSection — a parte mais importante do backtest.
 *
 * Responde: "quando o modelo diz 60%, acontece 60% das vezes?"
 * Tabela previsto x observado com IC de Wilson + grafico com a diagonal
 * de calibracao perfeita.
 */

import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { CalibrationChart } from "@/components/ui/Charts";
import { Table, TableShell, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Tooltip } from "@/components/ui/States";
import type { AggregateMetrics, CalibrationBin } from "@/types/backtest";
import { cn } from "@/utils/cn";
import { fmtInt, fmtNum, fmtPct } from "@/utils/format";

/** Desvio entre previsto e observado, em pontos percentuais. */
function gap(bin: CalibrationBin): number {
  return bin.observed_rate - bin.avg_predicted;
}

function gapClass(value: number): string {
  const abs = Math.abs(value);
  if (abs <= 0.03) return "text-pos-400";
  if (abs <= 0.07) return "text-warn-300";
  return "text-neg-400";
}

export default function CalibrationSection({
  bins,
  aggregate,
}: {
  bins: CalibrationBin[];
  aggregate: AggregateMetrics;
}) {
  const chartData = bins.map((b) => ({
    predicted: b.avg_predicted,
    empirical: b.observed_rate,
    n: b.n,
    ci_low: b.ci_low,
    ci_high: b.ci_high,
  }));

  return (
    <Card
      title="Calibração do modelo"
      hint="Probabilidade prevista vs frequência observada. Quanto mais perto da diagonal, melhor."
      padded={false}
      action={
        <div className="flex items-center gap-2">
          <Badge tone="neutral" size="sm">
            Brier {fmtNum(aggregate.brier, 4)}
          </Badge>
          <Badge tone="neutral" size="sm">
            Log-loss {fmtNum(aggregate.logloss, 4)}
          </Badge>
          <Badge
            tone={Math.abs(aggregate.ev_gap) <= 0.02 ? "positive" : "warning"}
            size="sm"
          >
            gap EV {fmtPct(aggregate.ev_gap)}
          </Badge>
        </div>
      }
    >
      <div className="grid grid-cols-1 gap-4 p-3 xl:grid-cols-[280px_minmax(0,1fr)]">
        <div className="flex flex-col items-center gap-2">
          <CalibrationChart bins={chartData} size={240} showBands />
          <p className="text-center text-[11px] leading-relaxed text-ink-4">
            Diagonal tracejada = calibração perfeita. Barras verticais = IC 95%
            (Wilson). Tamanho do ponto = volume da faixa.
          </p>
        </div>

        <TableShell className="max-h-[420px]">
          <Table>
            <THead>
              <Tr className="h-[34px] hover:bg-transparent">
                <Th align="start" width={110}>
                  Faixa
                </Th>
                <Th align="end" width={90}>
                  Sinais
                </Th>
                <Th align="end" width={110} title="Probabilidade média prevista pelo modelo">
                  Previsto
                </Th>
                <Th align="end" width={110} title="Frequência com que o resultado ocorreu">
                  Observado
                </Th>
                <Th align="end" width={100} title="Observado − previsto, em pontos percentuais">
                  Desvio
                </Th>
                <Th align="end" width={150}>
                  IC 95%
                </Th>
                <Th align="start" width={110}>
                  Amostra
                </Th>
              </Tr>
            </THead>
            <TBody>
              {bins.map((b) => (
                <Tr key={b.label}>
                  <Td align="start" mono className="text-ink-2">
                    {b.label}
                  </Td>
                  <Td mono className="text-ink">
                    {fmtInt(b.n)}
                  </Td>
                  <Td mono className="text-accent-300">
                    {fmtPct(b.avg_predicted)}
                  </Td>
                  <Td mono className="text-ink">
                    {fmtPct(b.observed_rate)}
                  </Td>
                  <Td mono className={cn("font-semibold", gapClass(gap(b)))}>
                    {gap(b) > 0 ? "+" : ""}
                    {fmtPct(gap(b))}
                  </Td>
                  <Td mono className="text-ink-3">
                    {fmtPct(b.ci_low)} – {fmtPct(b.ci_high)}
                  </Td>
                  <Td align="start">
                    {b.sufficient ? (
                      <Badge tone="positive" size="sm">
                        ok
                      </Badge>
                    ) : (
                      <Tooltip content="Menos de 30 sinais nesta faixa: a estimativa é instável.">
                        <span>
                          <Badge tone="warning" size="sm">
                            insuficiente
                          </Badge>
                        </span>
                      </Tooltip>
                    )}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableShell>
      </div>
    </Card>
  );
}
