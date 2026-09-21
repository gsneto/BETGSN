/**
 * EvAnalysisSection — o EV previsto tem poder preditivo?
 *
 * Separa os sinais por faixa de EV e mostra o comportamento observado.
 * Leitura: se as faixas de EV mais alto entregam retorno realizado maior,
 * o EV carrega informacao. Se nao, o EV esta mal calibrado.
 *
 * IMPORTANTE: e associacao dentro deste dataset historico, nao causalidade.
 * Amostra pequena em uma faixa torna a estimativa instavel — por isso o
 * aviso de amostra insuficiente aparece na propria linha.
 */

import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { HorizontalBars } from "@/components/ui/Charts";
import { Table, TableShell, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { EvBucket } from "@/types/backtest";
import { cn } from "@/utils/cn";
import { fmtInt, fmtPct, fmtPctSigned, signedColorClass } from "@/utils/format";

export default function EvAnalysisSection({ buckets }: { buckets: EvBucket[] }) {
  const usable = buckets.filter((b) => b.sufficient);
  const monotonic =
    usable.length >= 2 &&
    usable.every((b, i) => i === 0 || b.avg_realized_return > usable[i - 1].avg_realized_return);

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <Card
        title="Análise do EV previsto"
        hint="Faixas de EV vs comportamento observado"
        padded={false}
        action={
          <Badge tone={monotonic ? "positive" : "neutral"} size="sm">
            {monotonic ? "crescente com o EV" : "sem ordem clara"}
          </Badge>
        }
      >
        <div className="p-3">
          <TableShell>
            <Table>
              <THead>
                <Tr className="h-[34px] hover:bg-transparent">
                  <Th align="start" width={90}>
                    Faixa EV
                  </Th>
                  <Th align="end" width={80}>
                    Sinais
                  </Th>
                  <Th align="end" width={100}>
                    EV médio
                  </Th>
                  <Th align="end" width={110} title="Frequência de acerto na faixa">
                    Taxa
                  </Th>
                  <Th align="end" width={120} title="Retorno médio por unidade apostada">
                    Retorno real
                  </Th>
                  <Th align="end" width={140}>
                    IC 95%
                  </Th>
                </Tr>
              </THead>
              <TBody>
                {buckets.map((b) => (
                  <Tr key={b.label}>
                    <Td align="start" mono className="text-ink-2">
                      {b.label}
                    </Td>
                    <Td mono className="text-ink">
                      {fmtInt(b.n)}
                    </Td>
                    <Td mono className="text-accent-300">
                      {fmtPctSigned(b.avg_ev)}
                    </Td>
                    <Td mono className="text-ink-2">
                      {fmtPct(b.observed_rate)}
                    </Td>
                    <Td
                      mono
                      className={cn("font-semibold", signedColorClass(b.avg_realized_return))}
                    >
                      {fmtPctSigned(b.avg_realized_return)}
                    </Td>
                    <Td mono className="text-ink-3">
                      {fmtPct(b.ci_low)} – {fmtPct(b.ci_high)}
                      {!b.sufficient ? (
                        <span className="ms-1.5 text-warn-300" title="Amostra insuficiente">
                          !
                        </span>
                      ) : null}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableShell>
          <p className="mt-2.5 text-[11px] leading-relaxed text-ink-4">
            O retorno realizado é o resultado médio por unidade apostada. Um EV
            previsto alto que não se traduz em retorno realizado indica que o
            modelo superestima a probabilidade. Isto é associação histórica
            neste dataset — não prova de causalidade nem garantia futura.
          </p>
        </div>
      </Card>

      <Card
        title="Volume por faixa de EV"
        hint="Onde o modelo concentra os sinais"
      >
        <HorizontalBars
          data={buckets.map((b) => ({
            label: `${b.label} (${fmtPctSigned(b.avg_ev)})`,
            value: b.n,
            tone: b.lower >= 0.10 ? "positive" : b.lower >= 0.05 ? "info" : "neutral",
          }))}
          formatValue={(v) => fmtInt(v)}
        />
        <p className="mt-3 text-[11px] leading-relaxed text-ink-4">
          Muitos sinais na faixa mais alta de EV costumam indicar um mercado de
          referência pouco informado (ou o modelo excessivamente confiante),
          não necessariamente muitas oportunidades reais.
        </p>
      </Card>
    </div>
  );
}
