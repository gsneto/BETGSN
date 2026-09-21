/**
 * MovementPage — tela de MOVIMENTO de odds.
 *
 * Mostra o movimento de preços das odds ao longo do tempo,
 * com direção, dispersão e velocidade. Todos os dados de
 * GET /api/movement.
 */

import { fetchMovement } from "@/api/movement";
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
import { fmtOdd, fmtPct } from "@/utils/format";

export default function MovementPage() {
  const { dataVersion } = useStore();

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchMovement(signal),
    [dataVersion],
  );

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
        title="Movimento de Odds"
        hint={`${data.movements.length} movimentos · atualizado ${data.generated_at}`}
        padded={false}
      >
        <div className="p-3">
          {data.movements.length === 0 ? (
            <EmptyState
              title="Sem movimento"
              hint="Dados de movimento requerem observações de odds com timestamps."
            />
          ) : (
            <TableShell className="max-h-[440px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start">Jogo</Th>
                    <Th align="start">Mercado</Th>
                    <Th align="center">Resultado</Th>
                    <Th align="center">Abertura</Th>
                    <Th align="center">Atual</Th>
                    <Th align="end">Δ</Th>
                    <Th align="end">Δ%</Th>
                    <Th align="center">Direção</Th>
                    <Th align="center">Status</Th>
                  </Tr>
                </THead>
                <TBody>
                  {data.movements.map((m, i) => (
                    <Tr key={`${m.match}-${m.market}-${m.outcome}-${i}`}>
                      <Td align="start" className="font-medium text-ink">{m.match}</Td>
                      <Td mono className="text-ink-3">{m.market}</Td>
                      <Td mono className="text-ink-2">{m.outcome}</Td>
                      <Td mono className="text-ink-2">
                        {m.opening_odd != null ? fmtOdd(m.opening_odd) : "—"}
                      </Td>
                      <Td mono className="text-ink-2">
                        {m.current_odd != null ? fmtOdd(m.current_odd) : "—"}
                      </Td>
                      <Td mono className={cn(
                        m.price_delta != null && m.price_delta > 0
                          ? "text-pos-400"
                          : m.price_delta != null && m.price_delta < 0
                            ? "text-neg-400"
                            : "text-ink-3",
                      )}>
                        {m.price_delta != null ? (m.price_delta > 0 ? "+" : "") + fmtOdd(m.price_delta) : "—"}
                      </Td>
                      <Td mono className="text-ink-2">
                        {m.price_delta_pct != null ? fmtPct(m.price_delta_pct) : "—"}
                      </Td>
                      <Td align="center">
                        {m.market_direction != null ? (
                          <span className={cn(
                            m.market_direction > 0 ? "text-pos-400" : "text-neg-400",
                          )}>
                            {m.market_direction > 0 ? "▲" : m.market_direction < 0 ? "▼" : "—"}
                          </span>
                        ) : "—"}
                      </Td>
                      <Td align="center">
                        <Badge
                          tone={m.status === "MOVING" ? "accent" : "neutral"}
                          size="sm"
                        >
                          {m.status}
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
