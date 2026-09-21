/** Testes dos utilitarios de tabela (ordenacao e filtro locais). */

import { describe, expect, it } from "vitest";

import { sortBy, textFilter, toggleSort, type SortState } from "@/utils/table";

interface Row {
  name: string;
  value: number;
}

const rows: Row[] = [
  { name: "Bravo", value: 2 },
  { name: "alfa", value: 1 },
  { name: "Charlie", value: 3 },
];

describe("sortBy", () => {
  it("ordena numeros de forma decrescente", () => {
    expect(sortBy(rows, (r) => r.value, "desc").map((r) => r.value)).toEqual([3, 2, 1]);
  });

  it("ordena numeros de forma crescente", () => {
    expect(sortBy(rows, (r) => r.value, "asc").map((r) => r.value)).toEqual([1, 2, 3]);
  });

  it("ordena texto respeitando pt-BR", () => {
    const sorted = sortBy(rows, (r) => r.name, "asc").map((r) => r.name);
    expect(sorted).toEqual(["alfa", "Bravo", "Charlie"]);
  });

  it("nao muta o array original", () => {
    const original = [...rows];
    sortBy(rows, (r) => r.value, "desc");
    expect(rows).toEqual(original);
  });

  it("lida com lista vazia", () => {
    expect(sortBy([], (r: Row) => r.value, "asc")).toEqual([]);
  });
});

describe("toggleSort", () => {
  it("usa a direcao padrao ao trocar de coluna", () => {
    const current: SortState<"a" | "b"> = { key: "a", direction: "asc" };
    expect(toggleSort(current, "b", "desc")).toEqual({ key: "b", direction: "desc" });
  });

  it("inverte a direcao na mesma coluna", () => {
    const current: SortState<"a"> = { key: "a", direction: "desc" };
    expect(toggleSort(current, "a")).toEqual({ key: "a", direction: "asc" });
    expect(toggleSort({ key: "a", direction: "asc" }, "a")).toEqual({
      key: "a",
      direction: "desc",
    });
  });
});

describe("textFilter", () => {
  const fields = (r: Row) => [r.name, r.value];

  it("devolve tudo quando a busca e vazia", () => {
    expect(textFilter(rows, "", fields)).toHaveLength(3);
    expect(textFilter(rows, "   ", fields)).toHaveLength(3);
  });

  it("busca sem diferenciar maiuscula/minuscula", () => {
    expect(textFilter(rows, "BRAVO", fields).map((r) => r.name)).toEqual(["Bravo"]);
    expect(textFilter(rows, "charlie", fields).map((r) => r.name)).toEqual(["Charlie"]);
  });

  it("busca em campos numericos", () => {
    expect(textFilter(rows, "3", fields).map((r) => r.name)).toEqual(["Charlie"]);
  });

  it("devolve vazio quando nada casa", () => {
    expect(textFilter(rows, "zzz", fields)).toEqual([]);
  });

  it("nao muta o array original", () => {
    const original = [...rows];
    textFilter(rows, "zzz", fields);
    expect(rows).toEqual(original);
  });
});
