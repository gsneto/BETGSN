/**
 * Card / Panel — adaptado de TailAdmin
 * `components/common/ComponentCard.tsx`.
 *
 * Mudancas para o BETGSN: header em label-caps com slot de acao a
 * direita, padding menor (terminal denso) e variante `interactive`
 * com elevacao discreta no hover.
 */

import type { ReactNode } from "react";
import { cn } from "@/utils/cn";

interface CardProps {
  children: ReactNode;
  title?: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  interactive?: boolean;
  padded?: boolean;
  className?: string;
  bodyClassName?: string;
}

export default function Card({
  children,
  title,
  hint,
  action,
  interactive = false,
  padded = true,
  className,
  bodyClassName,
}: CardProps) {
  return (
    <section
      className={cn(
        interactive ? "panel-interactive" : "panel",
        "flex min-w-0 flex-col",
        className,
      )}
    >
      {title || action ? (
        <header className="flex min-h-[42px] items-center justify-between gap-3 border-b border-line px-4 py-2">
          <div className="min-w-0">
            <h2 className="label-caps truncate">{title}</h2>
            {hint ? (
              <p className="mt-0.5 truncate text-[11.5px] text-ink-4">{hint}</p>
            ) : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </header>
      ) : null}
      <div className={cn(padded && "p-4", "min-w-0 flex-1", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}
