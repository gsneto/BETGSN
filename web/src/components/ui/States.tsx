/**
 * Estados de UI: Skeleton, EmptyState, ErrorPanel, Tooltip, Toasts.
 *
 * Skeleton e Tooltip sao adaptados dos equivalentes TailAdmin
 * (`ui/spinner`, `ui/tooltip`), com os tokens do BETGSN e tamanhos
 * compativveis com a densidade da tabela.
 */

import { useState, type ReactNode } from "react";
import { cn } from "@/utils/cn";
import {
  AlertTriangleIcon,
  CheckIcon,
  CloseIcon,
  InfoIcon,
  SearchOffIcon,
} from "./icons";
import Button from "./Button";
import { useStore } from "@/store/context";

/* ------------------------------------------------------------ Skeleton */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4 w-full", className)} aria-hidden />;
}

/** Skeletons de KPI, com a mesma altura do card real: sem layout shift. */
export function KpiSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="panel h-[104px] p-4">
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="mt-3 h-7 w-24" />
          <Skeleton className="mt-3 h-2.5 w-28" />
        </div>
      ))}
    </div>
  );
}

/** Skeleton de tabela com linhas de 44px, igual a tabela real. */
export function TableSkeleton({
  rows = 10,
  cols = 8,
}: {
  rows?: number;
  cols?: number;
}) {
  return (
    <div className="panel overflow-hidden">
      <div className="flex h-[34px] items-center gap-4 border-b border-line bg-surface-2 px-3">
        {Array.from({ length: cols }, (_, i) => (
          <Skeleton key={i} className="h-2.5 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex h-11 items-center gap-4 border-b border-line px-3">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} className="h-3 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------- EmptyState */

interface EmptyStateProps {
  title: string;
  hint?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({
  title,
  hint,
  icon,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 py-14 text-center",
        className,
      )}
    >
      <span className="mb-1 flex size-10 items-center justify-center rounded-full bg-surface-2 text-ink-4">
        {icon ?? <SearchOffIcon className="size-5" />}
      </span>
      <p className="text-body-lg font-medium text-ink-2">{title}</p>
      {hint ? <p className="max-w-sm text-body text-ink-4">{hint}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/* ---------------------------------------------------------- ErrorPanel */

interface ErrorPanelProps {
  message: string;
  onRetry?: () => void;
  className?: string;
}

/**
 * Erro recuperavel. Nunca exibe traceback: a API ja devolve mensagem
 * estruturada e o detalhe tecnico fica no log do backend.
 */
export function ErrorPanel({ message, onRetry, className }: ErrorPanelProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-lg border border-neg-700/50 bg-neg-900/50 p-4",
        className,
      )}
    >
      <AlertTriangleIcon className="mt-0.5 size-4 shrink-0 text-neg-400" />
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium text-neg-300">
          Nao foi possivel carregar os dados
        </p>
        <p className="mt-0.5 text-body break-words text-ink-2">{message}</p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Tentar novamente
        </Button>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------- Tooltip */

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Tooltip({ content, children, className }: TooltipProps) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className={cn("relative inline-flex", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open ? (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 z-999 w-max max-w-[280px] -translate-x-1/2 rounded-md border border-line-strong bg-surface-3 px-2.5 py-1.5 text-[11.5px] leading-relaxed font-normal whitespace-normal text-ink-2 normal-case shadow-lg"
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}

/* -------------------------------------------------------------- Toasts */

export function ToastStack() {
  const { toasts, dismissToast } = useStore();
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-9999 flex w-[340px] flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className={cn(
            "fade-up pointer-events-auto flex items-start gap-2.5 rounded-lg border p-3 shadow-lg backdrop-blur-sm",
            t.tone === "success" && "border-pos-700/50 bg-pos-900/85 text-pos-300",
            t.tone === "error" && "border-neg-700/50 bg-neg-900/85 text-neg-300",
            t.tone === "info" && "border-line-strong bg-surface-3/95 text-ink-2",
          )}
        >
          <span className="mt-px shrink-0">
            {t.tone === "success" ? (
              <CheckIcon className="size-4" />
            ) : t.tone === "error" ? (
              <AlertTriangleIcon className="size-4" />
            ) : (
              <InfoIcon className="size-4" />
            )}
          </span>
          <p className="min-w-0 flex-1 text-body break-words">{t.message}</p>
          <button
            type="button"
            onClick={() => dismissToast(t.id)}
            aria-label="Fechar"
            className="shrink-0 rounded-sm p-0.5 opacity-60 transition-opacity hover:opacity-100"
          >
            <CloseIcon className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
