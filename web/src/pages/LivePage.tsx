/**
 * LivePage — BETGSN LIVE: terminal de mercado de odds em tempo real.
 *
 * NAO e uma tela de "melhores apostas": e um terminal que responde em
 * segundos — quais jogos, quais mercados, quantas casas, melhor preco,
 * mediana, fair, o que mudou, quem moveu, quais sinais e por que, e o
 * quao recente e cada quote.
 *
 * Arquitetura de dados: SSE (useRealtimeStream) e apenas o gatilho de
 * "algo mudou"; os dados vem de GET /api/realtime/board (tipado). Sem
 * recarregar a pagina; sem duplicar eventos (dedup no hook + no bus).
 */
import { useCallback, useMemo, useState } from "react";
import {
  fetchRealtimeBoard,
  fetchRealtimeProviders,
  fetchRealtimeStatus,
} from "@/api/realtime";
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
import { useRealtimeStream } from "@/hooks/useRealtimeStream";
import MatchDetail from "@/components/live/MatchDetail";
import ProviderHealthStrip from "@/components/live/ProviderHealthStrip";
import {
  FreshnessBadge,
  MatchStatusBadge,
  SIGNAL_TYPE_LABEL,
} from "@/components/live/badges";
import { fmtDateTime, fmtInt, fmtOdd } from "@/utils/format";
import type { EventView, MarketView, RealtimeSignal } from "@/types/realtime";

type SortKey = "kickoff" | "movement" | "gap" | "signal" | "dispersion";

interface BoardRow {
  event: EventView;
  market: MarketView;
  signals: RealtimeSignal[];
  movedAt: string;
  booksMoved: number;
  bestGapPct: number | null;
  dispersion: number | null;
}

const SORT_LABEL: Record<SortKey, string> = {
  kickoff: "Kickoff",
  movement: "Última movimentação",
  gap: "Maior gap best-vs-mediana",
  signal: "Sinal mais recente",
  dispersion: "Dispersão entre casas",
};

function marketFreshness(market: MarketView): { state: "FRESH" | "RECENT" | "STALE" | "UNKNOWN"; age: number | null } {
  let best: { state: "FRESH" | "RECENT" | "STALE" | "UNKNOWN"; age: number | null } = {
    state: "UNKNOWN",
    age: null,
  };
  for (const selection of market.selections) {
    for (const book of selection.books) {
      if (book.age_seconds == null) continue;
      if (best.age == null || book.age_seconds < best.age) {
        best = { state: book.freshness, age: book.age_seconds };
      }
    }
  }
  return best;
}

