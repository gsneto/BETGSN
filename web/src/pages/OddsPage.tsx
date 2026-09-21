/**
 * OddsPage — tela CASAS / ODDS.
 *
 * Paridade com a aba CASAS/ODDS da GUI legada (`_page_odds`):
 * seletor de jogo, seletor de mercado, tabela multi-casa com destaque da
 * melhor odd por resultado e painel de arbitragem com as stakes por
 * perna, calculadas pelo `scan_arbitrage` do backend.
 *
 * Acrescenta um resumo por casa (mercados cobertos, odds lideres, margem
 * media, sinais vencedores) que vem de GET /api/odds.
 */

import { useEffect, useMemo, useState } from "react";
import { fetchMarketComparison, fetchOddsOverview } from "@/api/odds";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import ProbBar from "@/components/ui/ProbBar";
import { Select } from "@/components/ui/Input";
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
import { fmtDateTime, fmtInt, fmtOdd, fmtPct, fmtPctSigned } from "@/utils/format";

export default function OddsPage() {
  const { dataVersion } = useStore();
  const [match, setMatch] = useState("");
  const [market, setMarket] = useState("");

  const overview = useApiResource(
    (signal) => fetchOddsOverview(signal),
    [dataVersion],
  );

  const matches = useMemo(() => overview.data?.matches ?? [], [overview.data]);
  const markets = useMemo(
    () => (match ? (overview.data?.markets_by_match[match] ?? []) : []),
    [overview.data, match],
  );

  // mantem as selecoes validas quando o snapshot muda
  useEffect(() => {
    if (matches.length > 0 && !matches.includes(match)) {
      setMatch(matches[0]);
    }
  }, [matches, match]);

  useEffect(() => {
    if (markets.length > 0 && !markets.includes(market)) {
      setMarket(markets[0]);
    }
  }, [markets, market]);

  const comparison = useApiResource(
    (signal) =>
      match
        ? fetchMarketComparison(match, market || undefined, signal)
        : Promise.resolve(null),
    [match, market, dataVersion],
  );

  if (overview.initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-[52px] w-full" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }

  if (overview.error && !overview.data) {
    return <ErrorPanel message={overview.error} onRetry={overview.reload} />;
  }

  const data = overview.data;
  if (!data) return null;

  const cmp = comparison.data;
  const arb = cmp?.arbitrage;

  return (
    <div className="flex flex-col gap-3">
      {overview.error ? (
        <ErrorPanel message={overview.error} onRetry={overview.reload} />
      ) : null}

      <Card
        title="Comparação de odds entre casas"
        hint={`${fmtInt(data.matches.length)} jogos · ${fmtInt(data.bookmakers.length)} casas · atualizado ${fmtDateTime(data.generated_at)}`}
        padded={false}
        action={
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2">
              <span className="label-caps">Jogo</span>
              <Select
                value={match}
                onChange={(e) => setMatch(e.target.value)}
                options={data.matches.map((m) => ({ value: m, label: m }))}
                className="w-[260px]"
                aria-label="Selecionar jogo"
              />
            </label>
            <label className="flex items-center gap-2">
              <span className="label-caps">Mercado</span>
              <Select
                value={market}
                onChange={(e) => setMarket(e.target.value)}
                options={markets.map((m) => ({ value: m, label: m }))}
                className="w-[200px]"
                aria-label="Selecionar mercado"
              />
            </label>
          </div>
        }
      >
        <div className="flex flex-col gap-3 p-3">
          {comparison.loading && !cmp ? (
            <Skeleton className="h-[360px] w-full" />
          ) : comparison.error ? (
            <ErrorPanel message={comparison.error} onRetry={comparison.reload} />
          ) : cmp ? (
            <>
              <TableShell className="max-h-[440px]">
                <Table>
                  <THead>
                    <Tr className="h-[34px] hover:bg-transparent">
                      <Th width={170} align="start">
                        Casa
                      </Th>
                      {cmp.outcomes.map((oc) => (
                        <Th key={oc} width={110} align="center" title={`Resultado ${oc}`}>
                          {oc}
                        </Th>
                      ))}
                      <Th width={100} align="end" title="Margem média da casa neste mercado">
                        Margem
                      </Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {cmp.rows.map((row) => (
                      <Tr key={row.book}>
                        <Td align="start">
                          <span className="font-medium text-ink">{row.book}</span>
                          {row.best_outcomes.length > 0 ? (
                            <Badge tone="accent" size="sm" className="ms-2">
                              melhor
                            </Badge>
                          ) : null}
                        </Td>
                        {cmp.outcomes.map((oc) => {
                          const isBest = cmp.best_books[oc] === row.book;
                          const odd = row.odds[oc];
                          return (
                            <Td
                              key={oc}
                              align="center"
                              mono
                              className={cn(
                                isBest
                                  ? "bg-accent-400/8 font-semibold text-accent-300"
                                  : "text-ink-2",
                              )}
                            >
                              {odd > 1 ? fmtOdd(odd) : "—"}
                            </Td>
                          );
                        })}
                        <Td mono className="text-ink-3">
                          {fmtPct(row.margin, 2)}
                        </Td>
                      </Tr>
                    ))}

                    {/* linha consolidada: melhor odd de cada resultado */}
                    <Tr className="bg-surface-2">
                      <Td align="start">
                        <span className="label-caps">Melhor odd</span>
                      </Td>
                      {cmp.outcomes.map((oc) => (
                        <Td key={oc} align="center" mono className="font-semibold text-accent-300">
                          {cmp.best_odds[oc] ? fmtOdd(cmp.best_odds[oc]) : "—"}
                        </Td>
                      ))}
                      <Td align="end" className="text-ink-4">
                        —
                      </Td>
                    </Tr>
                  </TBody>
                </Table>
              </TableShell>

              {/* modelo x mercado por resultado */}
              <div className="panel p-3">
                <p className="label-caps mb-2.5">
                  Modelo x mercado por resultado (mesma escala 0–100%)
                </p>
                <ul className="flex flex-col gap-2">
                  {cmp.outcomes.map((oc) => (
                    <li key={oc} className="flex items-center gap-3">
                      <span className="w-[150px] shrink-0 truncate text-[12.5px] text-ink-2" title={oc}>
                        {oc}
                      </span>
                      <span className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="flex items-center gap-2">
                          <ProbBar value={cmp.model_probs[oc] ?? 0} tone="model" className="flex-1" />
                          <span className="num w-[52px] shrink-0 text-end text-[11.5px] text-accent-300">
                            {fmtPct(cmp.model_probs[oc] ?? 0)}
                          </span>
                        </span>
                        <span className="flex items-center gap-2">
                          <ProbBar value={cmp.market_probs[oc] ?? 0} tone="market" className="flex-1" />
                          <span className="num w-[52px] shrink-0 text-end text-[11.5px] text-ink-3">
                            {fmtPct(cmp.market_probs[oc] ?? 0)}
                          </span>
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2.5 text-[11px] text-ink-4">
                  Âmbar = probabilidade do modelo · cinza = consenso de mercado sem vig
                </p>
              </div>

              {/* arbitragem */}
              <div
                className={cn(
                  "rounded-lg border p-3",
                  arb?.arbitrage
                    ? "border-accent-700/50 bg-accent-400/6"
                    : "border-line bg-surface-2",
                )}
              >
                <p className="label-caps mb-2">Arbitragem</p>
                {arb?.arbitrage ? (
                  <>
                    <p className="num text-body font-semibold text-accent-300">
                      ARBITRAGEM DETECTADA — margem garantida de {fmtPct(arb.margin, 2)}
                    </p>
                    <ul className="mt-2 flex flex-col gap-1">
                      {arb.legs.map((leg) => (
                        <li key={leg.outcome} className="num text-[12.5px] text-ink-2">
                          {leg.outcome} @ {leg.book} · odd {fmtOdd(leg.odd)} → stake{" "}
                          {fmtOdd(leg.stake)} (retorno {fmtOdd(leg.payout)})
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="num text-body text-ink-3">
                    Sem arbitragem neste mercado. Margem das melhores odds:{" "}
                    {fmtPctSigned(arb?.margin ?? 0, 2)} (negativo = sem arb).
                  </p>
                )}
              </div>
            </>
          ) : (
            <EmptyState
              title="Escolha um jogo"
              hint="As odds multi-casa aparecem aqui."
            />
          )}
        </div>
      </Card>

      {/* resumo por casa */}
      <Card
        title="Resumo por casa na rodada"
        hint="Cobertura de mercados, odds líderes e margem média de cada fonte"
        padded={false}
      >
        <div className="p-3">
          <TableShell className="max-h-[360px]">
            <Table>
              <THead>
                <Tr className="h-[34px] hover:bg-transparent">
                  <Th width={180} align="start">
                    Casa
                  </Th>
                  <Th width={120} align="end" title="Mercados em que a casa aparece">
                    Mercados
                  </Th>
                  <Th width={120} align="end" title="Vezes em que ofereceu a melhor odd">
                    Odds líderes
                  </Th>
                  <Th width={120} align="end" title="Participação nas melhores odds">
                    Share
                  </Th>
                  <Th width={120} align="end" title="Margem média por grupo de mercado">
                    Margem média
                  </Th>
                  <Th width={120} align="end" title="Sinais que apontam para esta casa">
                    Sinais
                  </Th>
                </Tr>
              </THead>
              <TBody>
                {data.bookmakers.map((b) => (
                  <Tr key={b.book}>
                    <Td align="start" className="font-medium text-ink">
                      {b.book}
                    </Td>
                    <Td mono className="text-ink-3">
                      {fmtInt(b.n_markets)}
                    </Td>
                    <Td mono className={cn(b.n_best_odds > 0 ? "text-accent-300" : "text-ink-4")}>
                      {fmtInt(b.n_best_odds)}
                    </Td>
                    <Td mono className="text-ink-3">
                      {fmtPct(b.best_odd_share)}
                    </Td>
                    <Td mono className="text-ink-2">
                      {fmtPct(b.avg_margin, 2)}
                    </Td>
                    <Td mono className={cn(b.signals_won > 0 ? "text-pos-400" : "text-ink-4")}>
                      {b.signals_won > 0 ? fmtInt(b.signals_won) : "—"}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableShell>
        </div>
      </Card>
    </div>
  );
}
