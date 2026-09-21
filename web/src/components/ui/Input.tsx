/**
 * Input / Select / Field — adaptados de TailAdmin
 * `components/form/input/InputField.tsx`, `form/Select.tsx` e
 * `form/Label.tsx`.
 *
 * Mudancas para o BETGSN:
 *  - altura 34px (o template usa h-11 = 44px), coerente com a barra
 *    de parametros densa do terminal;
 *  - foco em accent ambar com shadow-ring em vez do brand azul;
 *  - `suffix` para unidades (%, x) que o template nao possui;
 *  - inputs numericos usam a classe `num` (tabular-nums + mono).
 */

import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { cn } from "@/utils/cn";

/* --------------------------------------------------------------- Field */

interface FieldProps {
  label: string;
  htmlFor?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}

export function Field({ label, htmlFor, hint, children, className }: FieldProps) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <label htmlFor={htmlFor} className="label-caps" title={hint}>
        {label}
      </label>
      {children}
    </div>
  );
}

/* --------------------------------------------------------------- Input */

const controlBase =
  "h-[34px] w-full rounded-md border border-line-strong bg-surface-2 px-2.5 " +
  "text-body text-ink placeholder:text-ink-4 " +
  "transition-[border-color,background-color,box-shadow] duration-150 " +
  "hover:border-line-active/40 " +
  "focus:border-line-active focus:shadow-ring focus:outline-none " +
  "disabled:cursor-not-allowed disabled:opacity-60";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  suffix?: string;
  mono?: boolean;
}

export function Input({ suffix, mono = false, className, ...rest }: InputProps) {
  const input = (
    <input
      {...rest}
      className={cn(controlBase, mono && "num", suffix && "pe-8", className)}
    />
  );
  if (!suffix) return input;
  return (
    <div className="relative min-w-0">
      {input}
      <span className="pointer-events-none absolute end-2.5 top-1/2 -translate-y-1/2 text-[11.5px] text-ink-4">
        {suffix}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------- Select */

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  options: SelectOption[];
  mono?: boolean;
}

export function Select({ options, mono = false, className, ...rest }: SelectProps) {
  return (
    <div className="relative min-w-0">
      <select
        {...rest}
        className={cn(controlBase, "appearance-none pe-8", mono && "num", className)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-surface-2 text-ink">
            {o.label}
          </option>
        ))}
      </select>
      <svg
        aria-hidden
        viewBox="0 0 20 20"
        className="pointer-events-none absolute end-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-3"
        fill="none"
      >
        <path
          d="M4.8 8L10 13.2L15.2 8"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------- Switch */

interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
  id?: string;
}

/** Toggle — adaptado de TailAdmin `components/form/switch/Switch.tsx`. */
export function Switch({ checked, onChange, label, disabled, id }: SwitchProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-[22px] w-10 shrink-0 rounded-full border transition-colors duration-150",
        "focus-visible:shadow-ring",
        checked
          ? "border-accent-500/60 bg-accent-400/25"
          : "border-line-strong bg-surface-3",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <span
        className={cn(
          "absolute top-[2px] size-[16px] rounded-full transition-all duration-150 ease-[cubic-bezier(.2,.8,.2,1)]",
          checked ? "start-[20px] bg-accent-400" : "start-[2px] bg-ink-3",
        )}
      />
    </button>
  );
}

/* --------------------------------------------------------- SearchInput */

interface SearchProps extends InputHTMLAttributes<HTMLInputElement> {
  onClear?: () => void;
}

export function SearchInput({ onClear, value, className, ...rest }: SearchProps) {
  return (
    <div className={cn("relative min-w-0", className)}>
      <svg
        aria-hidden
        viewBox="0 0 20 20"
        fill="none"
        className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-4"
      >
        <circle cx="9" cy="9" r="5.4" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M13.2 13.2L17 17"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
      <input
        {...rest}
        value={value}
        className={cn(controlBase, "ps-8", onClear && value ? "pe-8" : undefined)}
      />
      {onClear && value ? (
        <button
          type="button"
          onClick={onClear}
          aria-label="Limpar busca"
          className="absolute end-2 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-ink-4 transition-colors hover:text-ink-2"
        >
          <svg aria-hidden viewBox="0 0 20 20" fill="none" className="size-3.5">
            <path
              d="M5.5 5.5L14.5 14.5M14.5 5.5L5.5 14.5"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </button>
      ) : null}
    </div>
  );
}
