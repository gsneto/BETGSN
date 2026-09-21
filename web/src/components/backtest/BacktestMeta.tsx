/**
 * BacktestMeta — barra de progresso da execucao e aviso de dados ausentes.
 *
 * O progresso vem do backend (fases reais do motor), nao de um timer
 * ficticio. O aviso de dados ausentes e explicito: o BETGSN declara o que
 * falta em vez de inventar odds ou estatisticas historicas.
 */

import Card from "@/components/ui/Card";
import { AlertTriangleIcon } from "@/components/ui/icons";
import type { BacktestJobStatus, MissingDataNote } from "@/types/backtest";
import { cn } from "@/utils/cn";
import { fmtInt, fmtPct } from "@/utils/format";

const PHASE_LABEL: Record<string, string> = {
  idle: "Aguardando",
  preparing: "Preparando dados",
  analyzing: "Analisando partidas",
  metrics: "Calculando métricas",
  saving: "Finalizando",
  done: "Concluído",
  error: "Erro",
};

const PHASE_ORDER = ["preparing", "analyzing", "metrics", "saving"];

export function BacktestProgress({ status }: { status: BacktestJobStatus }) {
  const active = PHASE_ORDER.includes(status.phase);
  if (status.phase === "idle" && status.done === 0) return null;

  const pct = Math.round(status.progress * 100);
  const failed = status.phase === "error";

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "panel flex flex-col gap-2 p-3",
        failed && "border-neg-700/50 bg-neg-900/30",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-body">
          <span
            aria-hidden
            className={cn(
              "size-[6px] rounded-full",
              failed ? "bg-neg-400" : active ? "bg-accent-400 pulse-dot" : "bg-pos-400",
            )}
          />
          <span className={failed ? "text-neg-300" : "text-ink-2"}>{status.message}</span>
        </span>
        <span className="num text-[11.5px] text-ink-4">
          {PHASE_LABEL[status.phase] ?? status.phase}
          {status.total > 0 ? ` · ${fmtInt(status.done)}/${fmtInt(status.total)}` : ""}
          {` · ${pct}%`}
        </span>
      </div>

      <div className="h-[3px] w-full overflow-hidden rounded-full bg-surface-3">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-300",
            failed ? "bg-neg-400" : "bg-accent-400",
          )}
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>

      {status.error ? (
        <p className="text-[11.5px] leading-relaxed break-words text-neg-300">
          {status.error}
        </p>
      ) : null}
    </div>
  );
}

export function MissingDataNotice({ notes }: { notes: MissingDataNote[] }) {
  if (notes.length === 0) return null;
  return (
    <Card
      title="Dados históricos ausentes"
      hint="O BETGSN não inventa odds nem estatísticas: o que falta está declarado abaixo"
    >
      <ul className="grid grid-cols-1 gap-2 xl:grid-cols-2">
        {notes.map((n) => (
          <li
            key={n.dado}
            className="rounded-md border border-line bg-surface-2 p-2.5"
          >
            <p className="text-body font-semibold text-ink">{n.dado}</p>
            <p className="mt-1 text-[11.5px] leading-relaxed text-ink-3">
              <span className="label-caps me-1.5">Por quê</span>
              {n.por_que}
            </p>
            <p className="mt-1 text-[11.5px] leading-relaxed text-ink-3">
              <span className="label-caps me-1.5">Fornece</span>
              {n.quem_fornece}
            </p>
            <p className="mt-1 text-[11.5px] leading-relaxed text-ink-4">
              <span className="label-caps me-1.5">Como plugar</span>
              {n.como_plugar}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/**
 * BaselineWarning — aviso obrigatório quando o mercado é o baseline ingênuo.
 *
 * Sem isso, os números da simulação parecem uma estimativa de lucro. Eles
 * não são: o baseline precifica todo jogo com a mesma distribuição (só
 * médias da liga), então em jogos desequilibrados ele oferece odds muito
 * acima do justo e o modelo "lucra" essa distorção. Mede se o modelo usa
 * informação que o baseline não usa — o que é trivialmente verdadeiro,
 * porque o baseline foi construído para não usar.
 */
export function BaselineWarning({
  oddsSource,
  avgModelProb,
  avgMarketProb,
}: {
  oddsSource: string;
  avgModelProb: number;
  avgMarketProb: number;
}) {
  if (oddsSource !== "naive_synthetic") return null;
  const gap = avgModelProb - avgMarketProb;

  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-lg border border-warn-500/45 bg-warn-900/35 p-3.5"
    >
      <p className="flex items-center gap-2 text-body font-semibold text-warn-300">
        <AlertTriangleIcon className="size-4 shrink-0" />
        A simulação abaixo NÃO é estimativa de lucro
      </p>
      <p className="text-[12px] leading-relaxed text-ink-2">
        O mercado de referência é um <b>baseline ingênuo</b>: ele precifica
        todo jogo com a mesma distribuição, porque só conhece as médias da
        liga e ignora quem está em campo. Em jogos desequilibrados ele oferece
        odds muito acima do justo, e a simulação "lucra" essa distorção em vez
        de habilidade do modelo.
      </p>
      <p className="num text-[11.5px] text-ink-3">
        Evidência: P modelo média {fmtPct(avgModelProb)} contra P mercado média{" "}
        {fmtPct(avgMarketProb)} — gap de {fmtPct(gap)}. Um mercado real nunca
        precifica eventos de 40% a 5%.
      </p>
      <p className="text-[12px] leading-relaxed text-ink-2">
        O que <b>é</b> válido aqui: a <b>calibração</b> (as probabilidades do
        modelo batem com a frequência real?) e o <b>Brier/Log-loss</b>. O que
        não é válido: capital final, ROI e drawdown.
      </p>
      <p className="text-[11.5px] leading-relaxed text-ink-4">
        Para medir contra o mercado de verdade é preciso odds históricas reais
        (plano pago) ou acumular capturas ao vivo com{" "}
        <span className="num">python betgsn.py --capture-odds</span>.
      </p>
    </div>
  );
}
