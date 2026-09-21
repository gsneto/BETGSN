/**
 * SegmentedControl — filtro de confianca.
 *
 * Substitui a ChipBar da GUI legada. Visual de segmented control moderno
 * (indicador deslizante), nao botoes grandes. O dot usa a cor semantica
 * do nivel correspondente.
 */

import { cn } from "@/utils/cn";

export interface Segment<T extends string> {
  value: T;
  label: string;
  /** classe de background do dot; ausente = sem dot */
  dotClass?: string;
  count?: number;
}

interface Props<T extends string> {
  segments: Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
  ariaLabel?: string;
}

export default function SegmentedControl<T extends string>({
  segments,
  value,
  onChange,
  className,
  ariaLabel,
}: Props<T>) {
  const index = Math.max(
    0,
    segments.findIndex((s) => s.value === value),
  );

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        "relative flex h-[34px] items-stretch rounded-md border border-line-strong bg-surface-2 p-0.5",
        className,
      )}
    >
      {/* indicador deslizante */}
      <span
        aria-hidden
        className="absolute top-0.5 bottom-0.5 rounded-[5px] bg-surface-active shadow-sm transition-[left,width] duration-[180ms] ease-[cubic-bezier(.2,.8,.2,1)]"
        style={{
          left: `calc(${(index / segments.length) * 100}% + 2px)`,
          width: `calc(${100 / segments.length}% - 4px)`,
        }}
      />
      {segments.map((s) => {
        const active = s.value === value;
        return (
          <button
            key={s.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(s.value)}
            className={cn(
              "relative z-1 flex flex-1 items-center justify-center gap-1.5 px-3 text-[12.5px] font-medium whitespace-nowrap",
              "transition-colors duration-150 focus-visible:shadow-ring",
              active ? "text-ink" : "text-ink-3 hover:text-ink-2",
            )}
          >
            {s.dotClass ? (
              <span
                aria-hidden
                className={cn(
                  "size-[5px] shrink-0 rounded-full transition-opacity duration-150",
                  s.dotClass,
                  active ? "opacity-100" : "opacity-60",
                )}
              />
            ) : null}
            {s.label}
            {s.count !== undefined ? (
              <span
                className={cn(
                  "num text-[10.5px] tabular-nums",
                  active ? "text-ink-2" : "text-ink-4",
                )}
              >
                {s.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
