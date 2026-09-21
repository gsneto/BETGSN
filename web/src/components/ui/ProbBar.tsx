/**
 * ProbBar — mini barra de probabilidade.
 *
 * REGRA: a largura e SEMPRE `value * 100%` da faixa, sem normalizacao
 * relativa e sem minimo artificial visivel. Uma barra que exagera valores
 * pequenos e uma visualizacao enganosa; aqui 5% ocupa 5%.
 */

import { cn } from "@/utils/cn";

type Tone = "model" | "market" | "positive" | "negative" | "info";

const fill: Record<Tone, string> = {
  model: "bg-accent-400/70",
  market: "bg-ink-3/60",
  positive: "bg-pos-400/70",
  negative: "bg-neg-400/70",
  info: "bg-info-400/70",
};

interface Props {
  /** fracao em [0,1] — exatamente o valor recebido do backend */
  value: number;
  tone?: Tone;
  className?: string;
  title?: string;
}

export default function ProbBar({
  value,
  tone = "model",
  className,
  title,
}: Props) {
  // arredonda para 1 casa: evita larguras como "91.60000000000001%"
  const clamped = Math.max(0, Math.min(1, value));
  const pct = Math.round(clamped * 1000) / 10;
  return (
    <span
      role="meter"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      title={title}
      className={cn(
        "block h-[3px] w-full overflow-hidden rounded-full bg-surface-3",
        className,
      )}
    >
      <span
        className={cn("block h-full rounded-full transition-[width] duration-200", fill[tone])}
        style={{ width: `${pct}%` }}
      />
    </span>
  );
}

/**
 * Par modelo x mercado empilhado: leitura vertical imediata de qual dos
 * dois esta maior. Ambas as barras usam a mesma escala 0-100%.
 */
export function ProbCompareBars({
  model,
  market,
}: {
  model: number;
  market: number;
}) {
  return (
    <span className="flex w-full flex-col gap-[3px]">
      <ProbBar value={model} tone="model" title={`Modelo ${(model * 100).toFixed(1)}%`} />
      <ProbBar
        value={market}
        tone="market"
        title={`Mercado ${(market * 100).toFixed(1)}%`}
      />
    </span>
  );
}
