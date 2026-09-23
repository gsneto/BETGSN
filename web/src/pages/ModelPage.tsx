/**
 * ModelPage — tela MODELO.
 *
 * A GUI legada exibia apenas o texto `MODEL_DOC`. Aqui a documentacao
 * continua (mesmo texto, servido pelo backend) e ganha a companhia dos
 * parametros REAIS do modelo e do resultado do backtest — ambos ja
 * existentes em `betgsn.model`/`betgsn.backtest`, sem recalculo.
 *
 * Esta tela e de apresentacao e leitura. Alterar parametros acontece na
 * barra de controles e sempre pelo backend.
 */

import { useEffect, useRef, useState } from "react";
import { fetchModel, fetchModelPerformance } from "@/api/model";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import { CalibrationChart } from "@/components/ui/Charts";
import KpiCard from "@/components/ui/KpiCard";
import {
  ErrorPanel,
  KpiSkeleton,
  Skeleton,
  Tooltip,
} from "@/components/ui";
import { RefreshCwIcon } from "@/components/ui/icons";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import type { ModelPerformance } from "@/types/api";
import { cn } from "@/utils/cn";
import {
  fmtInt,
  fmtNum,
  fmtPct,
  fmtPctSigned,
  fmtSigned,
  signedColorClass,
} from "@/utils/format";

