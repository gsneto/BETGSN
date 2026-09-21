/**
 * SignalsSourceBanner — de onde vieram os sinais, e o quanto confiar neles.
 *
 * Este componente existe por um motivo concreto: a tela SINAIS mostrava o
 * dataset sintetico (datas fixas no codigo, odds geradas pelo proprio
 * modelo) sem avisar. Um sinal de "Corinthians vs Palmeiras 19/09/2026"
 * com EV +9,2% parecia real e nao era.
 *
 * Agora a origem fica explicita, e o vies medido do modelo tambem.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { AlertTriangleIcon, DatabaseIcon, InfoIcon } from "@/components/ui/icons";
import type { ModelCalibrationInfo, SignalSource, SignalsSourceStatus } from "@/types/api";
import { cn } from "@/utils/cn";
import { fmtDate, fmtInt, fmtPct } from "@/utils/format";

interface Props {
  source: SignalSource;
  status: SignalsSourceStatus | null;
  detail: string;
  calibration: ModelCalibrationInfo | null;
  skippedNoRating: number;
  onChange: (next: SignalSource) => void;
}

export default function SignalsSourceBanner({
  source,
  status,
  detail,
  calibration,
  skippedNoRating,
  onChange,
}: Props) {
  const realAvailable = status?.real.available ?? false;

  return (
    <div className="flex flex-col gap-2.5">
      {/* ------------------------------------------------------ seletor */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface-1 px-3.5 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-md border",
              source === "real"
                ? "border-pos-700/50 bg-pos-900/40 text-pos-400"
                : "border-warn-500/45 bg-warn-900/35 text-warn-300",
            )}
          >
            {source === "real" ? (
              <DatabaseIcon className="size-3.5" />
            ) : (
              <AlertTriangleIcon className="size-3.5" />
            )}
          </span>
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-body font-semibold text-ink">
              {source === "real" ? "Dados reais" : "Dataset sintético"}
              <Badge tone={source === "real" ? "positive" : "warning"} size="sm">
                {source === "real" ? "jogos + odds reais" : "não use para apostar"}
              </Badge>
            </p>
            <p className="mt-0.5 text-[11.5px] leading-relaxed text-ink-4">{detail}</p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {realAvailable && status?.real.n_fixtures ? (
            <span className="num hidden text-[11px] text-ink-4 md:inline">
              {fmtInt(status.real.n_fixtures)} jogos ·{" "}
              {status.real.first_date ? fmtDate(status.real.first_date) : "—"}
            </span>
          ) : null}
          <div className="flex rounded-md border border-line-strong bg-surface-2 p-0.5">
            <Button
              size="sm"
              variant={source === "real" ? "accent" : "ghost"}
              disabled={!realAvailable}
              onClick={() => onChange("real")}
              title={
                realAvailable
                  ? "Jogos futuros reais com odds reais"
                  : "Sem jogos em cache: rode --import-fixtures-live"
              }
            >
              Reais
            </Button>
            <Button
              size="sm"
              variant={source === "synthetic" ? "accent" : "ghost"}
              onClick={() => onChange("synthetic")}
            >
              Sintéticos
            </Button>
          </div>
        </div>
      </div>

      {/* --------------------------------------------- aviso do sintetico */}
      {source === "synthetic" ? (
        <div
          role="alert"
          className="flex flex-col gap-1.5 rounded-lg border border-warn-500/45 bg-warn-900/35 px-3.5 py-3"
        >
          <p className="flex items-center gap-2 text-body font-semibold text-warn-300">
            <AlertTriangleIcon className="size-4 shrink-0" />
            Estes sinais não correspondem a jogos reais
          </p>
          <p className="text-[12px] leading-relaxed text-ink-2">
            O dataset local é gerado em memória: as rodadas têm datas fixas no
            código (<span className="num">betgsn/data.py</span>) e as odds são
            sintetizadas a partir do próprio modelo. Um jogo como
            &ldquo;Corinthians vs Palmeiras&rdquo; pode não existir no calendário.
          </p>
          <p className="text-[12px] leading-relaxed text-ink-2">
            Serve para testar o pipeline e comparar com os dados reais. Para
            decidir aposta, use a aba <b>Reais</b>.
          </p>
        </div>
      ) : null}

      {/* --------------------------------------------- vies medido do modelo */}
      {source === "real" && calibration ? (
        <div
          role="status"
          className="flex flex-col gap-1.5 rounded-lg border border-info-500/40 bg-info-900/25 px-3.5 py-3"
        >
          <p className="flex items-center gap-2 text-body font-semibold text-info-300">
            <InfoIcon className="size-4 shrink-0" />
            Os jogos e as odds são reais. O EV não é confiável.
          </p>
          <p className="text-[12px] leading-relaxed text-ink-2">
            O backtest mediu o modelo em {calibration.measured_on} contra a linha
            de fechamento: o EV previsto foi {fmtPct(calibration.ev_predicted)} e o
            retorno realizado {fmtPct(calibration.return_realized)} — um viés de{" "}
            <b className="text-warn-300">
              {calibration.gap_pp >= 0 ? "+" : ""}
              {(calibration.gap_pp * 100).toFixed(1)}pp
            </b>
            . O ROI simulado foi {fmtPct(calibration.simulated_roi)}.
          </p>
          <p className="text-[12px] leading-relaxed text-ink-2">
            Na prática: um sinal com EV +80% tem EV real próximo de zero. Use a
            lista como <b>triagem</b> de onde olhar, nunca como estimativa de
            retorno. Para a vantagem validada, rode{" "}
            <span className="num">python betgsn.py --scan-value</span>.
          </p>
          {skippedNoRating > 0 ? (
            <p className="text-[11px] text-ink-4">
              {fmtInt(skippedNoRating)} jogos futuros foram descartados por falta
              de rating do time (estreante ou nome divergente).
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
