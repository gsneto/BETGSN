/**
 * Primitivos de tabela — adaptados de TailAdmin
 * `components/ui/table/index.tsx`, que e apenas um wrapper fino.
 *
 * Mudancas para o BETGSN (high-density analytics table):
 *  - `TableShell` adiciona scroll horizontal e vertical com sticky header,
 *    necessario para uma grade de 16 colunas e centenas de linhas;
 *  - `Th` com estado de ordenacao e indicador discreto;
 *  - linhas de 44px (o template nao define altura) e separadores sutis;
 *  - alinhamento numerico a direita como padrao de coluna de dado.
 */

import type { ReactNode, ThHTMLAttributes, TdHTMLAttributes } from "react";
import { cn } from "@/utils/cn";
import type { SortDirection } from "@/utils/table";

export function TableShell({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "thin-scrollbar relative overflow-auto rounded-lg border border-line bg-surface-1",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Table({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <table
      className={cn("w-full border-separate border-spacing-0 text-table", className)}
    >
      {children}
    </table>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="sticky-head">{children}</thead>;
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody>{children}</tbody>;
}

interface RowProps {
  children: ReactNode;
  selected?: boolean;
  onClick?: () => void;
  className?: string;
}

export function Tr({ children, selected, onClick, className }: RowProps) {
  return (
    <tr
      onClick={onClick}
      aria-selected={selected || undefined}
      className={cn(
        "data-row h-11",
        onClick && "cursor-pointer",
        selected && "bg-surface-active",
        className,
      )}
    >
      {children}
    </tr>
  );
}

type Align = "start" | "end" | "center";

const alignClass: Record<Align, string> = {
  start: "text-start",
  end: "text-end",
  center: "text-center",
};

interface ThProps extends Omit<ThHTMLAttributes<HTMLTableCellElement>, "align"> {
  children: ReactNode;
  align?: Align;
  sortable?: boolean;
  active?: boolean;
  direction?: SortDirection;
  width?: number;
  title?: string;
}

export function Th({
  children,
  align = "end",
  sortable = false,
  active = false,
  direction = "desc",
  width,
  className,
  onClick,
  title,
  ...rest
}: ThProps) {
  return (
    <th
      {...rest}
      title={title}
      onClick={sortable ? onClick : undefined}
      scope="col"
      aria-sort={
        sortable ? (active ? (direction === "asc" ? "ascending" : "descending") : "none") : undefined
      }
      style={width ? { width, minWidth: width } : undefined}
      className={cn(
        "h-[34px] border-b border-line bg-surface-2 px-3 font-semibold",
        "text-label tracking-[0.06em] whitespace-nowrap uppercase",
        active ? "text-accent-300" : "text-ink-3",
        sortable && "cursor-pointer transition-colors duration-150 select-none hover:text-ink-2",
        alignClass[align],
        className,
      )}
    >
      <span
        className={cn(
          "inline-flex items-center gap-1",
          align === "end" && "flex-row-reverse",
        )}
      >
        {children}
        {sortable ? (
          <span
            aria-hidden
            className={cn(
              "text-[8px] leading-none transition-opacity duration-150",
              active ? "text-accent-400 opacity-100" : "opacity-0",
            )}
          >
            {direction === "asc" ? "▲" : "▼"}
          </span>
        ) : null}
      </span>
    </th>
  );
}

interface TdProps extends Omit<TdHTMLAttributes<HTMLTableCellElement>, "align"> {
  children?: ReactNode;
  align?: Align;
  mono?: boolean;
}

export function Td({
  children,
  align = "end",
  mono = false,
  className,
  ...rest
}: TdProps) {
  return (
    <td
      {...rest}
      className={cn(
        "border-b border-line px-3 align-middle whitespace-nowrap",
        mono && "num",
        alignClass[align],
        className,
      )}
    >
      {children}
    </td>
  );
}
