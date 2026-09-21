/**
 * Ordenacao e filtragem de dados JA RECEBIDOS do backend.
 * Permitido pelo contrato: apresentar, ordenar, filtrar. Proibido:
 * recalcular metrica estatistica.
 */

export type SortDirection = "asc" | "desc";

export interface SortState<K extends string> {
  key: K;
  direction: SortDirection;
}

/** Ordena uma copia do array por um extrator de valor. */
export function sortBy<T>(
  rows: readonly T[],
  extract: (row: T) => number | string,
  direction: SortDirection,
): T[] {
  const factor = direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = extract(a);
    const vb = extract(b);
    if (typeof va === "number" && typeof vb === "number") {
      return (va - vb) * factor;
    }
    return String(va).localeCompare(String(vb), "pt-BR") * factor;
  });
}

/** Alterna a direcao de ordenacao ao clicar no mesmo cabecalho. */
export function toggleSort<K extends string>(
  current: SortState<K>,
  key: K,
  defaultDirection: SortDirection = "desc",
): SortState<K> {
  if (current.key !== key) return { key, direction: defaultDirection };
  return {
    key,
    direction: current.direction === "asc" ? "desc" : "asc",
  };
}

/** Busca textual simples sobre campos escolhidos. */
export function textFilter<T>(
  rows: readonly T[],
  query: string,
  fields: (row: T) => (string | number)[],
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...rows];
  return rows.filter((row) =>
    fields(row).some((value) => String(value).toLowerCase().includes(q)),
  );
}
