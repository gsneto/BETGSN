/**
 * BacktestSignalsTable — tabela pesquisavel e paginada dos sinais.
 *
 * Ao clicar numa linha, abre o detalhe com TUDO que o algoritmo usou
 * naquela previsao (ratings, lambdas, medias point-in-time, origem da odd,
 * hash da configuracao). E a ferramenta de auditoria/debug.
 *
 * A paginacao e feita NO BACKEND: com milhares de sinais, trazer tudo
 * para o navegador seria desperdicio.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { fetchBacktestSignals } from "@/api/backtest";
import Badge from "@/components/ui/Badge";
import { confidenceTone } from "@/components/ui/badgeTone";
import Card from "@/components/ui/Card";
import { SearchInput, Select } from "@/components/ui/Input";
import { Table, TableShell, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { EmptyState, ErrorPanel, Skeleton } from "@/components/ui/States";
import type { BacktestSignal } from "@/types/backtest";
import { cn } from "@/utils/cn";
import {
  confidenceLabel,
  fmtDateTime,
  fmtInt,
  fmtMoney,
  fmtNum,
  fmtOdd,
  fmtPct,
  fmtPctSigned,
  signedColorClass,
} from "@/utils/format";

const PAGE_SIZE = 50;

const RESULT_OPTIONS = [
  { value: "", label: "Todos os resultados" },
  { value: "win", label: "Acertos" },
  { value: "loss", label: "Erros" },
  { value: "push", label: "Push" },
];

const CONFIDENCE_OPTIONS = [
  { value: "", label: "Todas as confianças" },
  { value: "FORTE", label: "FORTE" },
  { value: "MEDIA", label: "MÉDIA" },
  { value: "FRACA", label: "FRACA" },
];

export default function BacktestSignalsTable({ runId }: { runId: string }) {
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [outcomeResult, setOutcomeResult] = useState("");
  const [confidence, setConfidence] = useState("");
  const [offset, setOffset] = useState(0);

  const [items, setItems] = useState<BacktestSignal[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<BacktestSignal | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const debounceRef = useRef<number | null>(null);

  // busca com debounce: evita uma requisicao por tecla
  useEffect(() => {
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      setDebounced(search);
      setOffset(0);
    }, 300);
    return () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    };
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    fetchBacktestSignals(
      runId,
      {
        search: debounced,
        outcome_result: outcomeResult,
        confidence,
        offset,
        limit: PAGE_SIZE,
      },
      controller.signal,
    )
      .then((page) => {
        if (!active) return;
        setItems(page.items);
        setTotal(page.total);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(
          err instanceof ApiError ? err.userMessage : "Falha ao carregar os sinais.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [runId, debounced, outcomeResult, confidence, offset, reloadKey]);

  const resetFilters = useCallback(() => {
    setSearch("");
    setOutcomeResult("");
    setConfidence("");
    setOffset(0);
  }, []);

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = search !== "" || outcomeResult !== "" || confidence !== "";

  return (
    <Card
      title="Detalhes dos sinais"
      hint="Cada linha é um sinal congelado antes do resultado · clique para auditar a previsão"
      padded={false}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch("")}
            placeholder="Buscar time, mercado ou seleção…"
            aria-label="Buscar sinais do backtest"
            className="w-[240px]"
          />
          <Select
            aria-label="Filtrar por confiança"
            options={CONFIDENCE_OPTIONS}
            value={confidence}
            onChange={(e) => {
              setConfidence(e.target.value);
              setOffset(0);
            }}
            className="w-[170px]"
          />
          <Select
            aria-label="Filtrar por resultado"
            options={RESULT_OPTIONS}
            value={outcomeResult}
            onChange={(e) => {
              setOutcomeResult(e.target.value);
              setOffset(0);
            }}
            className="w-[170px]"
          />
        </div>
      }
    >
      <div className="p-3">
        {error ? (
          <ErrorPanel message={error} onRetry={() => setReloadKey((k) => k + 1)} />
        ) : loading && items.length === 0 ? (
          <Skeleton className="h-[320px] w-full" />
        ) : items.length === 0 ? (
          <EmptyState
            title="Nenhum sinal encontrado"
            hint={
              hasFilters
                ? "Ajuste a busca ou os filtros para ver mais resultados."
                : "Nenhum sinal foi gerado no período testado."
            }
            action={
              hasFilters ? (
                <button
                  type="button"
                  onClick={resetFilters}
                  className="text-body font-medium text-accent-300 transition-colors hover:text-accent-200"
                >
                  Limpar filtros
                </button>
              ) : null
            }
          />
        ) : (
          <>
            <TableShell className="max-h-[520px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start" width={130}>
                      Data
                    </Th>
                    <Th align="start" width={200}>
                      Jogo
                    </Th>
                    <Th align="start" width={150}>
                      Mercado
                    </Th>
                    <Th align="start" width={140}>
                      Seleção
                    </Th>
                    <Th align="end" width={70}>
                      Odd
                    </Th>
                    <Th align="end" width={80}>
                      Prob.
                    </Th>
                    <Th align="end" width={80}>
                      EV
                    </Th>
                    <Th align="start" width={100}>
                      Confiança
                    </Th>
                    <Th align="end" width={80}>
                      Placar
                    </Th>
                    <Th align="start" width={90}>
                      Resultado
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {items.map((s) => (
                    <Tr
                      key={s.signal_id}
                      selected={selected?.signal_id === s.signal_id}
                      onClick={() =>
                        setSelected((cur) =>
                          cur?.signal_id === s.signal_id ? null : s,
                        )
                      }
                    >
                      <Td align="start" mono className="text-ink-3">
                        {fmtDateTime(s.kickoff)}
                      </Td>
                      <Td align="start" className="max-w-[200px]">
                        <span className="block truncate font-medium text-ink" title={`${s.home} vs ${s.away}`}>
                          {s.home} vs {s.away}
                        </span>
                      </Td>
                      <Td align="start" className="max-w-[150px]">
                        <span className="block truncate text-ink-2" title={s.market}>
                          {s.market}
                        </span>
                      </Td>
                      <Td align="start" className="max-w-[140px]">
                        <span className="block truncate text-ink" title={s.outcome}>
                          {s.outcome}
                        </span>
                      </Td>
                      <Td mono className="font-semibold text-ink">
                        {fmtOdd(s.best_odd)}
                      </Td>
                      <Td mono className="text-accent-300">
                        {fmtPct(s.model_prob)}
                      </Td>
                      <Td mono className={cn("font-semibold", signedColorClass(s.ev))}>
                        {fmtPctSigned(s.ev)}
                      </Td>
                      <Td align="start">
                        <Badge tone={confidenceTone(s.confidence)} dot size="sm">
                          {confidenceLabel(s.confidence)}
                        </Badge>
                      </Td>
                      <Td mono className="text-ink-2">
                        {s.settled ? `${s.result_home_goals}-${s.result_away_goals}` : "—"}
                      </Td>
                      <Td align="start">
                        <ResultBadge signal={s} />
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>

            {selected ? <SignalAudit signal={selected} onClose={() => setSelected(null)} /> : null}

            <div className="mt-3 flex items-center justify-between gap-3">
              <span className="num text-[11.5px] text-ink-4">
                {fmtInt(total)} sinais · página {fmtInt(page)} de {fmtInt(pages)}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                  className="rounded-md border border-line-strong bg-surface-2 px-2.5 py-1 text-[12px] text-ink-2 transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Anterior
                </button>
                <button
                  type="button"
                  disabled={offset + PAGE_SIZE >= total}
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                  className="rounded-md border border-line-strong bg-surface-2 px-2.5 py-1 text-[12px] text-ink-2 transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Próxima
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

function ResultBadge({ signal }: { signal: BacktestSignal }) {
  if (!signal.settled) {
    return (
      <Badge tone="neutral" size="sm">
        não liquidado
      </Badge>
    );
  }
  if (signal.outcome_result === "win") {
    return (
      <Badge tone="positive" size="sm">
        acerto
      </Badge>
    );
  }
  if (signal.outcome_result === "push") {
    return (
      <Badge tone="info" size="sm">
        push
      </Badge>
    );
  }
  return (
    <Badge tone="negative" size="sm">
      erro
    </Badge>
  );
}

/**
 * SignalAudit — o que o algoritmo usou naquela previsao.
 *
 * Mostra explicitamente o contexto point-in-time: quantas partidas
 * anteriores existiam, a media de gols daquele instante, os ratings e
 * lambdas usados, a origem e o timestamp da odd.
 */
