/**
 * CoveragePage — tela de COVERAGE.
 */

import { fetchCoverage } from "@/api/coverage";
import Card from "@/components/ui/Card";
import {
  EmptyState,
  ErrorPanel,
  KpiCard,
  Skeleton,
} from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import { fmtPct } from "@/utils/format";

/** null = "não medido": ausência de medição, nunca zero fabricado. */
const fmtCoverage = (v: number | null): string =>
  v == null ? "não medido" : fmtPct(v);

/** Rótulo do contexto do card de cobertura. */
const coverageContext = (v: number | null): string =>
  v == null ? "sem população observada" : "sobre fixtures observados";

export default function CoveragePage() {
  const { dataVersion } = useStore();

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchCoverage(signal),
    [dataVersion],
  );

  if (initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-[52px] w-full" />
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="panel h-[104px] p-4">
              <Skeleton className="h-2.5 w-20" />
              <Skeleton className="mt-3 h-7 w-24" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error && !data) {
    return <ErrorPanel message={error} onRetry={reload} />;
  }

  if (!data) return null;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-5">
        <KpiCard
          label="Cobertura Odds"
          value={fmtCoverage(data.odds_coverage)}
          context={coverageContext(data.odds_coverage)}
          tone="accent"
        />
        <KpiCard
          label="Cobertura CLV"
          value={fmtCoverage(data.clv_coverage)}
          context={
            data.clv_coverage == null
              ? "sem linhas apostáveis"
              : "linhas com fechamento válido"
          }
          tone="accent"
        />
        <KpiCard
          label="Cobertura xG"
          value={fmtCoverage(data.xg_coverage)}
          context={
            data.xg_coverage == null
              ? "sem fonte de xG real"
              : "fixtures com xG real"
          }
          tone="accent"
        />
        <KpiCard label="Fixtures com odds" value={`${data.n_fixtures_with_odds}/${data.n_fixtures_total}`} />
        <KpiCard
          label="Bookmakers ativos"
          value={String(data.n_bookmakers_active)}
          context="observados nas odds"
        />
      </div>

      <Card
        title="Bookmakers observados"
        hint="Casas com odds efetivamente vistas nos fixtures — chaves de provider configuradas não contam"
        padded={false}
      >
        <div className="p-3">
          {data.bookmakers_observed.length === 0 ? (
            <EmptyState
              title="Nenhum bookmaker observado"
              hint="Sem odds em cache não há evidência de bookmaker ativo."
            />
          ) : (
            <div className="flex flex-wrap gap-2">
              {data.bookmakers_observed.map((b) => (
                <span
                  key={b}
                  className="label-caps mono rounded border border-line bg-surface-2 px-2 py-1 text-[11.5px]"
                >
                  {b}
                </span>
              ))}
            </div>
          )}
        </div>
      </Card>

      <Card
        title="Cobertura por provider"
        hint="Status, quota e features de cada fonte de dados"
        padded={false}
      >
        <div className="p-3">
          {data.providers.length === 0 ? (
            <EmptyState title="Nenhum provider" hint="Configure chaves para ver cobertura." />
          ) : (
            <div className="flex flex-col gap-2">
              {data.providers.map((p) => (
                <div key={p.name} className="flex items-center gap-3 rounded border border-line bg-surface-2 p-2">
                  <span className="font-medium text-ink w-40">{p.name}</span>
                  <span className="label-caps px-2 py-0.5 rounded text-[11px]">
                    {p.status}
                  </span>
                  <span className="mono text-ink-3 text-[11.5px]">
                    {p.features.length > 0 ? p.features.join(", ") : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {data.gaps.length > 0 ? (
        <Card
          title="Gaps de cobertura"
          hint="Providers sem configuração e métricas sem fonte de medição"
        >
          <div className="p-3">
            <div className="flex flex-col gap-1">
              {data.gaps.map((g, i) => (
                <div key={i} className="flex items-center gap-2 text-[12.5px]">
                  <span className="font-medium text-ink">{g.provider}</span>
                  <span className="text-neg-300">{g.detail}</span>
                </div>
              ))}
            </div>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
