/**
 * Button — adaptado de TailAdmin `components/ui/button/Button.tsx`.
 *
 * Mudancas para o BETGSN:
 *  - variantes do terminal (accent/outline/ghost/danger) sobre tokens navy;
 *  - alturas compactas (32/36/40px) em vez do padding generoso do template;
 *  - estado `loading` embutido (o TailAdmin nao tem) para o RECALCULAR;
 *  - microinteracao: hover translateY(-1px), active scale(.985).
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/utils/cn";

type Variant = "accent" | "outline" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  children: ReactNode;
  variant?: Variant;
  size?: Size;
  startIcon?: ReactNode;
  endIcon?: ReactNode;
  loading?: boolean;
  fullWidth?: boolean;
}

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[12.5px] gap-1.5",
  md: "h-9 px-3.5 text-body gap-2",
  lg: "h-10 px-4 text-body gap-2",
};

const variants: Record<Variant, string> = {
  accent:
    "bg-accent-400 text-app font-semibold hover:bg-accent-300 active:bg-accent-500 " +
    "shadow-sm disabled:bg-accent-800 disabled:text-accent-200/60",
  outline:
    "bg-surface-2 text-ink border border-line-strong hover:bg-surface-3 " +
    "hover:border-line-active/50 active:bg-surface-active",
  ghost:
    "bg-transparent text-ink-2 hover:bg-surface-2 hover:text-ink active:bg-surface-3",
  danger:
    "bg-neg-900 text-neg-300 border border-neg-700/60 hover:bg-neg-700/40 hover:text-neg-300",
};

export default function Button({
  children,
  variant = "outline",
  size = "md",
  startIcon,
  endIcon,
  loading = false,
  fullWidth = false,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  const isDisabled = disabled || loading;
  return (
    <button
      type="button"
      {...rest}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center rounded-md whitespace-nowrap select-none",
        "transition-[transform,background-color,border-color,color,box-shadow]",
        "duration-150 ease-[cubic-bezier(.2,.8,.2,1)]",
        "focus-visible:shadow-ring",
        sizes[size],
        variants[variant],
        fullWidth && "w-full",
        isDisabled
          ? "cursor-not-allowed opacity-70"
          : "hover:-translate-y-px active:translate-y-0 active:scale-[.985]",
        className,
      )}
    >
      {startIcon ? (
        <span className={cn("flex shrink-0 items-center", loading && "spin-slow")}>
          {startIcon}
        </span>
      ) : null}
      {children}
      {endIcon ? <span className="flex shrink-0 items-center">{endIcon}</span> : null}
    </button>
  );
}
