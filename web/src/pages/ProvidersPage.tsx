/**
 * ProvidersPage — tela de PROVIDERS e observabilidade.
 *
 * Mostra o status de cada provider de dados com:
 * status, última atualização, última execução, latência,
 * erro, quota, cobertura.
 */

import Button from "@/components/ui/Button";
import { useStore } from "@/store/context";
import { fetchProviders } from "@/api/providers";
import Badge from "@/components/ui/Badge";
import type { BadgeTone } from "@/components/ui/badgeTone";
import Card from "@/components/ui/Card";
import {
  EmptyState,
  ErrorPanel,
  KpiCard,
  Skeleton,
} from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { cn } from "@/utils/cn";
import { fmtInt, fmtDateTime } from "@/utils/format";
import { RefreshCwIcon } from "@/components/ui/icons";
import type { ProviderAvailability, ProviderHealth } from "@/types/api";

const statusTone: Record<ProviderAvailability, BadgeTone> = {
  HEALTHY: "positive",
  STALE: "warning",
  UNAVAILABLE: "negative",
  NO_COVERAGE: "neutral",
  DEGRADED: "warning",
  UNKNOWN: "neutral",
};

const statusLabel: Record<ProviderAvailability, string> = {
  HEALTHY: "Saudável",
  STALE: "Desatualizado",
  UNAVAILABLE: "Indisponível",
  NO_COVERAGE: "Sem cobertura",
  DEGRADED: "Degradado",
  UNKNOWN: "Sem observação",
};

export default function ProvidersPage() {
  const { dataVersion } = useStore();

  const { data, initialLoading, error, reload, loading } = useApiResource(
    (signal) => fetchProviders(signal),
    [dataVersion],
  );

  if (initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-[52px] w-full" />
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
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

  const nStale = data.providers.filter(
    (p: ProviderHealth) => p.status === "DEGRADED" || p.status === "STALE",
  ).length;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <KpiCard label="Providers" value={String(data.providers.length)} />
        <KpiCard
          label="Saudáveis"
          value={String(data.providers.filter((p: ProviderHealth) => p.status === "HEALTHY").length)}
          tone="positive"
        />
        <KpiCard label="Degradados / desatualizados" value={String(nStale)} tone="default" />
        <KpiCard
          label="Indisponíveis"
          value={String(data.providers.filter((p: ProviderHealth) => p.status === "UNAVAILABLE").length)}
          tone="negative"
        />
      </div>

      <Card
        title="Status dos Providers"
        hint={`Gerado em ${fmtDateTime(data.generated_at)} · health real observado pelo Odds Layer`}
        padded={false}
        action={
          <Button
            variant="ghost"
            size="md"
            loading={loading}
            onClick={reload}
            startIcon={<RefreshCwIcon className="size-4" />}
            aria-label="Atualizar status dos providers"
          >
            Atualizar
          </Button>
        }
      >
        <div className="p-3">
          {data.providers.length === 0 ? (
            <EmptyState title="Nenhum provider configurado" hint="Configure chaves de API para habilitar providers." />
          ) : (
            <div className="flex flex-col gap-2">
              {data.providers.map((p: ProviderHealth) => (
                <ProviderRow key={p.name} provider={p} />
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

function ProviderRow({ provider }: { provider: ProviderHealth }) {
  const tone = statusTone[provider.status] ?? "default";
  return (
    <div
      className={cn(
        "rounded-lg border p-3 flex flex-col gap-2",
        provider.status === "HEALTHY"
          ? "border-pos-700/30 bg-pos-900/5"
          : provider.status === "UNAVAILABLE"
            ? "border-neg-700/30 bg-neg-900/5"
            : provider.status === "DEGRADED" || provider.status === "STALE"
              ? "border-warn-500/30 bg-warn-900/10"
              : "border-line bg-surface-2",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium text-ink">{provider.name}</span>
        <Badge tone={tone} size="sm">{statusLabel[provider.status] ?? provider.status}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11.5px]">
        <span className="text-ink-3">Última atualização:</span>
        <span className="mono text-ink-2">{provider.last_update ? fmtDateTime(provider.last_update) : "—"}</span>
        <span className="text-ink-3">Latência:</span>
        <span className="mono text-ink-2">{provider.latency_ms != null ? `${provider.latency_ms.toFixed(1)} ms` : "—"}</span>
        <span className="text-ink-3">Quota:</span>
        <span className="mono text-ink-2">
          {provider.quota_used != null && provider.quota_remaining != null
            ? `${fmtInt(provider.quota_used)} usados / ${fmtInt(provider.quota_remaining)} restantes`
            : "—"}
        </span>
        <span className="text-ink-3">Features:</span>
        <span className="text-ink-2">{provider.features.join(", ") || "—"}</span>
      </div>
      {provider.error ? (
        <p className="text-[11.5px] text-neg-300">{provider.error}</p>
      ) : null}
      {provider.message ? (
        <p className="text-[11.5px] text-ink-3">{provider.message}</p>
      ) : null}
    </div>
  );
}