export default function LivePage() {
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [query, setQuery] = useState("");
  const [marketFilter, setMarketFilter] = useState("");
  const [signalFilter, setSignalFilter] = useState("");
  const [minBooks, setMinBooks] = useState(0);
  const [onlyMoving, setOnlyMoving] = useState(false);
  const [hideUnmatched, setHideUnmatched] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("kickoff");
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null);

  const bumpRefresh = useCallback(() => {
    setRefreshNonce((n) => n + 1);
  }, []);

  const connection = useRealtimeStream(
    "/api/realtime/stream",
    bumpRefresh,
  );

  const board = useApiResource(
    (signal) => fetchRealtimeBoard(undefined, signal),
    [refreshNonce],
  );
  const status = useApiResource(
    (signal) => fetchRealtimeStatus(signal),
    [refreshNonce],
  );
  const providers = useApiResource(
    (signal) => fetchRealtimeProviders(signal),
    [refreshNonce],
  );

  const data = board.data;

  const markets = useMemo(() => {
    if (!data) return [] as string[];
    return Array.from(new Set(data.events.flatMap((e) => e.markets.map((m) => m.market)))).sort();
  }, [data]);

  const signalTypes = useMemo(() => {
    if (!data) return [] as string[];
    return Array.from(new Set(data.signals.map((s) => s.signal_type))).sort();
  }, [data]);

  const rows = useMemo<BoardRow[]>(() => {
    if (!data) return [];
    const signalsByEvent = new Map<string, RealtimeSignal[]>();
    for (const signal of data.signals) {
      const list = signalsByEvent.get(signal.event_key) ?? [];
      list.push(signal);
      signalsByEvent.set(signal.event_key, list);
    }
    const out: BoardRow[] = [];
    for (const event of data.events) {
      for (const market of event.markets) {
        const move = data.last_moves[`${event.event_key}|${market.market}`];
        const signals = (signalsByEvent.get(event.event_key) ?? []).filter(
          (s) => s.market === market.market,
        );
        let gap: number | null = null;
        for (const selection of market.selections) {
          if (
            selection.best_vs_median != null &&
            selection.median > 0 &&
            (gap == null || selection.best_vs_median / selection.median > gap)
          ) {
            gap = selection.best_vs_median / selection.median;
          }
        }
        const dispersions = market.selections
          .map((s) => s.dispersion)
          .filter((d): d is number => d != null);
        out.push({
          event,
          market,
          signals,
          movedAt: move?.last_move_at ?? "",
          booksMoved: move?.books_moved.length ?? 0,
          bestGapPct: gap,
          dispersion: dispersions.length ? Math.max(...dispersions) : null,
        });
      }
    }
    return out;
  }, [data]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (hideUnmatched && !row.event.matched) return false;
      if (marketFilter && row.market.market !== marketFilter) return false;
      if (signalFilter && !row.signals.some((s) => s.signal_type === signalFilter)) return false;
      if (minBooks > 0 && row.market.n_books < minBooks) return false;
      if (onlyMoving && !row.movedAt) return false;
      if (q) {
        const haystack = [
          row.event.home,
          row.event.away,
          row.event.league,
          ...row.market.selections.flatMap((s) => s.books.map((b) => b.bookmaker)),
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [rows, query, marketFilter, signalFilter, minBooks, onlyMoving, hideUnmatched]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    switch (sortKey) {
      case "movement":
        copy.sort((a, b) => (b.movedAt || "").localeCompare(a.movedAt || ""));
        break;
      case "gap":
        copy.sort((a, b) => (b.bestGapPct ?? -1) - (a.bestGapPct ?? -1));
        break;
      case "signal":
        copy.sort((a, b) => {
          const latest = (r: BoardRow) =>
            r.signals.reduce((acc, s) => (s.observed_at > acc ? s.observed_at : acc), "");
          return latest(b).localeCompare(latest(a));
        });
        break;
      case "dispersion":
        copy.sort((a, b) => (b.dispersion ?? -1) - (a.dispersion ?? -1));
        break;
      default:
        copy.sort(
          (a, b) =>
            a.event.kickoff.localeCompare(b.event.kickoff) ||
            a.event.event_key.localeCompare(b.event.event_key),
        );
    }
    return copy;
  }, [filtered, sortKey]);

  if (board.initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-[52px] w-full" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }

  if (board.error && !data) {
    return <ErrorPanel message={board.error} onRetry={board.reload} />;
  }

  const engineStatus = status.data?.engine ?? null;
  const boot = status.data?.boot ?? board.data?.boot ?? null;

  return (
    <div className="flex flex-col gap-3">
      {board.error ? <ErrorPanel message={board.error} onRetry={board.reload} /> : null}

      {/* faixa de estado do sistema: conexao + engine + providers */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Card title="Sistema" hint="O terminal está vivo?">
          <div className="flex flex-col gap-2 p-3 text-xs">
            <div className="flex items-center gap-2">
              <span className="label-caps text-ink-4">Stream</span>
              <Badge
                tone={
                  connection === "open"
                    ? "positive"
                    : connection === "reconnecting"
                      ? "warning"
                      : "negative"
                }
                size="sm"
                dot
              >
                {connection === "open"
                  ? "CONECTADO"
                  : connection === "reconnecting"
                    ? "RECONECTANDO"
                    : connection === "connecting"
                      ? "CONECTANDO"
                      : "OFFLINE"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <span className="label-caps text-ink-4">Engine</span>
              {engineStatus ? (
                <Badge tone={engineStatus.running ? "positive" : "negative"} size="sm">
                  {engineStatus.running ? "RODANDO" : "PARADO"}
                </Badge>
              ) : boot?.building ? (
                <Badge tone="warning" size="sm">INICIALIZANDO</Badge>
              ) : (
                <Badge tone="negative" size="sm">INDISPONÍVEL</Badge>
              )}
            </div>
            {engineStatus ? (
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-ink-3">
                <dt className="text-ink-4">Eventos</dt>
                <dd className="num text-right">{fmtInt(engineStatus.state.events)} ({fmtInt(engineStatus.state.events_matched)} casados)</dd>
                <dt className="text-ink-4">Linhas monitoradas</dt>
                <dd className="num text-right">{fmtInt(engineStatus.state.lines)}</dd>
                <dt className="text-ink-4">Sinais ativos</dt>
                <dd className="num text-right">{fmtInt(engineStatus.signals.active)}</dd>
                <dt className="text-ink-4">Última quote</dt>
                <dd className="num text-right">{engineStatus.last_quote_at ? fmtDateTime(engineStatus.last_quote_at) : "—"}</dd>
                <dt className="text-ink-4">Último movimento</dt>
                <dd className="num text-right">{engineStatus.last_movement_at ? fmtDateTime(engineStatus.last_movement_at) : "—"}</dd>
                <dt className="text-ink-4">Último sinal</dt>
                <dd className="num text-right">{engineStatus.last_signal_at ? fmtDateTime(engineStatus.last_signal_at) : "—"}</dd>
                <dt className="text-ink-4">Heartbeat</dt>
                <dd className="num text-right">{engineStatus.last_heartbeat ? fmtDateTime(engineStatus.last_heartbeat) : "—"}</dd>
              </dl>
            ) : null}
            {engineStatus?.last_error ? (
              <p className="rounded bg-neg-400/10 p-2 text-[11px] text-neg-300">
                {engineStatus.last_error.slice(0, 300)}
              </p>
            ) : null}
            {boot?.error ? (
              <p className="rounded bg-neg-400/10 p-2 text-[11px] text-neg-300">
                bootstrap: {boot.error}
              </p>
            ) : null}
          </div>
        </Card>

        <ProviderHealthStrip
          data={providers.data ?? null}
          loading={providers.initialLoading}
          error={providers.error}
          onRetry={providers.reload}
        />
      </div>

      {/* signal board */}
      <Card
        title="BETGSN LIVE — Signal Board"
        hint={
          data
            ? `${fmtInt(sorted.length)} linhas · ${fmtInt(data.signals.length)} sinais · gerado em ${fmtDateTime(data.generated_at)}`
            : undefined
        }
        padded={false}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <SearchInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onClear={() => setQuery("")}
              placeholder="Jogo, liga ou casa…"
              aria-label="Buscar no board"
              className="w-[220px]"
            />
            <select
              aria-label="Filtrar por mercado"
              value={marketFilter}
              onChange={(e) => setMarketFilter(e.target.value)}
              className="input h-[34px] w-[170px] text-xs"
            >
              <option value="">Todos os mercados</option>
              {markets.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <select
              aria-label="Filtrar por tipo de sinal"
              value={signalFilter}
              onChange={(e) => setSignalFilter(e.target.value)}
              className="input h-[34px] w-[170px] text-xs"
            >
              <option value="">Todos os sinais</option>
              {signalTypes.map((t) => (
                <option key={t} value={t}>{SIGNAL_TYPE_LABEL[t] ?? t}</option>
              ))}
            </select>
            <select
              aria-label="Ordenar"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className="input h-[34px] w-[190px] text-xs"
            >
              {Object.entries(SORT_LABEL).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
            <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
              <input
                type="number"
                min={0}
                max={20}
                value={minBooks}
                onChange={(e) => setMinBooks(Number(e.target.value) || 0)}
                className="input h-[30px] w-[64px] text-xs"
                aria-label="Mínimo de casas"
              />
              casas mín.
            </label>
            <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
              <input
                type="checkbox"
                checked={onlyMoving}
                onChange={(e) => setOnlyMoving(e.target.checked)}
              />
              só com movimento
            </label>
            <label className="flex items-center gap-1.5 text-[11px] text-ink-3">
              <input
                type="checkbox"
                checked={hideUnmatched}
                onChange={(e) => setHideUnmatched(e.target.checked)}
              />
              esconder UNMATCHED
            </label>
          </div>
        }
      >
        <div className="p-3">
          {!data || sorted.length === 0 ? (
            <EmptyState
              title="Nada no ar com esses filtros"
              hint="O engine publica o que observa. Sem eventos casados ou sem captura recente, o board fica vazio — nunca inventado."
            />
          ) : (
            <TableShell className="max-h-[560px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start">Jogo</Th>
                    <Th align="center">Kickoff</Th>
                    <Th align="start">Mercado</Th>
                    <Th align="start">Melhor preço (casa)</Th>
                    <Th align="start">Mediana</Th>
                    <Th align="center" title="Casas cotando este mercado">Casas</Th>
                    <Th align="start" title="Última movimentação observada: quem moveu e quando">Movimento</Th>
                    <Th align="start">Sinais</Th>
                    <Th align="center" title="Idade da observação mais recente">Frescor</Th>
                    <Th align="center">Status</Th>
                  </Tr>
                </THead>
                <TBody>
                  {sorted.map((row) => {
                    const fresh = marketFreshness(row.market);
                    const key = `${row.event.event_key}|${row.market.market}`;
                    return (
                      <Tr
                        key={key}
                        className="cursor-pointer"
                        onClick={() => setSelectedEvent(row.event.event_key)}
                      >
                        <Td align="start" className="font-medium text-ink">
                          <span className="block">{row.event.home} vs {row.event.away}</span>
                          <span className="block text-[10.5px] text-ink-4">{row.event.league || row.event.event_key.split("|")[0].toUpperCase()}</span>
                        </Td>
                        <Td mono align="center" className="text-ink-2">
                          {fmtDateTime(row.event.kickoff)}
                        </Td>
                        <Td align="start" className="text-ink-3">{row.market.market}</Td>
                        <Td align="start">
                          <div className="flex flex-col gap-0.5">
                            {row.market.selections.slice(0, 3).map((s) => (
                              <span key={s.selection} className="num text-[11px] text-ink">
                                <span className="text-ink-4">{s.selection}</span>{" "}
                                {fmtOdd(s.best.price)}
                                <span className="text-ink-4"> @{s.best.bookmaker}</span>
                              </span>
                            ))}
                          </div>
                        </Td>
                        <Td align="start">
                          <div className="flex flex-col gap-0.5">
                            {row.market.selections.slice(0, 3).map((s) => (
                              <span key={s.selection} className="num text-[11px] text-ink-2">
                                <span className="text-ink-4">{s.selection}</span> {fmtOdd(s.median)}
                              </span>
                            ))}
                          </div>
                        </Td>
                        <Td mono align="center" className="text-ink-2">
                          {fmtInt(row.market.n_books)}
                        </Td>
                        <Td align="start">
                          {row.movedAt ? (
                            <Tooltip
                              content={`${row.booksMoved} casa(s) moveram: ${(data.last_moves[`${row.event.event_key}|${row.market.market}`]?.books_moved ?? []).join(", ")}`}
                            >
                              <span className="num text-[11px] text-accent-300">
                                ▲▼ {fmtDateTime(row.movedAt).slice(11)}
                              </span>
                            </Tooltip>
                          ) : (
                            <span className="text-[11px] text-ink-4">—</span>
                          )}
                        </Td>
                        <Td align="start">
                          <div className="flex flex-wrap gap-1">
                            {row.signals.slice(0, 3).map((s) => (
                              <Badge
                                key={s.signal_id}
                                tone={s.status === "ACTIVE" ? "accent" : s.status === "STALE" ? "warning" : "neutral"}
                                size="sm"
                              >
                                {SIGNAL_TYPE_LABEL[s.signal_type] ?? s.signal_type}
                              </Badge>
                            ))}
                            {row.signals.length > 3 ? (
                              <Badge tone="neutral" size="sm">+{row.signals.length - 3}</Badge>
                            ) : null}
                            {row.signals.length === 0 ? (
                              <span className="text-[11px] text-ink-4">—</span>
                            ) : null}
                          </div>
                        </Td>
                        <Td align="center">
                          <FreshnessBadge state={fresh.state} ageSeconds={fresh.age} />
                        </Td>
                        <Td align="center">
                          <MatchStatusBadge
                            status={row.event.match_status}
                            matched={row.event.matched}
                          />
                        </Td>
                      </Tr>
                    );
                  })}
                </TBody>
              </Table>
            </TableShell>
          )}
          <p className="mt-2 text-[11px] text-ink-4">
            Terminal de mercado: preços, medianas e sinais são <strong>informação</strong>,
            não recomendação de aposta. Clique numa linha para ver o detalhe do jogo
            (grade por casa, timeline, fair, modelo e o porquê de cada sinal).
            {connection !== "open" ? " — stream reconectando; os dados continuam disponíveis via HTTP." : ""}
          </p>
        </div>
      </Card>

      {selectedEvent ? (
        <MatchDetail
          eventKey={selectedEvent}
          onClose={() => setSelectedEvent(null)}
          refreshNonce={refreshNonce}
        />
      ) : null}
    </div>
  );
}
