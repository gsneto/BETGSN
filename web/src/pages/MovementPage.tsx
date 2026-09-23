/**
 * MovementPage — tela de MOVIMENTO de odds.
 *
 * Mostra o movimento de preços das odds ao longo do tempo,
 * com direção, dispersão e velocidade. Todos os dados de
 * GET /api/movement.
 *
 * HONESTIDADE: movimento exige SÉRIE TEMPORAL de observações com
 * timestamp. Uma única observação não prova movimento — o status
 * INSUFFICIENT_DATA do backend aparece explicitamente, e colunas de
 * movimento ficam "—" em vez de fingir estabilidade.
 */

import { useMemo, useState } from "react";
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
  Tooltip,
  Tr,
} from "@/components/ui";
import { SearchInput } from "@/components/ui/Input";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import { cn } from "@/utils/cn";
import { fmtDateTime, fmtInt, fmtNum, fmtOdd, fmtPct } from "@/utils/format";

/** Rótulos dos estados de movimento — mesmos enums do backend. */
const movementStatusLabel: Record<string, string> = {
  MOVING: "Movendo",
  STABLE: "Estável",
  NO_DATA: "Sem dados",
  INSUFFICIENT_DATA: "Dado insuficiente",
};

export default function MovementPage() {
  const { dataVersion } = useStore();
  const [query, setQuery] = useState("");

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchMovement(signal),
    [dataVersion],
  );

  const movements = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    if (!q) return data.movements;
    return data.movements.filter(
      (m) =>
        m.match.toLowerCase().includes(q) ||
        m.market.toLowerCase().includes(q) ||
        m.outcome.toLowerCase().includes(q),
    );
  }, [data, query]);

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

  const nObservable = data.movements.filter(
    (m) => m.status === "MOVING" || m.status === "STABLE",
  ).length;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <Card
        title="Movimento de Odds"
        hint={`${fmtInt(data.movements.length)} linhas · ${fmtInt(nObservable)} com série temporal observada · gerado em ${fmtDateTime(data.generated_at)}`}
        padded={false}
        action={
          <SearchInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClear={() => setQuery("")}
            placeholder="Buscar jogo, mercado ou resultado…"
            aria-label="Buscar movimentos"
            className="w-[280px]"
          />
        }
      >
        <div className="p-3">
          {data.movements.length === 0 ? (
            <EmptyState
              title="Sem movimento"
              hint="Dados de movimento requerem observações de odds com timestamps."
            />
          ) : movements.length === 0 ? (
            <EmptyState
              title="Nenhuma linha corresponde à busca"
              hint="Limpe a busca para ver todas as linhas monitoradas."
            />
          ) : (
            <TableShell className="max-h-[480px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start">Jogo</Th>
                    <Th align="start">Mercado</Th>
                    <Th align="center">Resultado</Th>
                    <Th align="center" title="Primeira observação com timestamp no store">Abertura</Th>
                    <Th align="center" title="Preço atual do fixture (sem timestamp quando não há série)">Atual</Th>
                    <Th align="end">Δ</Th>
                    <Th align="end">Δ%</Th>
                    <Th align="center">Direção</Th>
                    <Th align="end" title="Observações com timestamp no store">Obs.</Th>
                    <Th align="end" title="Casas na observação mais recente">Casas</Th>
                    <Th align="end" title="Dispersão de preço entre casas (0 = consenso)">Dispersão</Th>
                    <Th align="end" title="Minutos restantes até o kickoff (quando publicado)">Kickoff</Th>
                    <Th align="center">Status</Th>
                  </Tr>
                </THead>
                <TBody>
                  {movements.map((m, i) => (
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
                      <Td mono align="end" className="text-ink-3">
                        <Tooltip content="Observações persistidas com timestamp para esta linha">
                          <span>{fmtInt(m.n_observations)}</span>
                        </Tooltip>
                      </Td>
                      <Td mono align="end" className="text-ink-3">
                        {m.n_books > 0 ? fmtInt(m.n_books) : "—"}
                      </Td>
                      <Td mono align="end" className="text-ink-3">
                        {m.book_dispersion != null ? fmtNum(m.book_dispersion) : "—"}
                      </Td>
                      <Td mono align="end" className="text-ink-3">
                        {m.minutes_to_kickoff != null
                          ? m.minutes_to_kickoff >= 0
                            ? `${fmtInt(m.minutes_to_kickoff)} min`
                            : "iniciado"
                          : "—"}
                      </Td>
                      <Td align="center">
                        <Badge
                          tone={m.status === "MOVING" ? "accent" : "neutral"}
                          size="sm"
                        >
                          {movementStatusLabel[m.status] ?? m.status}
                        </Badge>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
          )}
          {data.movements.length > 0 ? (
            <p className="mt-2 text-[11px] text-ink-4">
              Linhas com status "Sem dados" ou "Dado insuficiente" não têm série
              temporal de odds suficiente — o preço atual é exibido, mas nada de
              movimento é calculado sobre o que não foi observado.
            </p>
          ) : null}
        </div>
      </Card>
    </div>
  );
}
