/**
 * MatchDetail — painel lateral de detalhe de um jogo do terminal LIVE.
 *
 * Seções (Fase 17): header, ODDS GRID por casa, MARKET FAIR, MODEL
 * COMPARISON (quando existe snapshot — senão NO_MODEL explícito),
 * MOVEMENT TIMELINE (histórico append-only do store), SINAIS com o
 * PORQUÊ, CLV do evento e problemas de qualidade.
 *
 * FONTES SEPARADAS: MARKET (mediana entre casas), FAIR (devig) e MODEL
 * (pipeline) nunca são misturados em um número único.
 */
import { useMemo, useState } from "react";
import { fetchRealtimeMatch } from "@/api/realtime";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
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
import {
  MatchStatusBadge,
  SIGNAL_TYPE_LABEL,
  SignalStatusBadge,
} from "@/components/live/badges";
import { cn } from "@/utils/cn";
import { fmtDateTime, fmtInt, fmtNum, fmtOdd, fmtPct } from "@/utils/format";
import type { RealtimeSignal } from "@/types/realtime";

function SignalsList({ signals }: { signals: RealtimeSignal[] }) {
  if (signals.length === 0) {
    return (
      <EmptyState
        title="Nenhum sinal neste jogo"
        hint="Sinais só existem quando a evidência existe. Nada é fabricado."
      />
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {signals.map((signal) => (
        <div
          key={signal.signal_id}
          className="rounded border border-line bg-surface-2/50 p-2.5 text-[11.5px]"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent" size="sm">
              {SIGNAL_TYPE_LABEL[signal.signal_type] ?? signal.signal_type}
            </Badge>
            <SignalStatusBadge status={signal.status} />
            <Badge tone="neutral" size="sm">{signal.market}</Badge>
            <span className="num text-ink-3">{signal.selection}</span>
            <span className="ml-auto text-[10.5px] text-ink-4">
              obs: {fmtDateTime(signal.observed_at)}
            </span>
          </div>
          <p className="mt-1.5 text-ink-2">{signal.reason}</p>
          <dl className="mt-1 grid grid-cols-3 gap-2 text-ink-3">
            <div>
              <dt className="text-ink-4">Mercado (mediana)</dt>
              <dd className="num">{signal.market_price != null ? fmtOdd(signal.market_price) : "—"}</dd>
            </div>
            <div>
              <dt className="text-ink-4">Fair (devig)</dt>
              <dd className="num">{signal.fair_price != null ? fmtOdd(signal.fair_price) : "—"}</dd>
            </div>
            <div>
              <dt className="text-ink-4">Modelo</dt>
              <dd className="num">{signal.model_price != null ? fmtOdd(signal.model_price) : "—"}</dd>
            </div>
          </dl>
          <p className="mt-1 text-[10px] text-ink-4">
            casas: {signal.bookmakers.join(", ")} · alpha: {signal.alpha_id} ·
            produção: {signal.production} (sinal informativo — decisão é sua)
          </p>
        </div>
      ))}
    </div>
  );
}

export default function MatchDetail({
  eventKey,
  onClose,
  refreshNonce,
}: {
  eventKey: string;
  onClose: () => void;
  refreshNonce: number;
}) {
  const [marketIndex, setMarketIndex] = useState(0);

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchRealtimeMatch(eventKey, signal),
    [eventKey, refreshNonce],
  );

  const timeline = useMemo(() => {
    if (!data) return [];
    return [...data.movement_timeline].reverse().slice(0, 60);
  }, [data]);

  return (
    <div className="fixed inset-0 z-100 flex justify-end bg-black/45 backdrop-blur-[2px]">
      <div
        role="dialog"
        aria-label="Detalhe do jogo"
        className="flex h-full w-full max-w-[860px] flex-col overflow-y-auto border-l border-line bg-app shadow-2xl"
      >
        <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-line bg-header/95 px-4 py-3 backdrop-blur">
          <div>
            <h2 className="text-sm font-semibold text-ink">
              {data ? `${data.event.home} vs ${data.event.away}` : "Carregando…"}
            </h2>
            {data ? (
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-3">
                <MatchStatusBadge
                  status={data.event.match_status}
                  matched={data.event.matched}
                />
                <span className="num">{fmtDateTime(data.event.kickoff)}</span>
                <span className="text-ink-4">· última atualização {fmtDateTime(data.event.last_update)}</span>
              </div>
            ) : null}
          </div>
          <Button variant="ghost" onClick={onClose} aria-label="Fechar detalhe">
            Fechar ✕
          </Button>
        </header>

        <div className="flex flex-col gap-3 p-4">
          {initialLoading ? (
            <>
              <Skeleton className="h-[220px] w-full" />
              <Skeleton className="h-[160px] w-full" />
            </>
          ) : error && !data ? (
            <ErrorPanel message={error} onRetry={reload} />
          ) : !data ? null : (
            <>
              {/* ODDS GRID + FAIR */}
              <Card
                title="Odds por casa"
                hint={`${fmtInt(data.event.markets.length)} mercado(s) · devig: ${data.event.markets[marketIndex]?.devig_method ?? "—"}`}
                padded={false}
                action={
                  <select
                    aria-label="Selecionar mercado"
                    value={marketIndex}
                    onChange={(e) => setMarketIndex(Number(e.target.value))}
                    className="input h-[32px] w-[200px] text-xs"
                  >
                    {data.event.markets.map((m, i) => (
                      <option key={m.market} value={i}>{m.market}</option>
                    ))}
                  </select>
                }
              >
                <div className="p-3">
                  {(() => {
                    const market = data.event.markets[marketIndex];
                    if (!market) {
                      return <EmptyState title="Sem mercados" hint="Nenhuma quote observada." />;
                    }
                    const books = Array.from(
                      new Set(market.selections.flatMap((s) => s.books.map((b) => b.bookmaker))),
                    ).sort();
                    return (
                      <TableShell className="max-h-[300px]">
                        <Table>
                          <THead>
                            <Tr className="h-[30px] hover:bg-transparent">
                              <Th align="start">Casa</Th>
                              {market.selections.map((s) => (
                                <Th key={s.selection} align="center">{s.selection}</Th>
                              ))}
                            </Tr>
                          </THead>
                          <TBody>
                            {books.map((book) => (
                              <Tr key={book}>
                                <Td align="start" className="text-ink-2">{book}</Td>
                                {market.selections.map((s) => {
                                  const quote = s.books.find((b) => b.bookmaker === book);
                                  return (
                                    <Td key={s.selection} mono align="center" className={cn(
                                      quote && quote.price === s.best.price ? "text-pos-300" : "text-ink-2",
                                    )}>
                                      {quote ? fmtOdd(quote.price) : "—"}
                                    </Td>
                                  );
                                })}
                              </Tr>
                            ))}
                            <Tr className="bg-surface-2/70">
                              <Td align="start" className="font-semibold text-ink">MELHOR</Td>
                              {market.selections.map((s) => (
                                <Td key={s.selection} mono align="center" className="font-semibold text-pos-300">
                                  {fmtOdd(s.best.price)}
                                  <span className="text-[10px] text-ink-4"> @{s.best.bookmaker}</span>
                                </Td>
                              ))}
                            </Tr>
                            <Tr className="bg-surface-2/70">
                              <Td align="start" className="text-ink-2">MEDIANA</Td>
                              {market.selections.map((s) => (
                                <Td key={s.selection} mono align="center" className="text-ink-2">
                                  {fmtOdd(s.median)}
                                </Td>
                              ))}
                            </Tr>
                            <Tr className="bg-surface-2/70">
                              <Td align="start" className="text-ink-2">FAIR (devig)</Td>
                              {market.selections.map((s) => {
                                const prob = market.fair_probabilities[s.selection];
                                return (
                                  <Td key={s.selection} mono align="center" className="text-accent-300">
                                    {prob ? fmtOdd(1 / prob) : "—"}
                                    <span className="text-[10px] text-ink-4"> {prob ? fmtPct(prob) : ""}</span>
                                  </Td>
                                );
                              })}
                            </Tr>
                          </TBody>
                        </Table>
                      </TableShell>
                    );
                  })()}
                  <p className="mt-2 text-[11px] text-ink-4">
                    overround do mercado:{" "}
                    {data.event.markets[marketIndex]?.overround != null
                      ? fmtNum(data.event.markets[marketIndex].overround, 4)
                      : "—"}{" "}
                    · dispersão temporal:{" "}
                    {fmtInt(data.event.markets[marketIndex]?.timestamp_span_seconds ?? 0)}s
                    {!data.event.markets[marketIndex]?.complete ? " · mercado incompleto: fair é aproximação local" : ""}
                  </p>
                </div>
              </Card>

              {/* MARKET vs MODEL — separados */}
              <Card
                title="Mercado vs Modelo"
                hint="Informação quantitativa — não é recomendação de aposta"
              >
                <div className="p-3 text-[11.5px] text-ink-2">
                  {data.model_comparison.status === "MODEL_AVAILABLE" && data.model_comparison.model ? (
                    <>
                      <p className="mb-2 text-ink-3">
                        snapshot do pipeline de {fmtDateTime(data.model_comparison.generated_at ?? "")} ·
                        status do modelo: <Badge tone="warning" size="sm">{data.model_comparison.model_status ?? "EXPERIMENTAL"}</Badge>
                      </p>
                      <TableShell className="max-h-[220px]">
                        <Table>
                          <THead>
                            <Tr className="h-[28px] hover:bg-transparent">
                              <Th align="start">Mercado</Th>
                              <Th align="start">Seleção</Th>
                              <Th align="center">MARKET (mediana)</Th>
                              <Th align="center">FAIR</Th>
                              <Th align="center">MODEL (prob.)</Th>
                              <Th align="center">DIFF (model − market fair)</Th>
                            </Tr>
                          </THead>
                          <TBody>
                            {data.event.markets.flatMap((market) => {
                              const modelProbs = data.model_comparison.model?.markets?.[market.market];
                              if (!modelProbs) return [];
                              return market.selections.map((s) => {
                                const fairProb = market.fair_probabilities[s.selection];
                                const modelProb = modelProbs[s.selection];
                                const diff =
                                  fairProb != null && modelProb != null
                                    ? modelProb - fairProb
                                    : null;
                                return (
                                  <Tr key={`${market.market}-${s.selection}`}>
                                    <Td align="start" className="text-ink-3">{market.market}</Td>
                                    <Td mono className="text-ink-2">{s.selection}</Td>
                                    <Td mono align="center" className="text-ink-2">{fmtOdd(s.median)}</Td>
                                    <Td mono align="center" className="text-ink-3">
                                      {fairProb ? fmtPct(fairProb) : "—"}
                                    </Td>
                                    <Td mono align="center" className="text-info-300">
                                      {modelProb != null ? fmtPct(modelProb) : "—"}
                                    </Td>
                                    <Td mono align="center" className={cn(
                                      diff == null ? "text-ink-4" : diff > 0 ? "text-pos-400" : "text-neg-400",
                                    )}>
                                      {diff != null ? fmtPct(diff, 2) : "—"}
                                    </Td>
                                  </Tr>
                                );
                              });
                            })}
                          </TBody>
                        </Table>
                      </TableShell>
                      <p className="mt-2 text-[11px] text-ink-4">
                        A evidência OOS atual mostra que os modelos experimentais NÃO
                        superam o mercado. A diferença acima é informação, nunca edge validado.
                      </p>
                    </>
                  ) : (
                    <p>
                      <Badge tone="neutral" size="sm">NO_MODEL</Badge>{" "}
                      Não há snapshot do pipeline para este jogo — nada é estimado aqui.
                    </p>
                  )}
                </div>
              </Card>

              {/* MOVEMENT TIMELINE (store append-only) */}
              <Card
                title="Movement timeline"
                hint={`${fmtInt(data.movement_timeline.length)} observações persistidas (append-only)`}
                padded={false}
              >
                <div className="p-3">
                  {timeline.length === 0 ? (
                    <EmptyState
                      title="Sem histórico persistido"
                      hint="As observações deste jogo ainda não passaram por uma captura com snapshot store."
                    />
                  ) : (
                    <TableShell className="max-h-[260px]">
                      <Table>
                        <THead>
                          <Tr className="h-[28px] hover:bg-transparent">
                            <Th align="center">Timestamp</Th>
                            <Th align="center">Min. p/ kickoff</Th>
                            <Th align="start">Mercado</Th>
                            <Th align="start">Seleção</Th>
                            <Th align="start">Casa</Th>
                            <Th align="center">Preço</Th>
                            <Th align="start">Provider</Th>
                          </Tr>
                        </THead>
                        <TBody>
                          {timeline.map((row, i) => (
                            <Tr key={`${row.timestamp}-${row.bookmaker}-${row.market}-${row.selection}-${i}`}>
                              <Td mono align="center" className="text-ink-3">{fmtDateTime(row.timestamp)}</Td>
                              <Td mono align="center" className="text-ink-4">{fmtNum(row.minutes_before_kickoff, 0)}</Td>
                              <Td align="start" className="text-ink-3">{row.market}</Td>
                              <Td mono className="text-ink-2">{row.selection}</Td>
                              <Td align="start" className="text-ink-2">{row.bookmaker}</Td>
                              <Td mono align="center" className="text-ink">{fmtOdd(row.price)}</Td>
                              <Td align="start" className="text-ink-4">{row.provider}</Td>
                            </Tr>
                          ))}
                        </TBody>
                      </Table>
                    </TableShell>
                  )}
                </div>
              </Card>

              {/* SINAIS com o PORQUÊ */}
              <Card
                title="Sinais"
                hint={`${fmtInt(data.signals.length)} ativo(s) · todos com motivo e evidência`}
              >
                <div className="p-3">
                  <SignalsList signals={data.signals} />
                </div>
              </Card>

              {/* CLV do evento */}
              <Card title="CLV do evento" hint="primeiro-preço (FIRST-WINS); close nunca fabricado">
                <div className="p-3 text-[11.5px] text-ink-2">
                  {data.clv.n === 0 ? (
                    <p>
                      n = 0 · média = null · status {data.clv.status}. Nenhuma entrada
                      registrada: a ausência é explícita, nunca CLV = 0.
                    </p>
                  ) : (
                    <ul className="flex flex-col gap-1">
                      {data.clv.entries.map((entry, i) => (
                        <li key={i} className="num text-ink-3">
                          {entry.market} · {entry.outcome} · entrada {fmtOdd(entry.entry_odd)} @{" "}
                          {fmtDateTime(entry.entry_timestamp)} · execução: {entry.execution_status}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </Card>

              {/* QUALIDADE / PROVENANCE */}
              <Card title="Qualidade e proveniência" hint="problemas de dados são visíveis, nunca sumidos">
                <div className="p-3 text-[11.5px] text-ink-2">
                  {data.problems.length === 0 ? (
                    <p className="text-ink-3">Nenhum problema de qualidade registrado para este evento.</p>
                  ) : (
                    <ul className="flex flex-col gap-1">
                      {data.problems.map((problem, i) => (
                        <li key={i} className="text-warn-300">
                          {problem.reason}: {problem.bookmaker} · {problem.market}/
                          {problem.selection} @ {fmtDateTime(problem.timestamp)} ({problem.provider})
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="mt-2 text-[10.5px] text-ink-4">
                    event_key: <span className="num">{eventKey}</span>
                  </p>
                </div>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
