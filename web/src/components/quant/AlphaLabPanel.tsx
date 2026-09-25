/**
 * AlphaLabPanel — painel de EVIDÊNCIA do laboratório quantitativo.
 *
 * Mostra o estado REAL do Alpha Lab, sem prometer lucro:
 *   - progresso do CLV (closed / 200);
 *   - execução (0 fills = UNKNOWN);
 *   - veredito por sinal (NO_EVIDENCE / VALIDATED / RESEARCH / BLOCKED);
 *   - prontidão de captura (READY / WAITING_FOR_PROVIDER_QUOTA);
 *   - veredito de promoção (NO_BET explícito).
 *
 * Nada aqui transforma sinal informativo em recomendação de aposta.
 */

import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { fetchQuantAlphaLab, fetchQuantClvProgress, fetchQuantExecutionStatus } from "@/api/quant";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import { fmtInt } from "@/utils/format";
import type { AlphaEvaluation } from "@/types/quant";

const STATUS_TONE: Record<string, "accent" | "positive" | "warning" | "negative" | "neutral" | "info"> = {
  VALIDATED: "info",
  PRODUCTION_CANDIDATE: "warning",
  PRODUCTION: "positive",
  FRAGILE: "warning",
  NO_EVIDENCE: "neutral",
  INSUFFICIENT_DATA: "neutral",
  RESEARCH: "neutral",
  BLOCKED: "negative",
  REJECTED: "negative",
};

function statusTone(status: string) {
  return STATUS_TONE[status] ?? "neutral";
}

export default function AlphaLabPanel() {
  const { dataVersion } = useStore();
  const progress = useApiResource((s) => fetchQuantClvProgress(s), [dataVersion]);
  const execution = useApiResource((s) => fetchQuantExecutionStatus(s), [dataVersion]);
  const lab = useApiResource((s) => fetchQuantAlphaLab(s), [dataVersion]);

  const evaluations: Record<string, AlphaEvaluation> =
    lab.data?.evaluations?.evaluations ?? {};
  const readiness = lab.data?.capture_readiness?.readiness;
  const verdict = lab.data?.promotion?.verdict ?? "NO_BET";
  const closed = progress.data?.closed ?? 0;
  const target = progress.data?.target ?? 200;
  const pct = target > 0 ? Math.min(100, (closed / target) * 100) : 0;

  return (
    <Card
      title="Alpha Lab — evidência"
      hint={`CLV ${fmtInt(closed)}/${fmtInt(target)} · execução ${
        execution.data?.status ?? "?"
      } · captura ${readiness?.status ?? "?"}`}
      action={<Badge tone="negative" size="sm">{verdict}</Badge>}
    >
      <div className="flex flex-col gap-3 p-3 text-xs" data-testid="alpha-lab-panel">
        {/* Progresso do CLV */}
        <div>
          <div className="mb-1 flex items-center justify-between text-ink-3">
            <span>CLV fechado</span>
            <span className="num">
              {fmtInt(closed)} / {fmtInt(target)} ({pct.toFixed(0)}%)
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-surface-3">
            <div
              className="h-full rounded-full bg-accent-400/70"
              style={{ width: `${pct}%` }}
            />
          </div>
          {progress.data ? (
            <p className="mt-1 text-[10.5px] text-ink-4">
              pending {fmtInt(progress.data.pending)} · no_close{" "}
              {fmtInt(progress.data.no_close)} · invalid {fmtInt(progress.data.invalid)} ·{" "}
              {progress.data.status}
            </p>
          ) : null}
        </div>

        {/* Execução */}
        <p className="text-[11px] text-ink-4">
          Execução: {execution.data?.status ?? "UNKNOWN"} — {execution.data?.note ?? "sem fills"}
        </p>

        {/* Veredito por sinal */}
        <div className="overflow-hidden rounded border border-line">
          <table className="w-full">
            <thead>
              <tr className="bg-surface-2 text-[10.5px] uppercase tracking-wide text-ink-4">
                <th className="px-2 py-1 text-start">Sinal</th>
                <th className="px-2 py-1 text-end">n</th>
                <th className="px-2 py-1 text-end">métrica</th>
                <th className="px-2 py-1 text-start">status</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(evaluations).map((e) => (
                <tr key={e.signal_type} className="border-t border-line">
                  <td className="px-2 py-1 text-ink-2">{e.signal_type}</td>
                  <td className="num px-2 py-1 text-end text-ink-3">{fmtInt(e.n)}</td>
                  <td className="num px-2 py-1 text-end text-ink-3">
                    {e.primary_metric == null ? "—" : e.primary_metric.toFixed(4)}
                  </td>
                  <td className="px-2 py-1">
                    <Badge tone={statusTone(e.status)} size="sm">
                      {e.status}
                    </Badge>
                  </td>
                </tr>
              ))}
              {Object.keys(evaluations).length === 0 ? (
                <tr>
                  <td className="px-2 py-2 text-ink-4" colSpan={4}>
                    {lab.data?.status === "NOT_RUN"
                      ? "Alpha Lab ainda não executado (python tools/alpha_lab_run.py)."
                      : "carregando."}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <p className="text-[10.5px] text-ink-4">
          INFORMACIONAL · RESEARCH · VALIDATED (observação) · NO_EVIDENCE ·
          BLOCKED. Nenhum sinal é recomendação de aposta; NO_BET permanece
          enquanto os gates (CLV ≥ 200, execução medida) não passarem.
        </p>
      </div>
    </Card>
  );
}
