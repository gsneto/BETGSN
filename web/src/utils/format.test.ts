/**
 * Testes dos formatadores.
 *
 * A regra do projeto: o frontend NUNCA recalcula estatistica, so formata.
 * Estes testes travam o contrato de apresentacao — em especial o sufixo
 * "pp" do edge (diferenca de probabilidade) versus "%" do EV (retorno).
 */

import { describe, expect, it } from "vitest";

import {
  confidenceLabel,
  fmtDate,
  fmtDateTime,
  fmtClock,
  fmtDuration,
  fmtEdgePp,
  fmtInt,
  fmtMoney,
  fmtMoneySigned,
  fmtNum,
  fmtOdd,
  fmtPct,
  fmtPctSigned,
  fmtScoreline,
  fmtSigned,
  signedColorClass,
} from "@/utils/format";

describe("percentuais", () => {
  it("converte fracao em percentual", () => {
    expect(fmtPct(0.9163)).toBe("91,6%");
    expect(fmtPct(0.5)).toBe("50,0%");
    expect(fmtPct(0)).toBe("0,0%");
    expect(fmtPct(1)).toBe("100,0%");
  });

  it("respeita o numero de casas", () => {
    expect(fmtPct(0.9163, 2)).toBe("91,63%");
    expect(fmtPct(0.9163, 0)).toBe("92%");
  });

  it("adiciona sinal explicito", () => {
    expect(fmtPctSigned(0.1163)).toBe("+11,6%");
    expect(fmtPctSigned(-0.05)).toBe("-5,0%");
    expect(fmtPctSigned(0)).toBe("0,0%");
  });
});

describe("edge em pontos percentuais", () => {
  it("usa sufixo pp, nao %", () => {
    // 0.776 e diferenca de probabilidade = 77,6pp (nao 77,6%)
    expect(fmtEdgePp(0.776)).toBe("+77,6pp");
    expect(fmtEdgePp(-0.031)).toBe("-3,1pp");
    expect(fmtEdgePp(0)).toBe("0,0pp");
  });

  it("nunca confunde edge com EV", () => {
    expect(fmtEdgePp(0.1)).not.toBe(fmtPctSigned(0.1));
    expect(fmtEdgePp(0.1)).toContain("pp");
    expect(fmtPctSigned(0.1)).toContain("%");
  });
});

describe("numeros", () => {
  it("formata inteiros com separador de milhar", () => {
    expect(fmtInt(1234)).toBe("1.234");
    expect(fmtInt(0)).toBe("0");
  });

  it("formata com casas decimais", () => {
    expect(fmtNum(2.5)).toBe("2,50");
    expect(fmtNum(2.5, 4)).toBe("2,5000");
  });

  it("formata odds", () => {
    expect(fmtOdd(1.22)).toBe("1,22");
    expect(fmtOdd(5.2)).toBe("5,20");
  });

  it("formata com sinal", () => {
    expect(fmtSigned(0.12)).toBe("+0,12");
    expect(fmtSigned(-0.12)).toBe("-0,12");
    expect(fmtSigned(0)).toBe("0,00");
  });
});

describe("dinheiro", () => {
  it("formata valores", () => {
    expect(fmtMoney(1234.5)).toBe("1.234,50");
    expect(fmtMoney(10)).toBe("10,00");
  });

  it("formata com sinal", () => {
    expect(fmtMoneySigned(11.63)).toBe("+11,63");
    expect(fmtMoneySigned(-250)).toBe("-250,00");
  });
});

describe("datas", () => {
  it("converte data ISO", () => {
    expect(fmtDate("2025-05-10")).toBe("10/05/2025");
  });

  it("aceita data com hora", () => {
    expect(fmtDate("2025-05-10 16:00")).toBe("10/05/2025");
  });

  it("devolve o original quando nao reconhece", () => {
    expect(fmtDate("nao e data")).toBe("nao e data");
  });

  it("formata data e hora", () => {
    expect(fmtDateTime("2025-05-10 16:00:00")).toBe("10/05/2025 16:00");
    expect(fmtDateTime("2025-05-10T16:00:00")).toBe("10/05/2025 16:00");
  });

  it("extrai a hora", () => {
    expect(fmtClock("2025-05-10 16:05:09")).toBe("16:05:09");
    expect(fmtClock(null)).toBe("—");
    expect(fmtClock(undefined)).toBe("—");
  });

  it("formata duracao", () => {
    expect(fmtDuration(75)).toBe("75 ms");
    expect(fmtDuration(1240)).toBe("1,24 s");
  });
});

describe("apresentacao de dominio", () => {
  it("traduz o nivel de confianca", () => {
    expect(confidenceLabel("FORTE")).toBe("FORTE");
    expect(confidenceLabel("MEDIA")).toBe("MÉDIA");
    expect(confidenceLabel("FRACA")).toBe("FRACA");
    expect(confidenceLabel("OUTRO")).toBe("OUTRO");
  });

  it("formata placar provavel", () => {
    expect(fmtScoreline(2, 1)).toBe("2-1");
  });

  it("mapeia cor pelo sinal do numero", () => {
    expect(signedColorClass(1)).toBe("text-pos-400");
    expect(signedColorClass(-1)).toBe("text-neg-400");
    expect(signedColorClass(0)).toBe("text-ink-3");
  });
});
