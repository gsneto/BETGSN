/**
 * BacktestRunsPanel — execucoes salvas, auditoria e comparacao.
 *
 * Cada execucao tem run_id, hash da configuracao e versao do modelo, o
 * que permite rodar de novo apos mudar o algoritmo e comparar. A
 * comparacao A vs B expoe as diferencas sem julgar qual e melhor.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import {
  compareBacktestRuns,
  deleteBacktestRun,
  fetchBacktestRuns,
} from "@/api/backtest";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import { Select } from "@/components/ui/Input";
import { Table, TableShell, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { EmptyState, ErrorPanel } from "@/components/ui/States";
import type { BacktestRunSummary, RunComparison } from "@/types/backtest";
import { cn } from "@/utils/cn";
import {
  fmtDateTime,
  fmtInt,
  fmtNum,
  fmtPct,
  fmtPctSigned,
  signedColorClass,
} from "@/utils/format";

const METRIC_LABELS: Record<string, string> = {
  n_signals: "Sinais",
  hit_rate: "Taxa de acerto",
  brier: "Brier score",
  logloss: "Log-loss",
  avg_ev: "EV médio previsto",
  avg_realized_return: "Retorno médio realizado",
  ev_gap: "Gap EV (previsto − realizado)",
};

const PERCENT_METRICS = new Set([
  "hit_rate",
  "avg_ev",
  "avg_realized_return",
  "ev_gap",
]);

interface Props {
  activeRunId: string | null;
  onOpenRun: (runId: string) => void;
  refreshKey: number;
}

export default function BacktestRunsPanel({
  activeRunId,
  onOpenRun,
  refreshKey,
}: Props) {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runA, setRunA] = useState("");
  const [runB, setRunB] = useState("");
  const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [comparing, setComparing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchBacktestRuns(50);
      setRuns(list);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : "Falha ao listar execuções.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // mantem as selecoes de comparacao validas
  useEffect(() => {
    if (runs.length === 0) return;
    setRunA((cur) => (runs.some((r) => r.run_id === cur) ? cur : runs[0].run_id));
    setRunB((cur) =>
      runs.some((r) => r.run_id === cur) ? cur : (runs[1]?.run_id ?? runs[0].run_id),
    );
  }, [runs]);

  const doCompare = async () => {
    if (!runA || !runB) return;
    setComparing(true);
    try {
      setComparison(await compareBacktestRuns(runA, runB));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : "Falha ao comparar.");
    } finally {
      setComparing(false);
    }
  };

  const remove = async (runId: string) => {
    try {
      await deleteBacktestRun(runId);
      if (comparison && (comparison.run_a.run_id === runId || comparison.run_b.run_id === runId)) {
        setComparison(null);
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : "Falha ao remover.");
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <Card
        title="Execuções salvas"
        hint="Cada execução é reproduzível a partir da configuração e do corpus (mesmo hash, mesmo resultado)"
        padded={false}
        action={
          <Button size="sm" variant="outline" onClick={() => void load()} loading={loading}>
            Atualizar
          </Button>
        }
      >
        <div className="p-3">
          {error ? <ErrorPanel message={error} onRetry={() => void load()} /> : null}
          {!error && runs.length === 0 && !loading ? (
            <EmptyState
              title="Nenhuma execução salva"
              hint="Configure o teste acima e clique em EXECUTAR BACKTEST."
            />
          ) : (
            <TableShell className="max-h-[320px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start" width={190}>
                      Execução
                    </Th>
                    <Th align="end" width={90}>
                      Sinais
                    </Th>
                    <Th align="end" width={100}>
                      Partidas
                    </Th>
                    <Th align="end" width={100}>
                      Taxa
                    </Th>
                    <Th align="end" width={90}>
                      Brier
                    </Th>
                    <Th align="end" width={110}>
                      Retorno
                    </Th>
                    <Th align="end" width={100}>
                      Drawdown
                    </Th>
                    <Th align="start" width={90}>
                      Config
                    </Th>
                    <Th align="end" width={140}>
                      Ações
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {runs.map((r) => (
                    <Tr
                      key={r.run_id}
                      selected={r.run_id === activeRunId}
                      onClick={() => onOpenRun(r.run_id)}
                    >
                      <Td align="start" mono className="text-ink-2">
                        <span className="block truncate" title={r.run_id}>
                          {fmtDateTime(r.created_at)}
                        </span>
                        <span className="block truncate text-[10.5px] text-ink-4">
                          v{r.model_version} · {Math.round(r.duration_ms)} ms
                        </span>
                      </Td>
                      <Td mono className="text-ink">
                        {fmtInt(r.n_signals)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {fmtInt(r.n_matches_evaluated)}
                        {r.n_matches_skipped > 0 ? (
                          <span className="text-ink-4"> (+{fmtInt(r.n_matches_skipped)})</span>
                        ) : null}
                      </Td>
                      <Td mono className="text-ink-2">
                        {r.hit_rate === null ? "—" : fmtPct(r.hit_rate)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {r.brier === null ? "—" : fmtNum(r.brier, 4)}
                      </Td>
                      <Td
                        mono
                        className={cn(
                          "font-semibold",
                          r.return_pct === null ? "text-ink-4" : signedColorClass(r.return_pct),
                        )}
                      >
                        {r.return_pct === null ? "—" : fmtPctSigned(r.return_pct)}
                      </Td>
                      <Td mono className="text-neg-400">
                        {r.max_drawdown === null ? "—" : fmtPct(r.max_drawdown)}
                      </Td>
                      <Td align="start">
                        <span
                          className="num text-[10.5px] text-ink-4"
                          title="Hash da configuração — dois backtests com o mesmo hash são comparáveis diretamente"
                        >
                          {r.config_hash}
                        </span>
                      </Td>
                      <Td align="end">
                        <div className="flex items-center justify-end gap-1.5">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenRun(r.run_id);
                            }}
                          >
                            abrir
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={(e) => {
                              e.stopPropagation();
                              void remove(r.run_id);
                            }}
                          >
                            excluir
                          </Button>
                        </div>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
          )}
        </div>
      </Card>

      <Card
        title="Comparação entre execuções"
        hint="Modelo antigo vs modelo novo · diferenças de calibração, volume e estabilidade"
        padded={false}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <span className="label-caps">A</span>
            <Select
              aria-label="Execução A"
              options={runs.map((r) => ({ value: r.run_id, label: r.run_id }))}
              value={runA}
              onChange={(e) => setRunA(e.target.value)}
              className="w-[210px]"
            />
            <span className="label-caps">B</span>
            <Select
              aria-label="Execução B"
              options={runs.map((r) => ({ value: r.run_id, label: r.run_id }))}
              value={runB}
              onChange={(e) => setRunB(e.target.value)}
              className="w-[210px]"
            />
            <Button
              size="sm"
              variant="outline"
              loading={comparing}
              disabled={!runA || !runB}
              onClick={() => void doCompare()}
            >
              Comparar
            </Button>
          </div>
        }
      >
        <div className="p-3">
          {!comparison ? (
            <EmptyState
              title="Selecione duas execuções"
              hint="Escolha A e B acima para comparar as métricas lado a lado."
            />
          ) : (
            <div className="flex flex-col gap-3">
              {comparison.same_config ? (
                <p className="rounded-md border border-info-500/40 bg-info-900/30 px-3 py-2 text-[12px] text-info-300">
                  As duas execuções usam a MESMA configuração (mesmo hash). Diferenças
                  indicam mudança no código do modelo ou nos dados — não em parâmetros.
                </p>
              ) : (
                <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-[12px] text-ink-3">
                  Configurações diferentes. As diferenças abaixo misturam efeito de
                  parâmetros e efeito de modelo.
                </p>
              )}

              <TableShell>
                <Table>
                  <THead>
                    <Tr className="h-[34px] hover:bg-transparent">
                      <Th align="start" width={240}>
                        Métrica
                      </Th>
                      <Th align="end" width={140}>
                        A
                      </Th>
                      <Th align="end" width={140}>
                        B
                      </Th>
                      <Th align="end" width={140}>
                        Diferença (B − A)
                      </Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {Object.entries(comparison.metrics).map(([key, m]) => {
                      const isPct = PERCENT_METRICS.has(key);
                      const fmt = (v: number) =>
                        isPct ? fmtPctSigned(v) : fmtNum(v, key === "n_signals" ? 0 : 4);
                      return (
                        <Tr key={key}>
                          <Td align="start" className="text-ink-2">
                            {METRIC_LABELS[key] ?? key}
                          </Td>
                          <Td mono className="text-ink">
                            {fmt(m.a)}
                          </Td>
                          <Td mono className="text-ink">
                            {fmt(m.b)}
                          </Td>
                          <Td
                            mono
                            className={cn(
                              "font-semibold",
                              key === "brier" || key === "logloss"
                                ? m.delta < 0
                                  ? "text-pos-400"
                                  : m.delta > 0
                                    ? "text-neg-400"
                                    : "text-ink-3"
                                : signedColorClass(m.delta),
                            )}
                          >
                            {isPct
                              ? fmtPctSigned(m.delta)
                              : fmtNum(m.delta, key === "n_signals" ? 0 : 4)}
                          </Td>
                        </Tr>
                      );
                    })}
                  </TBody>
                </Table>
              </TableShell>

              {comparison.temporal_month.length > 0 ? (
                <div>
                  <p className="label-caps mb-1.5">
                    Estabilidade temporal (taxa de acerto por mês)
                  </p>
                  <ul className="flex flex-wrap gap-2">
                    {comparison.temporal_month.map((t) => (
                      <li
                        key={t.label}
                        className="rounded-md border border-line bg-surface-2 px-2.5 py-1.5"
                      >
                        <span className="num block text-[10.5px] text-ink-4">{t.label}</span>
                        <span className="num block text-[11.5px] text-ink-2">
                          A {t.n_a > 0 ? fmtPct(t.hit_rate_a) : "—"}
                          {t.n_a > 0 ? <span className="text-ink-4"> ({fmtInt(t.n_a)})</span> : null}
                        </span>
                        <span className="num block text-[11.5px] text-ink-2">
                          B {t.n_b > 0 ? fmtPct(t.hit_rate_b) : "—"}
                          {t.n_b > 0 ? <span className="text-ink-4"> ({fmtInt(t.n_b)})</span> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
