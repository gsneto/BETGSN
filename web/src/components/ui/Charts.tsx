/**
 * MiniBarChart — grafico de barras leve em SVG.
 *
 * O TailAdmin usa react-apexcharts. Aqui NAO vale a pena carregar uma
 * biblioteca de charts (~500kB) para tres graficos simples: o SVG abaixo
 * e deterministico, acessivel e nao recria instancia a cada render.
 *
 * A escala e sempre linear a partir dos valores recebidos; nao ha
 * normalizacao que distorca a leitura.
 */

import { cn } from "@/utils/cn";

export interface BarDatum {
  label: string;
  value: number;
  tone?: "accent" | "positive" | "info" | "neutral";
}

const barTone = {
  accent: "bg-accent-400/75",
  positive: "bg-pos-400/75",
  info: "bg-info-400/75",
  neutral: "bg-ink-3/60",
} as const;

interface Props {
  data: BarDatum[];
  /** sufixo do valor exibido na ponta da barra */
  formatValue?: (v: number) => string;
  className?: string;
  emptyLabel?: string;
}

export function HorizontalBars({
  data,
  formatValue = (v) => String(v),
  className,
  emptyLabel = "Sem dados",
}: Props) {
  if (data.length === 0) {
    return <p className="py-6 text-center text-body text-ink-4">{emptyLabel}</p>;
  }
  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <ul className={cn("flex flex-col gap-2.5", className)}>
      {data.map((d) => (
        <li key={d.label} className="flex items-center gap-3">
          <span
            className="w-[150px] shrink-0 truncate text-[12.5px] text-ink-2"
            title={d.label}
          >
            {d.label}
          </span>
          <span className="h-[14px] min-w-0 flex-1 overflow-hidden rounded-sm bg-surface-2">
            <span
              className={cn(
                "block h-full rounded-sm transition-[width] duration-200",
                barTone[d.tone ?? "accent"],
              )}
              style={{ width: `${(d.value / max) * 100}%` }}
            />
          </span>
          <span className="num w-[68px] shrink-0 text-end text-[12.5px] font-medium text-ink">
            {formatValue(d.value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * ColumnChart — colunas verticais, usado na distribuicao de EV.
 * Valores pequenos continuam pequenos: escala linear honesta.
 */
export function ColumnChart({
  data,
  height = 132,
  formatValue = (v) => String(v),
  emptyLabel = "Sem dados",
}: {
  data: BarDatum[];
  height?: number;
  formatValue?: (v: number) => string;
  emptyLabel?: string;
}) {
  if (data.length === 0) {
    return <p className="py-6 text-center text-body text-ink-4">{emptyLabel}</p>;
  }
  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="flex items-end gap-2" style={{ height }}>
      {data.map((d) => (
        <div key={d.label} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
          <span className="num text-[11px] font-semibold text-ink-2">
            {formatValue(d.value)}
          </span>
          <div className="flex w-full flex-1 items-end">
            <div
              className={cn(
                "w-full rounded-t-sm transition-[height] duration-200",
                barTone[d.tone ?? "accent"],
              )}
              style={{ height: `${Math.max(2, (d.value / max) * 100)}%` }}
              title={`${d.label}: ${formatValue(d.value)}`}
            />
          </div>
          <span className="w-full truncate text-center text-[10.5px] text-ink-4">
            {d.label}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * CalibrationChart — curva previsto x observado do backtest.
 *
 * Diagonal de referencia = calibracao perfeita. Pontos acima da diagonal
 * indicam subconfianca do modelo; abaixo, excesso de confianca. O tamanho
 * do ponto cresce com n (amostra do bin), sem inventar escala.
 */
export function CalibrationChart({
  bins,
  size = 220,
  showBands = false,
}: {
  bins: { predicted: number; empirical: number; n: number; ci_low?: number; ci_high?: number }[];
  size?: number;
  showBands?: boolean;
}) {
  if (bins.length === 0) {
    return <p className="py-6 text-center text-body text-ink-4">Sem bins de calibração</p>;
  }
  const pad = 26;
  const inner = size - pad * 2;
  const maxN = Math.max(...bins.map((b) => b.n), 1);
  const x = (v: number) => pad + v * inner;
  const y = (v: number) => size - pad - v * inner;

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="Curva de calibração: probabilidade prevista versus frequência observada"
      className="max-w-full"
    >
      {/* grade */}
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={x(t)} y1={y(0)} x2={x(t)} y2={y(1)} className="stroke-line" strokeWidth="1" />
          <line x1={x(0)} y1={y(t)} x2={x(1)} y2={y(t)} className="stroke-line" strokeWidth="1" />
        </g>
      ))}

      {/* diagonal de calibracao perfeita */}
      <line
        x1={x(0)}
        y1={y(0)}
        x2={x(1)}
        y2={y(1)}
        className="stroke-ink-4"
        strokeWidth="1"
        strokeDasharray="3 3"
      />

      {/* intervalo de confianca (Wilson) de cada faixa */}
      {showBands
        ? bins.map((b, i) =>
            b.ci_low === undefined || b.ci_high === undefined ? null : (
              <line
                key={`ci-${i}`}
                x1={x(b.predicted)}
                y1={y(b.ci_low)}
                x2={x(b.predicted)}
                y2={y(b.ci_high)}
                className="stroke-info-400/40"
                strokeWidth="1.5"
              />
            ),
          )
        : null}

      {/* pontos */}
      {bins.map((b, i) => (
        <circle
          key={i}
          cx={x(b.predicted)}
          cy={y(b.empirical)}
          r={2.5 + (b.n / maxN) * 4}
          className="fill-accent-400/75 stroke-accent-300"
          strokeWidth="1"
        >
          <title>{`previsto ${(b.predicted * 100).toFixed(0)}% → real ${(b.empirical * 100).toFixed(0)}% (n=${b.n})`}</title>
        </circle>
      ))}

      {/* eixos */}
      <text x={size / 2} y={size - 6} textAnchor="middle" className="fill-ink-4 text-[10px]">
        probabilidade prevista
      </text>
      <text
        x={10}
        y={size / 2}
        textAnchor="middle"
        transform={`rotate(-90 10 ${size / 2})`}
        className="fill-ink-4 text-[10px]"
      >
        frequência real
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------ LineChart */

export interface LineSeries {
  name: string;
  values: number[];
  tone?: "accent" | "positive" | "info" | "negative" | "neutral";
  dashed?: boolean;
}

const lineStroke: Record<NonNullable<LineSeries["tone"]>, string> = {
  accent: "stroke-accent-400",
  positive: "stroke-pos-400",
  info: "stroke-info-400",
  negative: "stroke-neg-400",
  neutral: "stroke-ink-3",
};

/**
 * LineChart — series temporais em SVG.
 *
 * Usado na curva de capital e no drawdown. A escala e linear sobre o
 * dominio real dos dados; `referenceValue` desenha uma linha de base
 * (ex.: banca inicial) para leitura imediata de ganho/perda.
 *
 * Sem biblioteca de charts: sao duas series, e um SVG deterministico
 * evita ~500 kB de dependencia e recriacao de instancia a cada render.
 */
export function LineChart({
  series,
  labels,
  height = 200,
  formatValue = (v) => String(v),
  referenceValue,
  className,
}: {
  series: LineSeries[];
  labels: string[];
  height?: number;
  formatValue?: (v: number) => string;
  referenceValue?: number;
  className?: string;
}) {
  const nonEmpty = series.filter((s) => s.values.length > 0);
  if (nonEmpty.length === 0 || labels.length === 0) {
    return <p className="py-6 text-center text-body text-ink-4">Sem dados</p>;
  }

  const padX = 46;
  const padY = 14;
  const width = 640;
  const innerW = width - padX - 12;
  const innerH = height - padY * 2 - 16;

  const all = nonEmpty.flatMap((s) => s.values);
  const candidates = referenceValue !== undefined ? [...all, referenceValue] : all;
  let min = Math.min(...candidates);
  let max = Math.max(...candidates);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;

  const n = Math.max(...nonEmpty.map((s) => s.values.length));
  const x = (i: number) => padX + (n <= 1 ? 0 : (i / (n - 1)) * innerW);
  const y = (v: number) => padY + innerH - ((v - min) / (max - min)) * innerH;

  const yTicks = [max, (max + min) / 2, min];

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Série temporal"
      className={cn("w-full", className)}
      style={{ height }}
    >
      {yTicks.map((t, i) => (
        <g key={i}>
          <line
            x1={padX}
            y1={y(t)}
            x2={width - 12}
            y2={y(t)}
            className="stroke-line"
            strokeWidth="1"
          />
          <text x={padX - 6} y={y(t) + 3} textAnchor="end" className="fill-ink-4 text-[9px]">
            {formatValue(t)}
          </text>
        </g>
      ))}

      {referenceValue !== undefined ? (
        <line
          x1={padX}
          y1={y(referenceValue)}
          x2={width - 12}
          y2={y(referenceValue)}
          className="stroke-ink-3"
          strokeWidth="1"
          strokeDasharray="4 4"
        />
      ) : null}

      {nonEmpty.map((s) => {
        const points = s.values
          .map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`)
          .join(" ");
        return (
          <polyline
            key={s.name}
            points={points}
            fill="none"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={s.dashed ? "4 3" : undefined}
            className={lineStroke[s.tone ?? "accent"]}
          >
            <title>{s.name}</title>
          </polyline>
        );
      })}

      {/* marcadores nas pontas do eixo X */}
      {labels.length > 0 ? (
        <>
          <text x={padX} y={height - 3} textAnchor="start" className="fill-ink-4 text-[9px]">
            {labels[0]}
          </text>
          <text
            x={width - 12}
            y={height - 3}
            textAnchor="end"
            className="fill-ink-4 text-[9px]"
          >
            {labels[labels.length - 1]}
          </text>
        </>
      ) : null}
    </svg>
  );
}

/** Legenda de series do LineChart. */
export function ChartLegend({ series }: { series: LineSeries[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {series.map((s) => (
        <li key={s.name} className="flex items-center gap-1.5 text-[11.5px] text-ink-3">
          <span
            aria-hidden
            className={cn(
              "h-[2px] w-4 rounded-full",
              lineStroke[s.tone ?? "accent"].replace("stroke-", "bg-"),
            )}
          />
          {s.name}
        </li>
      ))}
    </ul>
  );
}
