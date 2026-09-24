/**
 * ProviderHealthStrip — saude dos providers do engine em tempo real.
 *
 * Mostra por provider: ticks, falhas, ultimo sucesso/falha e o ultimo
 * erro. O usuario precisa ver "SE UM PROVIDER CAIU, EU SEI QUE CAIU".
 */
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { EmptyState, ErrorPanel, Skeleton } from "@/components/ui";
import { fmtDateTime, fmtInt } from "@/utils/format";
import type { RealtimeProvidersResponse } from "@/types/realtime";

export default function ProviderHealthStrip({
  data,
  loading,
  error,
  onRetry,
}: {
  data: RealtimeProvidersResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (loading) {
    return (
      <Card title="Providers" hint="Saúde da captura em tempo real">
        <div className="p-3">
          <Skeleton className="h-16 w-full" />
        </div>
      </Card>
    );
  }
  if (error && !data) {
    return (
      <Card title="Providers" hint="Saúde da captura em tempo real">
        <div className="p-3">
          <ErrorPanel message={error} onRetry={onRetry} />
        </div>
      </Card>
    );
  }
  if (!data) return null;

  const entries = Object.values(data.providers);
  const loopProviders = entries.length > 0 ? entries : [];
  const storeProviders = Object.entries(data.provider_health_store ?? {});

  return (
    <Card
      title="Providers"
      hint={`${fmtInt(loopProviders.length)} loop(s) · store: ${fmtInt(storeProviders.length)} com health registrado`}
    >
      <div className="p-3">
        {loopProviders.length === 0 && storeProviders.length === 0 ? (
          <EmptyState
            title="Nenhum provider no engine"
            hint="Configure BETGSN_ODDS_API_KEY / PARLAY_API / ODDSPAPI no .env e inicie o engine."
          />
        ) : (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {loopProviders.map((loop) => {
              const store = data.provider_health_store?.[loop.provider] as
                | { state?: string; latency_ms?: number; credits_remaining?: number | null }
                | undefined;
              const healthy = !loop.last_error || loop.last_success_at >= loop.last_failure_at;
              return (
                <div
                  key={loop.provider}
                  className="rounded border border-line bg-surface-2/60 p-2 text-[11px]"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-ink">{loop.provider}</span>
                    <Badge tone={healthy ? "positive" : "negative"} size="sm" dot>
                      {healthy ? "HEALTHY" : "COM ERRO"}
                    </Badge>
                  </div>
                  <dl className="mt-1 grid grid-cols-2 gap-x-2 gap-y-0.5 text-ink-3">
                    <dt className="text-ink-4">Ticks</dt>
                    <dd className="num text-right">{fmtInt(loop.ticks)}</dd>
                    <dt className="text-ink-4">Último sucesso</dt>
                    <dd className="num text-right">{loop.last_success_at ? fmtDateTime(loop.last_success_at).slice(11) : "—"}</dd>
                    <dt className="text-ink-4">Última falha</dt>
                    <dd className="num text-right">{loop.last_failure_at ? fmtDateTime(loop.last_failure_at).slice(11) : "—"}</dd>
                    {store?.state ? (
                      <>
                        <dt className="text-ink-4">Health (store)</dt>
                        <dd className="text-right">{store.state}</dd>
                      </>
                    ) : null}
                    {store?.latency_ms != null ? (
                      <>
                        <dt className="text-ink-4">Latência</dt>
                        <dd className="num text-right">{Math.round(store.latency_ms)} ms</dd>
                      </>
                    ) : null}
                    {store?.credits_remaining != null ? (
                      <>
                        <dt className="text-ink-4">Créditos restantes</dt>
                        <dd className="num text-right">{fmtInt(store.credits_remaining)}</dd>
                      </>
                    ) : null}
                  </dl>
                  {loop.last_error ? (
                    <p className="mt-1 line-clamp-2 text-[10.5px] text-neg-300" title={loop.last_error}>
                      {loop.last_error}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}
