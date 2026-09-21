/**
 * SignalsKpiStrip — KPI cards da tela SINAIS.
 *
 * Todos os numeros vem de `SignalsKpis`, calculado no backend. O React
 * apenas formata. A sparkline do lucro esperado usa a soma cumulativa
 * dos lucros esperados JA calculados por sinal — e uma visualizacao dos
 * dados recebidos, nao um calculo novo.
 */

import { useMemo } from "react";
import KpiCard from "@/components/ui/KpiCard";
import { DatabaseIcon, LayersIcon, TargetIcon, TrendingUpIcon } from "@/components/ui/icons";
import type { Signal, SignalsKpis } from "@/types/api";
import { fmtInt, fmtMoneySigned, fmtPct, fmtPctSigned } from "@/utils/format";

interface Props {
  kpis: SignalsKpis;
  /** sinais filtrados na tela, para a sparkline acompanhar o filtro */
  rows: Signal[];
  sampleLabel: string;
}

export default function SignalsKpiStrip({ kpis, rows, sampleLabel }: Props) {
  const cumulative = useMemo(() => {
    let acc = 0;
    const out = rows.map((s) => (acc += s.expected_profit));
    return out.length > 1 ? out : [0, 0];
  }, [rows]);

  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label="Sinais fortes"
        value={fmtInt(kpis.strong)}
        context={`${fmtPct(kpis.strong_pct, 0)} da lista`}
        tone="accent"
        icon={<TargetIcon className="size-4" />}
        tooltip={`FORTE exige EV ≥ 8%. Total de ${fmtInt(kpis.total)} sinais acima do EV mínimo.`}
      />
      <KpiCard
        label="Sinais médios"
        value={fmtInt(kpis.medium)}
        context={`${fmtPct(kpis.medium_pct, 0)} da lista`}
        tone="info"
        icon={<LayersIcon className="size-4" />}
        tooltip={`MÉDIA exige EV ≥ 4,5%. FRACA (≥ 2%): ${fmtInt(kpis.weak)} sinais.`}
      />
      <KpiCard
        label="Lucro esperado"
        value={fmtMoneySigned(kpis.expected_profit)}
        context={`${fmtPctSigned(kpis.expected_profit_pct, 2)} da banca`}
        tone={kpis.expected_profit > 0 ? "positive" : kpis.expected_profit < 0 ? "negative" : "default"}
        spark={cumulative}
        icon={<TrendingUpIcon className="size-4" />}
        tooltip="Soma de stake × EV de todos os sinais. Retorno médio teórico, não lucro garantido."
      />
      <KpiCard
        label="Exposição"
        value={fmtPct(kpis.total_exposure_pct)}
        context={sampleLabel}
        delta={{
          text: fmtMoneySigned(-kpis.worst_case_loss),
          tone: "negative",
        }}
        icon={<DatabaseIcon className="size-4" />}
        tooltip={`Soma das stakes: ${fmtInt(kpis.total)} sinais. O delta é a perda máxima se todas perderem.${
          kpis.exposure_scaled_by < 0.999
            ? ` Stakes escaladas ×${kpis.exposure_scaled_by.toFixed(3)} pelo teto de exposição.`
            : ""
        }`}
      />
    </div>
  );
}
