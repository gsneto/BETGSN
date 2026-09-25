/**
 * SignalsTable — high-density sports analytics table.
 *
 * Construida sobre os primitivos de tabela adaptados do TailAdmin
 * (`components/ui/table`), com sticky header, ordenacao por coluna,
 * linha de 44px e detalhe expansivel.
 *
 * Paridade de colunas com a grade Tkinter legada (betgsn/gui.py
 * `_signal_columns`): Confianca, Jogo, Data, Mercado, Aposta, Odd, Casa,
 * P modelo, P mercado, Edge, EV, Stake, % banca, Lucro esp., Lucro se
 * vencer, Racional. Nenhuma informacao foi removida.
 */

import { memo, useMemo, useState } from "react";
import Badge from "@/components/ui/Badge";
import { confidenceTone } from "@/components/ui/badgeTone";
import { ProbCompareBars } from "@/components/ui/ProbBar";
import {
  Table,
  TableShell,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/Table";
import { EmptyState, Tooltip } from "@/components/ui/States";
import type { Signal } from "@/types/api";
import { cn } from "@/utils/cn";
import {
  confidenceLabel,
  fmtDate,
  fmtEdgePp,
  fmtInt,
  fmtMoney,
  fmtMoneySigned,
  fmtNum,
  fmtOdd,
  fmtPct,
  fmtPctSigned,
  signedColorClass,
} from "@/utils/format";
import { sortBy, toggleSort, type SortState } from "@/utils/table";

type SortKey =
  | "confidence"
  | "match"
  | "kickoff"
  | "market"
  | "outcome"
  | "best_odd"
  | "best_book"
  | "model_prob"
  | "market_prob"
  | "edge"
  | "ev"
  | "stake"
  | "stake_pct"
  | "expected_profit"
  | "gross_profit_if_win";

const CONF_ORDER: Record<string, number> = {
  FORTE: 3,
  MEDIA: 2,
  FRACA: 1,
  DESCARTE: 0,
};

const extractors: Record<SortKey, (s: Signal) => number | string> = {
  confidence: (s) => CONF_ORDER[s.confidence] ?? 0,
  match: (s) => s.match,
  kickoff: (s) => s.kickoff,
  market: (s) => s.market,
  outcome: (s) => s.outcome,
  best_odd: (s) => s.best_odd,
  best_book: (s) => s.best_book,
  model_prob: (s) => s.model_prob,
  market_prob: (s) => s.market_prob,
  edge: (s) => s.edge,
  ev: (s) => s.ev,
  stake: (s) => s.stake,
  stake_pct: (s) => s.stake_pct,
  expected_profit: (s) => s.expected_profit,
  gross_profit_if_win: (s) => s.gross_profit_if_win,
};

interface Props {
  rows: Signal[];
  maxEv: number;
  onClearFilters?: () => void;
  hasFilters: boolean;
  emptyHint?: string;
  /**
   * `false` quando a decisão global é NO_BET. A tabela continua mostrando a
   * triagem analítica (probabilidades, edge, EV), mas nenhuma coluna
   * operacional (stake/% banca/lucro) apresenta valor — "—" em vez de um
   * número que a decisão já rejeitou. Default `true` para uso em BET.
   */
  betAllowed?: boolean;
}

export default function SignalsTable({
  rows,
  maxEv,
  onClearFilters,
  hasFilters,
  emptyHint,
  betAllowed = true,
}: Props) {
  const [sort, setSort] = useState<SortState<SortKey>>({
    key: "ev",
    direction: "desc",
  });
  const [expanded, setExpanded] = useState<string | null>(null);

  const sorted = useMemo(
    () => sortBy(rows, extractors[sort.key], sort.direction),
    [rows, sort],
  );

  const onSort = (key: SortKey) =>
    setSort((cur) => toggleSort(cur, key, key === "match" || key === "market" ? "asc" : "desc"));

  const head = (
    key: SortKey,
    label: string,
    width: number,
    align: "start" | "end" | "center" = "end",
    title?: string,
  ) => (
    <Th
      width={width}
      align={align}
      sortable
      active={sort.key === key}
      direction={sort.direction}
      onClick={() => onSort(key)}
      title={title}
    >
      {label}
    </Th>
  );

  if (rows.length === 0) {
    return (
      <TableShell>
        <EmptyState
          title="Nenhum sinal encontrado"
          hint={
            hasFilters
              ? "Ajuste os filtros de confiança ou a busca para ver mais resultados."
              : emptyHint ??
                "Nenhum sinal atingiu o EV mínimo configurado. Reduza o EV mín. e recalcule."
          }
          action={
            hasFilters && onClearFilters ? (
              <button
                type="button"
                onClick={onClearFilters}
                className="text-body font-medium text-accent-300 transition-colors hover:text-accent-200"
              >
                Limpar filtros
              </button>
            ) : null
          }
        />
      </TableShell>
    );
  }

  return (
    <TableShell className="max-h-[calc(100vh-430px)] min-h-[280px]">
      <Table>
        <THead>
          <Tr className="h-[34px] hover:bg-transparent">
            {head("confidence", "Confiança", 112, "start", "FORTE ≥ 8% EV · MÉDIA ≥ 4,5% · FRACA ≥ 2%")}
            {head("match", "Jogo", 210, "start", "Partida analisada pelo modelo")}
            {head("kickoff", "Data", 96, "start")}
            {head("market", "Mercado", 160, "start", "Mercado onde o valor foi encontrado")}
            {head(
              "outcome",
              betAllowed ? "Aposta" : "Resultado",
              150,
              "start",
              betAllowed
                ? "Resultado sugerido pelo modelo"
                : "Resultado observado pelo modelo — triagem analítica (NO BET)",
            )}
            {head("best_odd", "Odd", 72, "end", "Melhor odd disponível entre as casas")}
            {head("best_book", "Fonte", 104, "start", "Casa que oferece a melhor odd")}
            {head("model_prob", "P modelo", 96, "end", "Probabilidade do modelo (Poisson/Dixon-Coles)")}
            {head("market_prob", "P mercado", 100, "end", "Probabilidade implícita do consenso, sem vig")}
            <Th width={70} align="center" title="Modelo (âmbar) vs mercado (cinza), mesma escala 0-100%">
              M/M
            </Th>
            {head("edge", "Edge", 84, "end", "P modelo − P mercado, em pontos percentuais")}
            {head("ev", "EV", 104, "end", "Valor esperado por unidade: P modelo × odd − 1")}
            {head(
              "stake",
              betAllowed ? "Stake" : "Stake ·",
              84,
              "end",
              betAllowed
                ? "Valor sugerido pela banca atual (Kelly fracionado)"
                : "NO BET: sem stake operacional — a decisão global bloqueia aposta",
            )}
            {head("stake_pct", "% banca", 84, "end")}
            {head("expected_profit", "Lucro esp.", 96, "end", "stake × EV")}
            {head("gross_profit_if_win", "Se vencer", 92, "end", "Retorno bruto se a aposta vencer")}
            <Th width={240} align="start" title="Resumo qualitativo do edge e do consenso">
              Racional
            </Th>
          </Tr>
        </THead>
        <TBody>
          {sorted.map((s) => (
            <SignalRow
              key={s.id}
              signal={s}
              maxEv={maxEv}
              expanded={expanded === s.id}
              betAllowed={betAllowed}
              onToggle={() => setExpanded((cur) => (cur === s.id ? null : s.id))}
            />
          ))}
        </TBody>
      </Table>
    </TableShell>
  );
}

/* ------------------------------------------------------------------ linha */

interface RowProps {
  signal: Signal;
  maxEv: number;
  expanded: boolean;
  betAllowed: boolean;
  onToggle: () => void;
}

const SignalRow = memo(function SignalRow({
  signal: s,
  maxEv,
  expanded,
  betAllowed,
  onToggle,
}: RowProps) {
  const blocked = "NO BET — sem stake operacional";
  return (
    <>
      <Tr selected={expanded} onClick={onToggle}>
        <Td align="start">
          <Badge tone={confidenceTone(s.confidence)} dot size="sm">
            {confidenceLabel(s.confidence)}
          </Badge>
        </Td>

        <Td align="start" className="max-w-[210px]">
          <span className="block truncate font-semibold text-ink" title={s.match}>
            {s.match}
          </span>
          {s.round_label ? (
            <span className="block truncate text-[10.5px] leading-tight text-ink-4">
              {s.round_label}
            </span>
          ) : null}
        </Td>

        <Td align="start" mono className="text-ink-3">
          {fmtDate(s.kickoff)}
        </Td>

        <Td align="start" className="max-w-[160px]">
          <span className="block truncate text-ink-2" title={s.market}>
            {s.market}
          </span>
        </Td>

        <Td align="start" className="max-w-[150px]">
          <span className="block truncate font-medium text-ink" title={s.outcome}>
            {s.outcome}
          </span>
        </Td>

        <Td mono className="font-semibold text-ink">
          {fmtOdd(s.best_odd)}
        </Td>

        <Td align="start" className="text-ink-2">
          {s.best_book}
          <span className="num ms-1 text-[10.5px] text-ink-4">·{s.n_books}</span>
        </Td>

        <Td mono className="font-medium text-ink">
          {fmtPct(s.model_prob)}
        </Td>

        <Td mono className="text-ink-3">
          {fmtPct(s.market_prob)}
        </Td>

        <Td align="center" className="w-[70px] px-2">
          <ProbCompareBars model={s.model_prob} market={s.market_prob} />
        </Td>

        <Td mono className={cn("font-semibold", signedColorClass(s.edge))}>
          {fmtEdgePp(s.edge)}
        </Td>

        <Td mono>
          <span className={cn("font-semibold", signedColorClass(s.ev))}>
            {fmtPctSigned(s.ev)}
          </span>
          {maxEv > 0 ? (
            <span
              aria-hidden
              className="mt-1 block h-[2px] w-full overflow-hidden rounded-full bg-surface-3"
            >
              <span
                className="block h-full rounded-full bg-pos-400/60"
                style={{ width: `${Math.min(100, (s.ev / maxEv) * 100)}%` }}
              />
            </span>
          ) : null}
        </Td>

        <Td mono className={cn("text-ink", !betAllowed && "text-ink-4")}>
          {betAllowed ? (
            fmtMoney(s.stake)
          ) : (
            <Tooltip content={blocked}>
              <span className="text-ink-4">—</span>
            </Tooltip>
          )}
        </Td>

        <Td mono className="text-ink-3">
          {betAllowed ? fmtPct(s.stake_pct, 2) : "—"}
        </Td>

        <Td
          mono
          className={cn("font-medium", betAllowed && signedColorClass(s.expected_profit))}
        >
          {betAllowed ? fmtMoneySigned(s.expected_profit) : "—"}
        </Td>

        <Td mono className="text-ink-3">
          {betAllowed ? fmtMoneySigned(s.gross_profit_if_win) : "—"}
        </Td>

        <Td align="start" className="max-w-[240px]">
          <Tooltip content={s.rationale || "—"}>
            <span className="block max-w-[228px] truncate text-ink-4">
              {s.rationale || "—"}
            </span>
          </Tooltip>
        </Td>
      </Tr>

      {expanded ? <SignalDetail signal={s} betAllowed={betAllowed} /> : null}
    </>
  );
});

/* --------------------------------------------------------------- detalhe */

function SignalDetail({
  signal: s,
  betAllowed,
}: {
  signal: Signal;
  betAllowed: boolean;
}) {
  const blocked = "NO BET — sem stake operacional";
  const pairs: [string, string][] = [
    ["Confiança", confidenceLabel(s.confidence)],
    ["P modelo", fmtPct(s.model_prob)],
    ["P mercado (sem vig)", fmtPct(s.market_prob)],
    ["Edge", fmtEdgePp(s.edge)],
    ["EV por unidade", fmtPctSigned(s.ev)],
    ["Fair odd (consenso)", fmtOdd(s.fair_odd)],
    ["Odd mediana", fmtOdd(s.median_odd)],
    ["Melhor odd", `${fmtOdd(s.best_odd)} · ${s.best_book}`],
    ["Casas no consenso", fmtInt(s.n_books)],
    ["Kelly completo", fmtPct(s.kelly)],
    [
      "Stake",
      betAllowed
        ? `${fmtMoney(s.stake)} (${fmtPct(s.stake_pct, 2)} da banca)`
        : blocked,
    ],
    ["Lucro esperado", betAllowed ? fmtMoneySigned(s.expected_profit) : "—"],
    ["Lucro se vencer", betAllowed ? fmtMoneySigned(s.gross_profit_if_win) : "—"],
    ["Perda se perder", betAllowed ? fmtMoneySigned(-s.loss_if_lose) : "—"],
    ["Competição", s.league || "—"],
    ["Rodada", s.round_label || "—"],
  ];

  return (
    <tr className="bg-surface-2/70">
      <td colSpan={17} className="border-b border-line px-4 py-3">
        <div className="fade-up grid grid-cols-2 gap-x-8 gap-y-2 md:grid-cols-4 xl:grid-cols-6">
          {pairs.map(([k, v]) => (
            <div key={k} className="min-w-0">
              <p className="label-caps truncate">{k}</p>
              <p className="num mt-0.5 truncate text-body text-ink" title={v}>
                {v}
              </p>
            </div>
          ))}
        </div>
        {s.rationale ? (
          <p className="mt-3 border-t border-line pt-2 text-body text-ink-3">
            <span className="label-caps me-2">Racional</span>
            {s.rationale}
          </p>
        ) : null}
        <p className="num mt-2 text-[11px] text-ink-4">
          EV = P modelo × odd − 1 = {fmtNum(s.model_prob, 4)} × {fmtOdd(s.best_odd)} − 1 ={" "}
          {fmtPctSigned(s.ev, 2)} · calculado no backend
        </p>
      </td>
    </tr>
  );
}
