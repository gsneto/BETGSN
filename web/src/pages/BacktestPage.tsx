/**
 * BacktestPage — validacao historica do modelo.
 *
 * Fluxo:
 *   1. carrega as opcoes (periodo, competicoes, mercados, defaults);
 *   2. configura e dispara o backtest (processamento no backend);
 *   3. acompanha o progresso real;
 *   4. mostra calibracao, EV, temporal, drawdown, segmentos e auditoria.
 *
 * Nenhum calculo estatistico acontece aqui: o React so formata o que o
 * motor produziu.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { fetchBacktestOptions, fetchBacktestRun } from "@/api/backtest";
import BacktestConfigPanel from "@/components/backtest/BacktestConfigPanel";
import BacktestKpis from "@/components/backtest/BacktestKpis";
import BacktestRunsPanel from "@/components/backtest/BacktestRunsPanel";
import BacktestSignalsTable from "@/components/backtest/BacktestSignalsTable";
import CalibrationSection from "@/components/backtest/CalibrationSection";
import EvAnalysisSection from "@/components/backtest/EvAnalysisSection";
import { BacktestProgress, BaselineWarning, MissingDataNotice } from "@/components/backtest/BacktestMeta";
import SegmentsTable from "@/components/backtest/SegmentsTable";
import TemporalSection from "@/components/backtest/TemporalSection";
import Card from "@/components/ui/Card";
import { EmptyState, ErrorPanel, KpiSkeleton, Skeleton } from "@/components/ui/States";
import { useBacktestJob } from "@/hooks/useBacktestJob";
import type {
  BacktestOptions,
  BacktestRequest,
  BacktestRunDetail,
} from "@/types/backtest";
import { fmtDateTime, fmtInt } from "@/utils/format";

export default function BacktestPage() {
  const [options, setOptions] = useState<BacktestOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [detail, setDetail] = useState<BacktestRunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [runsRefresh, setRunsRefresh] = useState(0);

  const loadDetail = useCallback(async (runId: string) => {
    setDetailLoading(true);
    try {
      setDetail(await fetchBacktestRun(runId));
      setDetailError(null);
    } catch (err) {
      setDetailError(
        err instanceof ApiError ? err.userMessage : "Falha ao carregar o resultado.",
      );
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const job = useBacktestJob((runId) => {
    setRunsRefresh((k) => k + 1);
    void loadDetail(runId);
  });

  useEffect(() => {
    let active = true;
    fetchBacktestOptions()
      .then((o) => {
        if (active) {
          setOptions(o);
          setOptionsError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setOptionsError(
            err instanceof ApiError ? err.userMessage : "Falha ao carregar as opções.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const handleRun = useCallback(
    (request: BacktestRequest) => {
      setDetail(null);
      setDetailError(null);
      void job.run(request);
    },
    [job],
  );

  const handleOpenRun = useCallback(
    (runId: string) => {
      void loadDetail(runId);
    },
    [loadDetail],
  );

  if (optionsError && !options) {
    return (
      <ErrorPanel
        message={optionsError}
        onRetry={() => window.location.reload()}
      />
    );
  }

  if (!options) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-[300px] w-full" />
        <KpiSkeleton count={6} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* ------------------------------------------------------- cabecalho */}
      <header className="flex flex-col gap-1">
        <h1 className="text-page font-semibold tracking-[-0.01em] text-ink">
          Backtest
        </h1>
        <p className="text-body text-ink-3">
          Validação histórica do modelo utilizando apenas informações disponíveis
          antes de cada partida.
        </p>
      </header>

      <BacktestConfigPanel
        options={options}
        running={job.running}
        onRun={handleRun}
      />

      {job.status.phase !== "idle" || job.error ? (
        <BacktestProgress status={job.status} />
      ) : null}

      {job.error && job.status.phase !== "error" ? (
        <ErrorPanel message={job.error} onRetry={() => job.reset()} />
      ) : null}

      {/* -------------------------------------------------------- resultado */}
      {detailLoading && !detail ? (
        <div className="flex flex-col gap-3">
          <KpiSkeleton count={6} />
          <Skeleton className="h-[320px] w-full" />
        </div>
      ) : detailError ? (
        <ErrorPanel message={detailError} />
      ) : detail ? (
        <>
          <Card
            title={`Execução ${detail.run_id}`}
            hint={`${fmtDateTime(detail.created_at)} · modelo v${detail.model_version} · hash ${detail.config_hash} · schema ${detail.point_in_time_schema} · ${fmtInt(detail.meta.n_matches_in_period ?? 0)} partidas no período`}
          >
            <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-[11.5px] text-ink-3">
              <span>
                <span className="label-caps me-1.5">Ponto de corte</span>
                cada sinal usou somente partidas com kickoff estritamente anterior
              </span>
              <span>
                <span className="label-caps me-1.5">Mercados</span>
                {detail.config.market_keys.join(", ")}
              </span>
              <span>
                <span className="label-caps me-1.5">EV mín.</span>
                {(detail.config.min_ev * 100).toFixed(1)}%
              </span>
              <span>
                <span className="label-caps me-1.5">xG</span>
                {detail.config.use_xg ? "sim" : "não"}
              </span>
              <span>
                <span className="label-caps me-1.5">Banca</span>
                {fmtInt(detail.config.bankroll)}
              </span>
            </div>
          </Card>

          <BaselineWarning
            oddsSource={detail.config.odds_source}
            avgModelProb={detail.aggregate.avg_model_prob}
            avgMarketProb={detail.aggregate.avg_market_prob}
          />

          <BacktestKpis
            aggregate={detail.aggregate}
            simulation={detail.simulation}
            minSample={options.min_sample}
          />

          <CalibrationSection
            bins={detail.calibration}
            aggregate={detail.aggregate}
          />

          <EvAnalysisSection buckets={detail.ev_buckets} />

          <TemporalSection
            simulation={detail.simulation}
            temporal={detail.temporal}
          />

          <SegmentsTable rows={detail.segments} />

          <BacktestSignalsTable runId={detail.run_id} />

          <MissingDataNotice notes={detail.missing_data_notes} />
        </>
      ) : (
        <Card title="Resultado">
          <EmptyState
            title="Nenhum backtest executado ainda"
            hint="Configure o período, os mercados e os parâmetros acima e clique em EXECUTAR BACKTEST. O processamento roda inteiro no backend."
          />
        </Card>
      )}

      {/* --------------------------------------------------------- auditoria */}
      <BacktestRunsPanel
        activeRunId={detail?.run_id ?? job.status.run_id}
        onOpenRun={handleOpenRun}
        refreshKey={runsRefresh}
      />
    </div>
  );
}
