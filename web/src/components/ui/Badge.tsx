/**
 * Badge — adaptado de TailAdmin `components/ui/badge/Badge.tsx`.
 *
 * Mudancas para o BETGSN:
 *  - paleta semantica quantitativa (accent/pos/neg/info) em vez de brand;
 *  - backgrounds a 10-14% de opacidade (o template usa 50/15 chapados),
 *    porque a tabela e densa e fundos saturados poluem a leitura;
 *  - suporte a `dot`, usado pela coluna CONFIANCA: "● FORTE".
 */

import type { ReactNode } from "react";
import { cn } from "@/utils/cn";
import type { BadgeTone } from "./badgeTone";

type BadgeSize = "sm" | "md";

interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
  size?: BadgeSize;
  dot?: boolean;
  className?: string;
}

const tones: Record<BadgeTone, { chip: string; dot: string }> = {
  accent: { chip: "bg-accent-400/12 text-accent-300", dot: "bg-accent-400" },
  positive: { chip: "bg-pos-400/12 text-pos-300", dot: "bg-pos-400" },
  negative: { chip: "bg-neg-400/12 text-neg-300", dot: "bg-neg-400" },
  info: { chip: "bg-info-400/12 text-info-300", dot: "bg-info-400" },
  warning: { chip: "bg-warn-400/12 text-warn-300", dot: "bg-warn-400" },
  neutral: { chip: "bg-surface-3 text-ink-2", dot: "bg-ink-3" },
};

const sizes: Record<BadgeSize, string> = {
  sm: "h-[18px] px-1.5 text-[10.5px] gap-1",
  md: "h-[22px] px-2 text-[11.5px] gap-1.5",
};

export default function Badge({
  children,
  tone = "neutral",
  size = "md",
  dot = false,
  className,
}: BadgeProps) {
  const t = tones[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full font-semibold tracking-wide whitespace-nowrap",
        sizes[size],
        t.chip,
        className,
      )}
    >
      {dot ? (
        <span className={cn("size-[5px] shrink-0 rounded-full", t.dot)} aria-hidden />
      ) : null}
      {children}
    </span>
  );
}
