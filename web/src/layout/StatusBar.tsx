/**
 * StatusBar — rodape do terminal.
 *
 * Paridade com a `_build_statusbar` da GUI legada: mensagem de estado a
 * esquerda e resumo financeiro (exposicao, lucro esperado, perda maxima)
 * a direita, alem do aviso de escalonamento de stake pelo teto de
 * exposicao.
 */

import { useStore } from "@/store/context";
import { fmtClock, fmtInt, fmtMoney, fmtMoneySigned, fmtPct, fmtPctSigned } from "@/utils/format";

export default function StatusBar() {
  const { summary, error, recalculating, status, socketState } = useStore();
  const k = summary?.kpis;

  const left = recalculating
    ? "Recalculando modelo, mercados e sinais…"
    : error
      ? `ERRO: ${error}`
      : summary
        ? `OK — ${fmtInt(summary.n_games)} jogos, ${fmtInt(k?.total ?? 0)} sinais acima do EV mínimo. Gerado em ${summary.generated_at}.`
        : "Aguardando primeiro cálculo…";

  const scaled =
    k && k.exposure_scaled_by < 0.999
      ? `  ·  stakes escaladas ×${k.exposure_scaled_by.toFixed(3)} pelo teto de exposição`
      : "";

  return (
    <footer className="sticky bottom-0 z-99 flex h-[28px] items-center gap-4 border-t border-line bg-header/95 px-4 backdrop-blur-md">
      <span
        className={
          error
            ? "min-w-0 flex-1 truncate text-[11.5px] text-neg-400"
            : "min-w-0 flex-1 truncate text-[11.5px] text-ink-3"
        }
        title={left}
      >
        {left}
      </span>

      {k && summary ? (
        <span className="num hidden shrink-0 items-center gap-3 text-[11.5px] text-ink-3 lg:flex">
          <span>
            Exposição{" "}
            <b className="font-semibold text-ink">
              {fmtMoney(k.total_exposure)} ({fmtPct(k.total_exposure_pct)})
            </b>
          </span>
          <span aria-hidden className="text-ink-4">
            ·
          </span>
          <span>
            Lucro esp.{" "}
            <b className={k.expected_profit >= 0 ? "font-semibold text-pos-400" : "font-semibold text-neg-400"}>
              {fmtMoneySigned(k.expected_profit)} ({fmtPctSigned(k.expected_profit_pct, 2)})
            </b>
          </span>
          <span aria-hidden className="text-ink-4">
            ·
          </span>
          <span>
            Perda máx.{" "}
            <b className="font-semibold text-neg-400">{fmtMoney(k.worst_case_loss)}</b>
          </span>
          {scaled ? <span className="text-ink-4">{scaled}</span> : null}
        </span>
      ) : null}

      <span className="hidden shrink-0 items-center gap-2 text-[11.5px] text-ink-4 md:flex">
        <span className="num">
          {status ? `backend v${status.version} · python ${status.python_version}` : "backend —"}
        </span>
        <span aria-hidden>·</span>
        <span className="num">
          {socketState === "open"
            ? "ws conectado"
            : socketState === "connecting"
              ? "ws conectando"
              : "ws offline"}
        </span>
        <span aria-hidden>·</span>
        <span className="num">{fmtClock(summary?.generated_at)}</span>
      </span>
    </footer>
  );
}
