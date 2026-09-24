/**
 * QuantPage — observabilidade quantitativa: MARKET vs MODEL, auditoria
 * do line-shopping, ciclo de vida do CLV e estado da referência.
 *
 * Prioridade é INFORMAÇÃO e RASTREABILIDADE, não estética: cada número
 * vem do backend com sua origem declarada; cache ausente/stale aparece
 * como estado explícito, nunca como número de outra medição. Nada aqui
 * decide ou promove — o NO BET vive no promotion gate.
 */

import { fetchClv } from "@/api/clv";
import {
  fetchQuantBenchmarks,
  fetchQuantClvStatus,
  fetchQuantLineShopping,
  fetchQuantMl,
  fetchQuantModelVsMarket,
} from "@/api/quant";
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { KpiCard } from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import { fmtDateTime, fmtInt, fmtPct } from "@/utils/format";

function fmtDelta(value: number | null | undefined): string {
  if (value == null) return "—";
  return fmtPct(value);
}

function fmtNum(value: number | null | undefined, digits = 4): string {
  if (value == null) return "—";
  return value.toFixed(digits);
}

function cacheTone(status: string): "accent" | "warning" | "neutral" {
  if (status === "VALID") return "accent";
  if (status === "STALE") return "warning";
  return "neutral";
}

