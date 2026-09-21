/**
 * TopSignalCards — cards compactos das melhores oportunidades.
 *
 * Equivalente ao InsightsPanel da GUI legada. Apenas apresentacao
 * analitica: nao existe acao de execucao de aposta.
 */

import Badge from "@/components/ui/Badge";
import { confidenceTone } from "@/components/ui/badgeTone";
import type { Signal } from "@/types/api";
import { cn } from "@/utils/cn";
import {
  confidenceLabel,
  fmtEdgePp,
  fmtOdd,
  fmtPct,
  fmtPctSigned,
  signedColorClass,
} from "@/utils/format";

export default function TopSignalCards({ rows }: { rows: Signal[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
      {rows.map((s, i) => (
        <article key={s.id} className="panel-interactive flex flex-col gap-2.5 p-3.5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <span className="num text-[10.5px] font-semibold text-ink-4">
                #{i + 1}
              </span>
              <h3
                className="truncate text-body font-semibold text-ink"
                title={s.match}
              >
                {s.match}
              </h3>
              <p className="truncate text-[11.5px] text-ink-4" title={s.outcome}>
                {s.outcome} · {s.market}
              </p>
            </div>
            <Badge tone={confidenceTone(s.confidence)} dot size="sm">
              {confidenceLabel(s.confidence)}
            </Badge>
          </div>

          <div className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-line pt-2.5">
            <Metric label="Modelo" value={fmtPct(s.model_prob)} />
            <Metric label="Mercado" value={fmtPct(s.market_prob)} muted />
            <Metric
              label="Edge"
              value={fmtEdgePp(s.edge)}
              className={signedColorClass(s.edge)}
            />
            <Metric
              label="EV"
              value={fmtPctSigned(s.ev)}
              className={signedColorClass(s.ev)}
            />
            <Metric label="Odd" value={fmtOdd(s.best_odd)} />
            <Metric label="Fonte" value={s.best_book} muted mono={false} />
          </div>
        </article>
      ))}
    </div>
  );
}

function Metric({
  label,
  value,
  muted = false,
  mono = true,
  className,
}: {
  label: string;
  value: string;
  muted?: boolean;
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="label-caps truncate">{label}</p>
      <p
        className={cn(
          "mt-0.5 truncate text-body font-semibold",
          mono && "num",
          className ?? (muted ? "text-ink-3" : "text-ink"),
        )}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}
