/**
 * Formatadores de apresentacao. Convertem os valores JA CALCULADOS pelo
 * backend em texto. Nao ha nenhuma regra estatistica aqui — apenas
 * formatacao numerica e de data.
 */

const nf = (min: number, max = min) =>
  new Intl.NumberFormat("pt-BR", {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
  });

const int0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const dec2 = nf(2);

/** Inteiro com separador de milhar: 1.234 */
export const fmtInt = (v: number): string => int0.format(v);

/** Duas casas: 12,34 */
export const fmtNum = (v: number | null, digits = 2): string => v == null ? "—" : nf(digits).format(v);

/** Odd decimal: 2,15 */
export const fmtOdd = (v: number): string => dec2.format(v);

/** Fracao -> percentual. 0.9163 -> "91,6%" */
export const fmtPct = (v: number, digits = 1): string =>
  `${nf(digits).format(v * 100)}%`;

/** Fracao -> percentual com sinal. 0.1163 -> "+11,6%" */
export const fmtPctSigned = (v: number, digits = 1): string => {
  const value = v * 100;
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${nf(digits).format(Math.abs(value))}%`;
};

/**
 * Edge em pontos percentuais. O backend manda diferenca de
 * probabilidade (0.776 = 77,6pp), por isso o sufixo "pp" e nao "%".
 */
export const fmtEdgePp = (v: number, digits = 1): string => {
  const value = v * 100;
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${nf(digits).format(Math.abs(value))}pp`;
};

/** Dinheiro sem simbolo (a banca nao tem moeda definida): 1.234,56 */
export const fmtMoney = (v: number): string => dec2.format(v);

/** Dinheiro com sinal explicito: +11,63 / -4,20 */
export const fmtMoneySigned = (v: number): string => {
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${sign}${dec2.format(Math.abs(v))}`;
};

/** Numero com sinal: +0,12 */
export const fmtSigned = (v: number, digits = 2): string => {
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${sign}${nf(digits).format(Math.abs(v))}`;
};

/** Milissegundos legiveis: 75 ms / 1,24 s */
export const fmtDuration = (ms: number): string =>
  ms < 1000 ? `${Math.round(ms)} ms` : `${nf(2).format(ms / 1000)} s`;

/** "2026-09-19" -> "19/09/2026" (sem timezone: string pura do backend) */
export function fmtDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

/** "2026-09-18 14:03:22" -> "18/09/2026 14:03" */
export function fmtDateTime(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : value;
}

/** Hora curta de um timestamp do backend: "14:03:22" */
export function fmtClock(value: string | null | undefined): string {
  if (!value) return "—";
  const m = /(\d{2}):(\d{2}):(\d{2})/.exec(value);
  return m ? `${m[1]}:${m[2]}:${m[3]}` : value;
}

/** Placar provavel: 2-1 */
export const fmtScoreline = (home: number, away: number): string =>
  `${home}-${away}`;

/** Rotulo de confianca com acento correto para exibicao. */
export function confidenceLabel(level: string): string {
  switch (level) {
    case "MEDIA":
      return "MÉDIA";
    case "FORTE":
      return "FORTE";
    case "FRACA":
      return "FRACA";
    default:
      return level;
  }
}

/** Classe de cor semantica por sinal do numero. */
export function signedColorClass(v: number): string {
  if (v > 0) return "text-pos-400";
  if (v < 0) return "text-neg-400";
  return "text-ink-3";
}
