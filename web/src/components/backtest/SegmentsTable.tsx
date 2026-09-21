/**
 * SegmentsTable — performance por segmento.
 *
 * Permite descobrir ONDE o modelo funciona e onde nao funciona:
 * competicao, mercado, faixa de odd, faixa de probabilidade, faixa de EV,
 * confianca, mes e temporada.
 */

import { useMemo, useState } from "react";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { Select } from "@/components/ui/Input";
import { Table, TableShell, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { EmptyState } from "@/components/ui/States";
import type { SegmentRow } from "@/types/backtest";
import { cn } from "@/utils/cn";
import { fmtInt, fmtNum, fmtPct, fmtPctSigned } from "@/utils/format";

const DIMENSION_LABELS: Record<string, string> = {
  competicao: "Competição",
  temporada: "Temporada",
  mercado: "Mercado",
  confianca: "Confiança",
  faixa_odd: "Faixa de odd",
  faixa_probabilidade: "Faixa de probabilidade",
  faixa_ev: "Faixa de EV",
  mes: "Mês",
};

const DIMENSION_ORDER = [
  "mercado",
  "confianca",
  "faixa_ev",
  "faixa_odd",
  "faixa_probabilidade",
  "mes",
  "competicao",
  "temporada",
];

export default function SegmentsTable({ rows }: { rows: SegmentRow[] }) {
  const dimensions = useMemo(() => {
    const present = new Set(rows.map((r) => r.dimension));
    const ordered = DIMENSION_ORDER.filter((d) => present.has(d));
    const rest = [...present].filter((d) => !DIMENSION_ORDER.includes(d)).sort();
    return [...ordered, ...rest];
  }, [rows]);

  const [dimension, setDimension] = useState(dimensions[0] ?? "mercado");

  const filtered = useMemo(
    () => rows.filter((r) => r.dimension === dimension),
    [rows, dimension],
  );

  if (rows.length === 0) {
    return (
      <Card title="Performance por segmento">
        <EmptyState
          title="Sem segmentos"
          hint="Nenhum sinal liquidado para segmentar."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Performance por segmento"
      hint="Onde o modelo acerta e onde erra · IC 95% por Wilson"
      padded={false}
      action={
        <Select
          aria-label="Dimensão de segmentação"
          options={dimensions.map((d) => ({
            value: d,
            label: DIMENSION_LABELS[d] ?? d,
          }))}
          value={dimension}
          onChange={(e) => setDimension(e.target.value)}
          className="w-[190px]"
        />
      }
    >
      <div className="p-3">
        <TableShell className="max-h-[420px]">
          <Table>
            <THead>
              <Tr className="h-[34px] hover:bg-transparent">
                <Th align="start" width={200}>
                  Segmento
                </Th>
                <Th align="end" width={80}>
                  Sinais
                </Th>
                <Th align="end" width={100} title="Frequência observada de acerto">
                  Taxa
                </Th>
                <Th align="end" width={110} title="Probabilidade média prevista">
                  Previsto
                </Th>
                <Th align="end" width={100} title="EV médio previsto">
                  EV médio
                </Th>
                <Th align="end" width={100}>
                  Brier
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
              {filtered.map((r) => {
                const gap = r.observed_rate - r.avg_predicted;
                return (
                  <Tr key={`${r.dimension}|${r.segment}`}>
                    <Td align="start" className="max-w-[200px]">
                      <span className="block truncate text-ink" title={r.segment}>
                        {r.segment}
                      </span>
                    </Td>
                    <Td mono className="text-ink-2">
                      {fmtInt(r.n)}
                    </Td>
                    <Td mono className="text-ink">
                      {fmtPct(r.observed_rate)}
                    </Td>
                    <Td mono className="text-accent-300">
                      {fmtPct(r.avg_predicted)}
                    </Td>
                    <Td mono className="text-ink-2">
                      {fmtPctSigned(r.avg_ev)}
                    </Td>
                    <Td mono className={cn("text-ink-2", r.brier > 0.25 && "text-warn-300")}>
                      {fmtNum(r.brier, 4)}
                    </Td>
                    <Td mono className="text-ink-3">
                      {fmtPct(r.ci_low)} – {fmtPct(r.ci_high)}
                      <span
                        className={cn(
                          "ms-1.5",
                          Math.abs(gap) > 0.07 ? "text-neg-400" : "text-ink-4",
                        )}
                        title={`desvio ${fmtPctSigned(gap)}`}
                      >
                        {gap > 0 ? "▲" : gap < 0 ? "▼" : "="}
                      </span>
                    </Td>
                    <Td align="start">
                      {r.sufficient ? (
                        <Badge tone="positive" size="sm">
                          ok
                        </Badge>
                      ) : (
                        <Badge tone="warning" size="sm">
                          insuficiente
                        </Badge>
                      )}
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableShell>
        <p className="mt-2.5 text-[11px] leading-relaxed text-ink-4">
          ▲ observado acima do previsto · ▼ abaixo. Brier acima de 0,25 indica
          desempenho pior que um chute fixo de 50%. Segmentos com poucos sinais
          não sustentam conclusão.
        </p>
      </div>
    </Card>
  );
}
