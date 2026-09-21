/**
 * BacktestKpis — cards de topo do resultado.
 *
 * Sinais, acertos, erros, taxa de acerto (com IC 95%), odd media e EV
 * medio previsto. O IC de Wilson acompanha a taxa porque taxa de acerto
 * sem intervalo e enganosa com N pequeno.
 */

import KpiCard from "@/components/ui/KpiCard";
import type { AggregateMetrics, Simulation } from "@/types/backtest";
import { fmtInt, fmtNum, fmtOdd, fmtPct, fmtPctSigned } from "@/utils/format";

export default function BacktestKpis({
  aggregate,
  simulation,
  minSample,
}: {
  aggregate: AggregateMetrics;
  simulation: Simulation;
  minSample: number;
}) {
  const [ciLow, ciHigh] = normalizeCi(aggregate.hit_rate_ci);
  const [retLow, retHigh] = normalizeCi(aggregate.realized_return_ci);

  return (
    <div className="flex flex-col gap-3">
      {!aggregate.sample_sufficient ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-lg border border-warn-500/40 bg-warn-900/40 px-3 py-2 text-body text-warn-300"
        >
          <span className="font-semibold">AMOSTRA INSUFICIENTE</span>
          <span className="text-ink-3">
            {fmtInt(aggregate.n_settled)} sinais liquidados, abaixo do mínimo de{" "}
            {fmtInt(minSample)}. Os números abaixo não sustentam conclusão.
          </span>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-3 2xl:grid-cols-6">
        <KpiCard
          label="Sinais"
          value={fmtInt(aggregate.n_signals)}
          context={`${fmtInt(aggregate.n_settled)} liquidados · ${fmtInt(aggregate.n_pushes)} push`}
          tooltip="Sinais que passaram por todos os filtros do Scanner no período testado."
        />
        <KpiCard
          label="Acertos"
          value={fmtInt(aggregate.n_wins)}
          context={`${fmtInt(aggregate.n_losses)} erros`}
          tone="positive"
        />
        <KpiCard
          label="Taxa de acerto"
          value={fmtPct(aggregate.hit_rate)}
          context={`IC 95%: ${fmtPct(ciLow)} – ${fmtPct(ciHigh)}`}
          tone="info"
          tooltip="Proporção de sinais vencidos. O intervalo usa o método de Wilson, apropriado para binomial com N pequeno."
        />
        <KpiCard
          label="Odd média"
          value={fmtOdd(aggregate.avg_odd)}
          context={`P modelo ${fmtPct(aggregate.avg_model_prob)} · P mercado ${fmtPct(aggregate.avg_market_prob)}`}
        />
        <KpiCard
          label="EV médio previsto"
          value={fmtPctSigned(aggregate.avg_ev)}
          context={`realizado ${fmtPctSigned(aggregate.avg_realized_return)}`}
          tone={aggregate.ev_gap > 0.02 ? "negative" : "positive"}
          tooltip={`Gap previsto − realizado: ${fmtPctSigned(aggregate.ev_gap)}. Gap positivo grande significa que o modelo promete mais do que entrega.`}
        />
        <KpiCard
          label="Brier / Log-loss"
          value={fmtNum(aggregate.brier, 4)}
          context={`log-loss ${fmtNum(aggregate.logloss, 4)}`}
          tooltip="Brier: média de (prob − resultado)². 0 = perfeito; 0,25 = sempre 50%. Log-loss penaliza confiança errada com mais força."
        />
      </div>

      {/* -------------------------------------------------- simulacao */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-3 2xl:grid-cols-6">
        <KpiCard
          label="Capital final"
          value={fmtNum(simulation.final_bankroll)}
          context={`inicial ${fmtNum(simulation.initial_bankroll)}`}
          tone={simulation.profit >= 0 ? "positive" : "negative"}
          tooltip="SIMULAÇÃO HISTÓRICA. Não é previsão de lucro futuro."
        />
        <KpiCard
          label="Retorno simulado"
          value={fmtPctSigned(simulation.return_pct)}
          context={`ROI ${fmtPctSigned(simulation.roi)}`}
          tone={simulation.return_pct >= 0 ? "positive" : "negative"}
        />
        <KpiCard
          label="Drawdown máximo"
          value={fmtPct(simulation.max_drawdown)}
          context={simulation.max_drawdown_day || "—"}
          tone="negative"
          tooltip="Maior queda do topo ao fundo na curva de capital simulada."
        />
        <KpiCard
          label="Total apostado"
          value={fmtNum(simulation.total_staked)}
          context={`${fmtInt(simulation.n_bets)} apostas`}
        />
        <KpiCard
          label="Maior sequência"
          value={`${fmtInt(simulation.longest_win_streak)}V / ${fmtInt(simulation.longest_loss_streak)}D`}
          context="vitórias / derrotas seguidas"
          tooltip="A maior sequência de derrotas é o que testa a resistência psicológica e o dimensionamento da banca."
        />
        <KpiCard
          label="Volatilidade"
          value={fmtNum(simulation.return_std, 4)}
          context="desvio-padrão do retorno por aposta"
          tooltip="Dispersão do retorno por unidade apostada. Retorno médio parecido com volatilidade muito diferente muda o risco."
        />
      </div>

      <p className="text-[11.5px] text-ink-4">
        Retorno médio realizado IC 95%: {fmtPctSigned(retLow)} a {fmtPctSigned(retHigh)}
        {simulation.exposure_scaled_days > 0
          ? ` · teto de exposição aplicado em ${fmtInt(simulation.exposure_scaled_days)} dias`
          : ""}
      </p>
    </div>
  );
}

function normalizeCi(ci: [number, number] | number[]): [number, number] {
  return [ci[0] ?? 0, ci[1] ?? 0];
}