export default function QuantPage() {
  const { dataVersion } = useStore();
  const benchmarks = useApiResource((s) => fetchQuantBenchmarks(s), [dataVersion]);
  const modelMarket = useApiResource((s) => fetchQuantModelVsMarket(s), [dataVersion]);
  const lineShopping = useApiResource((s) => fetchQuantLineShopping(s), [dataVersion]);
  const ml = useApiResource((s) => fetchQuantMl(s), [dataVersion]);
  const clvStatus = useApiResource((s) => fetchQuantClvStatus(s), [dataVersion]);
  const clv = useApiResource((s) => fetchClv(s), [dataVersion]);

  return (
    <div className="flex flex-col gap-3">
      {/* ------------------------- referência / benchmark ------------------------- */}
      <Card
        title="Referência de benchmark (Etapa 19)"
        hint={
          benchmarks.data
            ? `corpus ${benchmarks.data.corpus.signature} · train ${benchmarks.data.protocol.train_days}d / test ${benchmarks.data.protocol.test_days}d / embargo ${benchmarks.data.protocol.gap_days_embargo}d · seed ${benchmarks.data.protocol.bootstrap_seed}`
            : "manifest da referência oficial"
        }
      >
        {benchmarks.data ? (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={cacheTone(benchmarks.data.reproducibility.all_valid ? "VALID" : "STALE")} size="sm">
                {benchmarks.data.reproducibility.all_valid ? "reproduzível" : "reprodutibilidade rupturada"}
              </Badge>
              <span className="text-xs text-ink-3">
                código {benchmarks.data.code_version}
              </span>
            </div>
            <ul className="flex flex-col gap-1">
              {Object.entries(benchmarks.data.caches).map(([name, entry]) => (
                <li key={name} className="flex items-center justify-between gap-2 text-xs">
                  <span className="mono text-ink-2">{name}</span>
                  <Badge tone={cacheTone(entry.status)} size="sm">
                    {entry.status}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-xs text-ink-3">{benchmarks.error ?? "carregando…"}</p>
        )}
      </Card>

      {/* ------------------------------ model vs market ------------------------------ */}
      <Card
        title="Modelo vs Mercado — 24 janelas OOS"
        hint="mesma população de linhas; fontes separadas (market_raw/market_fair/model)"
      >
        {modelMarket.data?.status === "OK" ? (
          <ul className="flex flex-col gap-1 text-xs">
            {(
              [
                ["MARKET RAW", modelMarket.data.market_raw],
                ["MARKET FAIR", modelMarket.data.market_fair],
                ["MODEL RAW", modelMarket.data.model_raw],
                ["MODEL CALIBRATED", modelMarket.data.model_calibrated],
              ] as const
            ).map(([label, m]) =>
              m ? (
                <li key={label} className="flex items-center justify-between gap-2">
                  <span className="label-caps text-ink-3">{label}</span>
                  <span className="mono text-ink-2">
                    Brier {fmtNum(m.brier)} · LogLoss {fmtNum(m.logloss)} · ECE {fmtNum(m.ece)} · n {fmtInt(m.n)}
                  </span>
                </li>
              ) : null,
            )}
            <li className="flex items-center justify-between gap-2">
              <span className="label-caps text-ink-3">PAREADO vs MARKET RAW</span>
              <span className="mono text-ink-2">
                {modelMarket.data.paired_model_vs_raw?.verdict ?? "n/d"}
              </span>
            </li>
          </ul>
        ) : (
          <p className="text-xs text-ink-3">
            {modelMarket.data?.detail ?? modelMarket.error ?? "carregando…"}
          </p>
        )}
      </Card>

      {/* ------------------------------ line-shopping ------------------------------ */}
      <Card
        title="Auditoria do line-shopping"
        hint="população constante: mesmas apostas, preço variando — efeito PREÇO isolado de SELEÇÃO"
      >
        {lineShopping.data?.status === "OK" ? (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
              {Object.entries(lineShopping.data.rule_population_by_price ?? {}).map(
                ([price, stats]) => (
                  <KpiCard
                    key={price}
                    label={`ROI preço ${price}`}
                    value={fmtDelta(stats.roi)}
                    context={`n ${fmtInt(stats.n)}`}
                  />
                ),
              )}
            </div>
            {lineShopping.data.strategy_model_decomposition ? (
              <p className="text-xs text-ink-3">
                strategy_model (EV&gt;0): {fmtInt(lineShopping.data.strategy_model_decomposition.n_rows ?? 0)} linhas ·
                melhor preço {fmtDelta(lineShopping.data.strategy_model_decomposition.roi_best)} vs mediana{" "}
                {fmtDelta(lineShopping.data.strategy_model_decomposition.roi_median)} — delta puro de preço{" "}
                <span className="mono">{fmtDelta(lineShopping.data.strategy_model_decomposition.delta_price_effect)}</span>
              </p>
            ) : null}
            <p className="text-[11px] text-ink-4">
              {lineShopping.data.limitations?.[0]}
            </p>
          </div>
        ) : (
          <p className="text-xs text-ink-3">
            {lineShopping.data?.detail ?? lineShopping.error ?? "carregando…"}
          </p>
        )}
      </Card>

      {/* ------------------------------ modelos ML ------------------------------ */}
      <Card
        title="Modelos experimentais — mesmas 24 janelas"
        hint="evidência comparativa: sem ranking, sem promoção"
      >
        {ml.data?.status === "OK" ? (
          <ul className="flex flex-col gap-1 text-xs">
            {Object.entries(ml.data.models ?? {}).map(([name, m]) => (
              <li key={name} className="flex items-center justify-between gap-2">
                <span className="label-caps text-ink-3">{name}</span>
                <span className="mono text-ink-2">
                  LogLoss {fmtNum(m.model_raw?.logloss)} vs mercado {fmtNum(m.market_raw?.logloss)} ·
                  delta {fmtNum(m.delta_logloss_vs_market_raw)} · n {fmtInt(m.n_bets_oos ?? 0)}
                </span>
              </li>
            ))}
            {ml.data.ensemble ? (
              <li className="flex flex-col gap-1 border-t border-surface-2 pt-2">
                {ml.data.ensemble.status === "OK" ? (
                  <>
                    <span className="flex items-center justify-between gap-2">
                      <span className="label-caps text-ink-3">ensemble</span>
                      <span className="mono text-ink-2">
                        LogLoss {fmtNum(ml.data.ensemble.model_raw?.logloss)} vs mercado{" "}
                        {fmtNum(ml.data.ensemble.market_raw?.logloss)} · delta{" "}
                        {fmtNum(ml.data.ensemble.delta_logloss_vs_market_raw)} · n{" "}
                        {fmtInt(ml.data.ensemble.n_bets_oos ?? 0)}
                      </span>
                    </span>
                    <span className="text-[11px] text-ink-4">
                      Brier {fmtNum(ml.data.ensemble.model_raw?.brier)} · ECE {fmtNum(ml.data.ensemble.model_raw?.ece)} ·
                      MARKET FAIR LogLoss {fmtNum(ml.data.ensemble.market_fair?.logloss)} ·
                      delta {fmtNum(ml.data.ensemble.delta_logloss_vs_market_fair)}
                    </span>
                    <span className="text-[11px] text-ink-4">
                      {ml.data.ensemble.stacking_protocol
                        ? `stacking OOS por janela: ${ml.data.ensemble.stacking_protocol.base_models.join(" + ")} · ${ml.data.ensemble.stacking_protocol.n_folds} folds rolling-origin no TRAIN`
                        : "stacking OOS por janela"}
                    </span>
                  </>
                ) : (
                  <span className="text-ink-4">
                    Ensemble: {ml.data.ensemble.status} — {ml.data.ensemble.reason}
                  </span>
                )}
              </li>
            ) : null}
          </ul>
        ) : (
          <p className="text-xs text-ink-3">{ml.data?.detail ?? ml.error ?? "carregando…"}</p>
        )}
      </Card>

      {/* ------------------------------ CLV lifecycle ------------------------------ */}
      <Card
        title="CLV prospectivo — ciclo de vida"
        hint={clvStatus.data?.lifecycle_note ?? "PENDING/NO_CLOSE/CLOSED/INVALID/MISMATCH"}
      >
        {clvStatus.data ? (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-2 xl:grid-cols-5">
              {(["PENDING", "NO_CLOSE", "CLOSED", "INVALID", "MISMATCH"] as const).map(
                (state) => (
                  <KpiCard
                    key={state}
                    label={state}
                    value={String(clvStatus.data?.lifecycle?.[state] ?? 0)}
                  />
                ),
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 xl:grid-cols-5">
              <KpiCard
                label="CLV válido n"
                value={String(clvStatus.data.clv_statistics?.n ?? 0)}
                context="apenas CLOSED entra na amostra"
              />
              <KpiCard
                label="CLV médio"
                value={clvStatus.data.clv_statistics?.mean != null ? fmtPct(clvStatus.data.clv_statistics.mean) : "—"}
                context={
                  clvStatus.data.clv_statistics?.median != null
                    ? `mediana ${fmtPct(clvStatus.data.clv_statistics.median)}`
                    : "sem amostra"
                }
              />
              <KpiCard
                label="Taxa de fechamento"
                value={clvStatus.data.close_rate != null ? fmtPct(clvStatus.data.close_rate) : "não medido"}
                context="CLOSED / (CLOSED + NO_CLOSE)"
              />
              <KpiCard
                label="Última captura"
                value={clvStatus.data.capture?.last_observation_timestamp ? fmtDateTime(clvStatus.data.capture.last_observation_timestamp) : "—"}
                context={`${fmtInt(clvStatus.data.capture?.n_observations ?? 0)} observações`}
              />
              <KpiCard
                label="Último fechamento"
                value={clvStatus.data.clv_statistics?.last_closing_timestamp ? fmtDateTime(clvStatus.data.clv_statistics.last_closing_timestamp) : "—"}
                context={clvStatus.data.provider_issues?.length ? "providers com problema" : "identidade casada"}
              />
            </div>
            {clvStatus.data.provider_issues?.length ? (
              <div className="flex flex-wrap items-center gap-2">
                {clvStatus.data.provider_issues.map((name) => (
                  <Badge key={name} tone="warning" size="sm">
                    {`${name}: ${clvStatus.data?.provider_health?.[name]?.state ?? "?"}`}
                  </Badge>
                ))}
              </div>
            ) : null}
            <p className="text-xs text-ink-3">{clvStatus.data.promotion_gate_note}</p>
            <p className="text-[11px] text-ink-4">
              CLV prospectivo n={clvStatus.data.clv_prospective.n} · coverage das entradas registradas{" "}
              {clv.data?.coverage != null ? fmtPct(clv.data.coverage) : "não medido"}
            </p>
          </div>
        ) : (
          <p className="text-xs text-ink-3">{clvStatus.error ?? "carregando…"}</p>
        )}
      </Card>
    </div>
  );
}