function SignalAudit({
  signal: s,
  onClose,
}: {
  signal: BacktestSignal;
  onClose: () => void;
}) {
  const groups: { title: string; items: [string, string][] }[] = [
    {
      title: "Contexto point-in-time",
      items: [
        ["Partidas anteriores ao kickoff", fmtInt(s.n_prior_matches)],
        ["Kickoff (UTC)", s.kickoff_utc],
        ["Corte temporal", s.odds_as_of],
        ["Média de gols da liga (no corte)", fmtNum(s.league_goals, 2)],
        ["Blend de xG", fmtPct(s.attack_blend, 0)],
        ["Fonte de odds", s.odds_source],
      ],
    },
    {
      title: "Modelo naquele instante",
      items: [
        ["λ casa / fora", `${s.lambda_home.toFixed(3)} / ${s.lambda_away.toFixed(3)}`],
        ["Ataque casa / fora", `${s.home_attack.toFixed(3)} / ${s.away_attack.toFixed(3)}`],
        ["Defesa casa / fora", `${s.home_defense.toFixed(3)} / ${s.away_defense.toFixed(3)}`],
        ["xG criado casa / fora", `${s.home_xg_for.toFixed(2)} / ${s.away_xg_for.toFixed(2)}`],
      ],
    },
    {
      title: "Sinal",
      items: [
        ["P modelo", fmtPct(s.model_prob)],
        ["P mercado (sem vig)", fmtPct(s.market_prob)],
        ["Edge", fmtPctSigned(s.edge)],
        ["EV", fmtPctSigned(s.ev)],
        ["Fair odd / mediana", `${fmtOdd(s.fair_odd)} / ${fmtOdd(s.median_odd)}`],
        ["Casas no consenso", fmtInt(s.n_books)],
        ["Kelly completo", fmtPct(s.kelly)],
        ["Stake / % banca", `${fmtMoney(s.stake)} / ${fmtPct(s.stake_pct, 2)}`],
      ],
    },
    {
      title: "Resolução (depois do congelamento)",
      items: [
        ["Placar real", s.settled ? `${s.result_home_goals}-${s.result_away_goals}` : "—"],
        ["Resultado do sinal", s.outcome_result ?? "não liquidado"],
        [
          "Retorno por unidade",
          s.realized_return === null ? "—" : fmtPctSigned(s.realized_return),
        ],
        ["Lucro", s.profit === null ? "—" : fmtMoney(s.profit)],
      ],
    },
    {
      title: "Rastreabilidade",
      items: [
        ["Competição / temporada", `${s.competition || "—"} / ${s.season || "—"}`],
        ["Modelo", `v${s.model_version}`],
        ["Hash da configuração", s.config_hash],
        ["ID do sinal", s.signal_id],
      ],
    },
  ];

  return (
    <div className="fade-up mt-3 rounded-lg border border-line-active/40 bg-surface-2 p-3.5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-body-lg font-semibold text-ink">
            {s.home} vs {s.away}
          </p>
          <p className="text-[11.5px] text-ink-4">
            {s.outcome} · {s.market} · {fmtDateTime(s.kickoff)}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-md border border-line-strong px-2 py-1 text-[11.5px] text-ink-3 transition-colors hover:text-ink"
        >
          fechar
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        {groups.map((g) => (
          <div key={g.title} className="min-w-0">
            <p className="label-caps mb-1.5">{g.title}</p>
            <dl className="flex flex-col gap-1">
              {g.items.map(([k, v]) => (
                <div
                  key={k}
                  className="flex items-baseline justify-between gap-2 border-b border-line pb-0.5"
                >
                  <dt className="truncate text-[11px] text-ink-4" title={k}>
                    {k}
                  </dt>
                  <dd className="num shrink-0 text-[11.5px] font-medium text-ink" title={v}>
                    {v}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      {s.rationale ? (
        <p className="mt-3 border-t border-line pt-2 text-[11.5px] text-ink-3">
          <span className="label-caps me-2">Racional</span>
          {s.rationale}
        </p>
      ) : null}
    </div>
  );
}