export default function ModelPage() {
  const { dataVersion } = useStore();
  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchModel(signal),
    [dataVersion],
  );

  if (initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <KpiSkeleton />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }
  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />;
  if (!data) return null;

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <KpiCard
          label="Versão do motor"
          value={`v${data.version}`}
          context={data.engine}
          tone="accent"
          tooltip="Versão do pacote BETGSN que produziu este snapshot."
        />
        <KpiCard
          label="Mercados modelados"
          value={fmtInt(data.n_markets)}
          context={`${fmtInt(data.n_teams)} times · ${fmtInt(data.n_fixtures)} jogos`}
          tooltip="Mercados derivados da matriz de placar e das contagens Poisson."
        />
        <KpiCard
          label="Amostra de treino"
          value={fmtInt(data.n_history_matches)}
          context="partidas no histórico"
          tone="info"
          tooltip="Jogos usados no ajuste de ataque/defesa por ponto fixo."
        />
        <KpiCard
          label="Fonte de dados"
          value={data.providers && Object.values(data.providers).some(Boolean) ? "Real" : "Sintética"}
          context={data.data_source}
          tone={Object.values(data.providers ?? {}).some(Boolean) ? "positive" : "default"}
          tooltip="Sem chaves de API o app roda com dataset local de seed fixo, para testar o pipeline."
        />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card title="Constantes do modelo" hint="Valores fixos do motor estatístico">
          <ParamList
            items={[
              ["ρ Dixon-Coles", fmtNum(data.constants.rho_dixon_coles, 3),
               "Correção para placares baixos (0-0, 1-0, 0-1, 1-1)."],
              ["Grade máxima de gols", `${data.constants.max_goals_grid}`,
               "Matriz (max+1)² de placares antes da normalização."],
              ["Média de gols da liga", fmtNum(data.constants.league_avg_goals),
               "Constante base; o pipeline usa a média do histórico."],
              ["Vantagem de casa", fmtNum(data.constants.home_advantage),
               "Fator multiplicativo do mandante no λ."],
              ["Blend de xG", fmtPct(data.attack_blend, 0),
               "Peso do xG sobre os gols puros no ataque (0 = só gols)."],
              ["Mín. de casas no consenso", `${data.constants.min_books}`,
               "Abaixo disso o sinal é descartado."],
              ["Spread máx. de odds", fmtPct(data.constants.max_spread, 0),
               "Dispersão tolerada entre casas."],
              ["Seed do dataset", `${data.constants.dataset_seed}`,
               "Seed fixa: o dataset local é reprodutível."],
            ]}
          />
        </Card>

        <Card title="Limiares e configuração ativa" hint="Como o sinal é classificado e parametrizado">
          <ParamList
            items={[
              ["FORTE", `EV ≥ ${fmtPct(data.constants.ev_forte, 1)}`, "Sinal de maior confiança."],
              ["MÉDIA", `EV ≥ ${fmtPct(data.constants.ev_media, 1)}`, "Confiança intermediária."],
              ["FRACA", `EV ≥ ${fmtPct(data.constants.ev_fraca, 1)}`, "Limite mínimo para entrar na lista."],
              ["Banca", fmtNum(data.configuration.bankroll), "Base do cálculo de stake."],
              ["Kelly fracionado", fmtNum(data.configuration.kelly_fraction),
               "Fração de Kelly aplicada."],
              ["EV mínimo ativo", fmtPct(data.configuration.min_ev, 1),
               "Filtro atual da listagem de sinais."],
              ["Risco por aposta", fmtPct(data.configuration.stake_cap, 2),
               "Teto por aposta."],
              ["Exposição máxima", fmtPct(data.configuration.max_exposure, 0),
               "Teto da soma das stakes."],
            ]}
          />
        </Card>
      </div>

      <Card title="Fontes de dados" hint="Chaves de API detectadas no ambiente do backend">
        <ul className="grid grid-cols-1 gap-2 md:grid-cols-3">
          {Object.entries(data.providers).map(([name, ok]) => (
            <li
              key={name}
              className="flex items-center justify-between rounded-md border border-line bg-surface-2 px-3 py-2"
            >
              <span className="text-body text-ink-2">{name}</span>
              <Badge tone={ok ? "positive" : "neutral"} dot size="sm">
                {ok ? "configurada" : "sem chave"}
              </Badge>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11.5px] text-ink-4">
          As chaves ficam apenas no ambiente do backend. Nada é exposto ao bundle do navegador.
        </p>
      </Card>

      <Card
        title="Mercados cobertos"
        hint="Rótulos gerados por betgsn/markets.py"
      >
        <ul className="flex flex-wrap gap-2">
          {data.markets.map((m) => (
            <li key={m}>
              <Badge tone="neutral">{m}</Badge>
            </li>
          ))}
        </ul>
      </Card>

      <BacktestSection />

      <Card
        title="Documentação do modelo"
        hint="Texto servido pelo backend a partir de betgsn/gui.py"
      >
        <pre className="thin-scrollbar max-h-[520px] overflow-auto rounded-md border border-line bg-surface-2 p-4 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">
          {data.documentation}
        </pre>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------ backtest */

function BacktestSection() {
  const [perf, setPerf] = useState<ModelPerformance | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // aborta o request pesado se a secao for desmontada (troca de aba)
  const abortRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const run = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      setPerf(await fetchModelPerformance({ split: 0.7, min_ev: 0.03 }, controller.signal));
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Falha no backtest.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  return (
    <Card
      title="Validação out-of-sample"
      hint="Backtest walk-forward: treina no passado, testa no futuro. Nenhum lookahead."
      action={
        <Button
          variant="outline"
          size="sm"
          loading={loading}
          onClick={() => void run()}
          startIcon={<RefreshCwIcon className="size-3.5" />}
        >
          {perf ? "Rodar novamente" : "Rodar backtest"}
        </Button>
      }
    >
      {error ? (
        <ErrorPanel message={error} onRetry={() => void run()} />
      ) : !perf ? (
        <p className="py-4 text-center text-body text-ink-4">
          O backtest é executado sob demanda no backend (pode levar alguns segundos).
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Log-loss" value={fmtNum(perf.logloss, 4)} hint="Uniforme = 1,0986; menor é melhor." />
            <Stat label="Brier" value={fmtNum(perf.brier, 4)} hint="Uniforme = 0,6667; menor é melhor." />
            <Stat label="Acerto top pick" value={fmtPct(perf.accuracy)} hint="Chute aleatório = 33,3%." />
            <Stat label="Apostas" value={fmtInt(perf.n_bets)} hint={`${fmtInt(perf.n_train)} treino / ${fmtInt(perf.n_test)} teste`} />
            <Stat
              label="ROI"
              value={fmtPctSigned(perf.roi)}
              hint="Lucro sobre o total apostado."
              tone={signedColorClass(perf.roi)}
            />
            <Stat
              label="Drawdown máx."
              value={fmtPct(perf.max_drawdown)}
              hint="Maior queda da banca no período."
              tone="text-neg-400"
            />
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[240px_1fr]">
            <div>
              <p className="label-caps mb-2">Curva de calibração</p>
              <CalibrationChart bins={perf.calibration_bins} />
            </div>
            <div className="grid grid-cols-2 gap-3 self-start md:grid-cols-3">
              <Stat label="Banca inicial" value={fmtNum(perf.bankroll_start)} />
              <Stat label="Banca final" value={fmtNum(perf.bankroll_end)} />
              <Stat
                label="Lucro"
                value={fmtSigned(perf.profit)}
                tone={signedColorClass(perf.profit)}
              />
              <Stat label="Total apostado" value={fmtNum(perf.total_staked)} />
              <Stat label="Acertos" value={`${fmtInt(perf.n_wins)} (${fmtPct(perf.hit_rate)})`} />
              <Stat
                label="EV médio previsto"
                value={fmtPctSigned(perf.ev_mean_pred)}
                hint="Média dos EVs apostados."
              />
              <Stat
                label="Retorno médio real"
                value={fmtPctSigned(perf.return_mean_real)}
                hint="Gap vs EV previsto mede a calibração."
                tone={signedColorClass(perf.return_mean_real)}
              />
              <Stat label="Split" value={`${fmtPct(perf.split, 0)} treino`} />
              <Stat label="Amostra de teste" value={fmtInt(perf.n_test)} />
            </div>
          </div>

          <details className="rounded-md border border-line bg-surface-2">
            <summary className="cursor-pointer px-3 py-2 text-body text-ink-2 select-none">
              Relatório completo em texto (gerado pelo backend)
            </summary>
            <pre className="thin-scrollbar max-h-[320px] overflow-auto border-t border-line p-3 font-mono text-[11.5px] leading-relaxed whitespace-pre text-ink-3">
              {perf.summary}
            </pre>
          </details>
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------- helpers */

function ParamList({ items }: { items: [string, string, string?][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      {items.map(([label, value, hint]) => (
        <div
          key={label}
          className="flex items-baseline justify-between gap-3 border-b border-line py-2"
        >
          {hint ? (
            <Tooltip content={hint}>
              <dt className="cursor-help truncate text-[12.5px] text-ink-3 underline decoration-dotted decoration-ink-4/50 underline-offset-2">
                {label}
              </dt>
            </Tooltip>
          ) : (
            <dt className="truncate text-[12.5px] text-ink-3">{label}</dt>
          )}
          <dd className="num shrink-0 text-[12.5px] font-medium text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  const body = (
    <div className="min-w-0 rounded-md border border-line bg-surface-2 p-2.5">
      <p className="label-caps truncate">{label}</p>
      <p className={cn("num mt-1 truncate text-body-lg font-semibold", tone ?? "text-ink")}>
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-[10.5px] leading-tight text-ink-4">{hint}</p> : null}
    </div>
  );
  return hint ? <Tooltip content={hint} className="block">{body}</Tooltip> : body;
}
