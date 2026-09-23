/**
 * AppHeader — cabecalho do terminal.
 *
 * Inspirado na estrutura do TailAdmin `layout/AppHeader.tsx` (marca a
 * esquerda, cluster de status/acoes a direita), mas SEM sidebar: para
 * este produto a arquitetura HEADER + TABS + CONTEUDO e melhor, porque
 * sao apenas 5 areas e o espaco horizontal e precioso para a tabela.
 *
 * Direita: status de conexao, contagem de sinais, ultima atualizacao e
 * o botao RECALCULAR (acao principal).
 */

import Button from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/States";
import { RefreshCwIcon, CloseIcon } from "@/components/ui/icons";
import { useStore } from "@/store/context";
import { cn } from "@/utils/cn";
import { fmtClock, fmtDuration, fmtInt, fmtPct } from "@/utils/format";

export default function AppHeader() {
  const { summary, status, socketState, recalculating, recalcJob, recalculate, cancelRecalculate } =
    useStore();

  const connected = socketState === "open";
  const strong = summary?.kpis.strong ?? 0;
  const total = summary?.kpis.total ?? 0;
  const progress = recalculating && recalcJob ? recalcJob.progress : null;

  const statusTone = recalculating
    ? "text-accent-300"
    : connected
      ? "text-pos-400"
      : "text-neg-400";

  const statusText = recalculating
    ? "recalculando"
    : connected
      ? "conectado"
      : socketState === "connecting"
        ? "conectando"
        : "desconectado";

  return (
    <header className="header-glow sticky top-0 z-99 border-b border-line bg-header/95 backdrop-blur-md">
      <div className="flex h-[60px] items-center gap-4 px-4">
        {/* ---------------------------------------------------- marca */}
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden
            className="flex size-8 shrink-0 items-center justify-center rounded-md border border-accent-700/50 bg-accent-400/10 text-[15px] font-bold text-accent-400"
          >
            B
          </span>
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              <h1 className="text-[17px] leading-none font-bold tracking-[-0.01em] text-ink">
                BETGSN
              </h1>
              <span className="text-body text-ink-3">Preditor estatístico</span>
            </div>
            <p className="mt-1 truncate text-[11.5px] leading-none text-ink-4">
              Poisson / Dixon-Coles + consenso de {fmtInt(summary?.n_bookmakers ?? 0)} fontes
            </p>
          </div>
        </div>

        <div className="flex-1" />

        {/* -------------------------------------------- cluster direito */}
        <div className="flex items-center gap-4">
          {/* status de conexao */}
          <Tooltip
            content={
              status?.message
                ? `Backend: ${status.message}`
                : `WebSocket ${statusText}. Backend v${status?.version ?? "—"} · Python ${status?.python_version ?? "—"}`
            }
          >
            <span className="flex items-center gap-2">
              <span
                aria-hidden
                className={cn(
                  "size-[6px] rounded-full",
                  recalculating
                    ? "bg-accent-400 pulse-dot"
                    : connected
                      ? "bg-pos-400"
                      : "bg-neg-400",
                )}
              />
              <span className={cn("text-[11.5px] font-medium", statusTone)}>
                {statusText}
              </span>
            </span>
          </Tooltip>

          <span aria-hidden className="h-6 w-px bg-line" />

          {/* densidade de dados */}
          <div className="hidden items-center gap-4 md:flex">
            <HeaderMetric
              label="Sinais"
              value={fmtInt(total)}
              detail={`${fmtInt(strong)} fortes`}
              tooltip="Sinais acima do EV mínimo no último cálculo."
            />
            <HeaderMetric
              label="Jogos"
              value={fmtInt(summary?.n_games ?? 0)}
              detail={`${fmtInt(summary?.n_markets ?? 0)} mercados`}
              tooltip="Partidas analisadas pelo modelo nas rodadas futuras."
            />
            <HeaderMetric
              label="Atualizado"
              value={fmtClock(summary?.generated_at)}
              detail={
                summary ? fmtDuration(summary.computed_in_ms) : "aguardando cálculo"
              }
              tooltip="Horário do último snapshot e tempo de processamento no backend."
            />
          </div>

          <Button
            variant="accent"
            size="md"
            loading={recalculating}
            onClick={() => void recalculate()}
            startIcon={<RefreshCwIcon className="size-4" />}
            className="tracking-[0.04em] uppercase"
          >
            {recalculating
              ? progress !== null
                ? `Calculando ${fmtPct(progress, 0)}`
                : "Calculando"
              : "Recalcular"}
          </Button>

          {recalculating ? (
            <Tooltip
              content={
                recalcJob?.message
                  ? `${recalcJob.message} — clique para cancelar (o snapshot atual é preservado)`
                  : "Cancelar recalculo — o snapshot atual é preservado"
              }
            >
              <Button
                variant="ghost"
                size="md"
                onClick={cancelRecalculate}
                aria-label="Cancelar recálculo"
                className="px-2"
              >
                <CloseIcon className="size-4" />
              </Button>
            </Tooltip>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function HeaderMetric({
  label,
  value,
  detail,
  tooltip,
}: {
  label: string;
  value: string;
  detail: string;
  tooltip: string;
}) {
  return (
    <Tooltip content={tooltip}>
      <span className="flex flex-col items-end">
        <span className="label-caps leading-none">{label}</span>
        <span className="num mt-1 text-body leading-none font-semibold text-ink">
          {value}
        </span>
        <span className="mt-0.5 text-[10.5px] leading-none text-ink-4">{detail}</span>
      </span>
    </Tooltip>
  );
}
