/**
 * SignalsPage — tela SINAIS.
 *
 * Duas fontes, explicitamente separadas:
 *   real      -> jogos FUTUROS REAIS com odds REAIS (football-data.co.uk)
 *   synthetic -> dataset local gerado em memoria (datas fixas, odds do modelo)
 *
 * O padrao e `real`. O modo sintetico continua disponivel para comparar,
 * e a tela avisa em ambos os casos o que esperar de cada um.
 *
 * Todos os dados vem de GET /api/signals. Nenhum calculo aqui.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchSignals, fetchSignalsStatus } from "@/api/signals";
import SignalsKpiStrip from "@/components/signals/SignalsKpiStrip";
import SignalsSourceBanner from "@/components/signals/SignalsSourceBanner";
import SignalsTable from "@/components/signals/SignalsTable";
import TopSignalCards from "@/components/signals/TopSignalCards";
import Card from "@/components/ui/Card";
import { SearchInput } from "@/components/ui/Input";
import SegmentedControl, { type Segment } from "@/components/ui/SegmentedControl";
import { ErrorPanel, KpiSkeleton, TableSkeleton } from "@/components/ui/States";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import type {
  ConfidenceFilter,
  SignalSource,
  SignalsSourceStatus,
} from "@/types/api";
import { fmtDateTime, fmtInt } from "@/utils/format";
import { textFilter } from "@/utils/table";

export default function SignalsPage() {
  const { dataVersion, config } = useStore();
  const [source, setSource] = useState<SignalSource>("real");
  const [query, setQuery] = useState("");
  const [confidence, setConfidence] = useState<ConfidenceFilter>("all");
  const [status, setStatus] = useState<SignalsSourceStatus | null>(null);

  // descobre qual fonte esta disponivel e adota o padrao do backend
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    fetchSignalsStatus(controller.signal)
      .then((s) => {
        if (!active) return;
        setStatus(s);
      })
      .catch(() => {
        /* status e opcional: sem ele o seletor fica so no sintetico */
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [dataVersion]);

  const { data, initialLoading, loading, error, reload } = useApiResource(
    (signal) =>
      fetchSignals(
        source,
        { minEv: config.min_ev, bankroll: config.bankroll, useXg: config.use_xg },
        signal,
      ),
    [dataVersion, source, config.min_ev, config.bankroll, config.use_xg],
  );

  const all = useMemo(() => data?.signals ?? [], [data]);

  const rows = useMemo(() => {
    const byConfidence =
      confidence === "all" ? all : all.filter((s) => s.confidence === confidence);
    return textFilter(byConfidence, query, (s) => [
      s.match,
      s.market,
      s.outcome,
      s.best_book,
      s.confidence,
      s.kickoff,
    ]);
  }, [all, confidence, query]);

  const segments = useMemo<Segment<ConfidenceFilter>[]>(
    () => [
      { value: "all", label: "Todos", count: all.length },
      {
        value: "FORTE",
        label: "Forte",
        dotClass: "bg-accent-400",
        count: data?.kpis.strong ?? 0,
      },
      {
        value: "MEDIA",
        label: "Média",
        dotClass: "bg-info-400",
        count: data?.kpis.medium ?? 0,
      },
      {
        value: "FRACA",
        label: "Fraca",
        dotClass: "bg-ink-3",
        count: data?.kpis.weak ?? 0,
      },
    ],
    [all.length, data?.kpis.strong, data?.kpis.medium, data?.kpis.weak],
  );

  const clearFilters = useCallback(() => {
    setQuery("");
    setConfidence("all");
  }, []);

  const hasFilters = query.trim() !== "" || confidence !== "all";

  // Diagnóstico honesto do estado vazio: distingue "não há jogos futuros
  // suficientes" de "nenhum sinal passou o EV mínimo". A mensagem não pode
  // culpar o EV quando o bloqueio real é falta de consenso de casas ou de
  // jogos futuros no arquivo de fixtures.
  const emptyHint = useMemo(() => {
    if (source !== "real" || !data) return undefined;
    const books = data.skipped_insufficient_books ?? 0;
    const noRating = data.skipped_no_rating ?? 0;
    const total = (data.kpis.total ?? 0) + books + noRating;
    if (total === 0) {
      return "Não há jogos futuros com odds no momento. A fonte é atualizada pelo site às sextas e terças — tente novamente depois.";
    }
    if (books > 0 && data.kpis.total === 0) {
      return `${books} jogo(s) futuro(s) têm odds, mas de apenas 2 casas — abaixo do mínimo de 3 exigido para consenso. O sistema não inventa sinal com consenso fraco. Volte quando houver jogos das ligas principais.`;
    }
    return undefined;
  }, [source, data]);

  const banner = (
    <SignalsSourceBanner
      source={source}
      status={status}
      detail={data?.source_detail ?? ""}
      calibration={data?.calibration ?? null}
      skippedNoRating={data?.skipped_no_rating ?? 0}
      decision={data?.decision ?? null}
      onChange={setSource}
    />
  );

  if (initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        {banner}
        <KpiSkeleton />
        <TableSkeleton rows={12} cols={10} />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="flex flex-col gap-3">
        {banner}
        <ErrorPanel message={error} onRetry={reload} />
      </div>
    );
  }

  if (!data) return null;

  const topRows = rows.slice(0, 5);

  return (
    <div className="flex flex-col gap-3">
      {banner}

      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <SignalsKpiStrip
        kpis={data.kpis}
        rows={rows}
        sampleLabel={`${fmtInt(rows.length)} de ${fmtInt(all.length)} sinais na lista`}
      />

      <Card
        title="Sinais ranqueados por valor esperado"
        hint={`Gerado em ${fmtDateTime(data.generated_at)} · banca ${fmtInt(data.bankroll)}${
          loading ? " · atualizando…" : ""
        }`}
        padded={false}
        action={
          <div className="flex items-center gap-2">
            <SearchInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onClear={() => setQuery("")}
              placeholder="Buscar jogo, mercado, aposta ou fonte…"
              aria-label="Buscar sinais"
              className="w-[300px]"
            />
            <SegmentedControl
              ariaLabel="Filtrar por confiança"
              segments={segments}
              value={confidence}
              onChange={setConfidence}
            />
          </div>
        }
      >
        <div className="p-3">
          <SignalsTable
            rows={rows}
            maxEv={data.kpis.max_ev}
            hasFilters={hasFilters}
            onClearFilters={clearFilters}
            emptyHint={emptyHint}
          />
        </div>
      </Card>

      {topRows.length > 0 ? (
        <Card
          title="Destaques do modelo"
          hint="Maior valor esperado dentro do filtro atual · apresentação analítica, sem execução"
        >
          <TopSignalCards rows={topRows} />
        </Card>
      ) : null}
    </div>
  );
}
