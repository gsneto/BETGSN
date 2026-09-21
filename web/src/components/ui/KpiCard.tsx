/**
 * KpiCard — cartao de metrica.
 *
 * Estrutura derivada de TailAdmin `components/ecommerce/EcommerceMetrics.tsx`
 * (label / valor / delta), adaptada para o BETGSN:
 *  - valor em fonte mono tabular (comparacao vertical entre cards);
 *  - contexto secundario textual obrigatorio, porque metrica sem base
 *    amostral nao diz nada;
 *  - sparkline opcional derivada de valores JA calculados pelo backend.
 *
 * Nenhum numero e inventado: todos vem de `SignalsKpis`/endpoints.
 */

import type { ReactNode } from "react";
import { cn } from "@/utils/cn";
import { Tooltip } from "./States";

type Tone = "default" | "accent" | "positive" | "negative" | "info";

const valueTone: Record<Tone, string> = {
  default: "text-ink",
  accent: "text-accent-300",
  positive: "text-pos-400",
  negative: "text-neg-400",
  info: "text-info-300",
};

interface KpiCardProps {
  label: string;
  value: string;
  context?: string;
  delta?: { text: string; tone: Tone };
  tone?: Tone;
  tooltip?: string;
  spark?: number[];
  icon?: ReactNode;
}

export default function KpiCard({
  label,
  value,
  context,
  delta,
  tone = "default",
  tooltip,
  spark,
  icon,
}: KpiCardProps) {
  const body = (
    <article className="panel-interactive flex h-[104px] flex-col justify-between p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="label-caps">{label}</span>
        {icon ? <span className="shrink-0 text-ink-4">{icon}</span> : null}
      </div>

      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p
            className={cn(
              "num text-kpi leading-none font-semibold tracking-[-0.02em]",
              valueTone[tone],
            )}
          >
            {value}
          </p>
          {context ? (
            <p className="mt-1.5 truncate text-[11.5px] leading-none text-ink-3">
              {context}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1">
          {delta ? (
            <span
              className={cn(
                "num text-[11.5px] leading-none font-semibold",
                valueTone[delta.tone],
              )}
            >
              {delta.text}
            </span>
          ) : null}
          {spark && spark.length > 1 ? <Sparkline values={spark} tone={tone} /> : null}
        </div>
      </div>
    </article>
  );

  return tooltip ? (
    <Tooltip content={tooltip} className="block">
      {body}
    </Tooltip>
  ) : (
    body
  );
}

const strokeTone: Record<Tone, string> = {
  default: "stroke-ink-3",
  accent: "stroke-accent-400",
  positive: "stroke-pos-400",
  negative: "stroke-neg-400",
  info: "stroke-info-400",
};

/**
 * Sparkline fiel: escala linear simples entre min e max dos valores
 * recebidos, sem suavizacao que distorca a forma.
 */
function Sparkline({ values, tone }: { values: number[]; tone: Tone }) {
  const w = 62;
  const h = 22;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(2)},${(h - ((v - min) / range) * h).toFixed(2)}`)
    .join(" ");

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      aria-hidden
      className="overflow-visible"
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={cn(strokeTone[tone], "opacity-70")}
      />
    </svg>
  );
}
