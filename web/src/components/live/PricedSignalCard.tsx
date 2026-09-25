/**
 * PricedSignalCard — apresentação honesta de um sinal precificado.
 *
 * Nada aqui promete aposta: execução nunca é presumida e `NO_BET` continua
 * como resposta padrão quando faltam evidências, gate ou execução medida.
 */
import Badge from "@/components/ui/Badge";
import Card from "@/components/ui/Card";
import { fmtOdd } from "@/utils/format";
import type { PricedSignalPayload } from "@/types/realtime";

const STATUS_TONE = {
  MEASURED: "positive",
  UNKNOWN: "warning",
} as const;

const FRESHNESS_TONE = {
  LIVE: "positive",
  STALE: "warning",
  EXPIRED: "negative",
  UNKNOWN: "neutral",
} as const;

const EVIDENCE_TONE = {
  FORTE: "positive",
  RESEARCH: "warning",
} as const;

const PRODUCTION_TONE = {
  REVIEW: "warning",
  NO_BET: "negative",
} as const;

function fmtPct(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(2)}%`;
}

function fmtNum(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? "—" : value.toFixed(4);
}

interface Props {
  signal: PricedSignalPayload;
}

export default function PricedSignalCard({ signal }: Props) {
  return (
    <Card
      title={`Priced signal · ${signal.market} · ${signal.selection}`}
      hint={`gate ${signal.policy_fingerprint.slice(0, 8)} · modelo ${
        signal.model_fingerprint || "n/d"
      }`}
    >
      <div className="flex flex-col gap-3 p-3 text-xs" data-testid="priced-signal-card">
        <div className="flex flex-wrap gap-2">
          <Badge tone={EVIDENCE_TONE[signal.evidence_status]} size="sm">
            {signal.evidence_status === "FORTE" ? "FORTE" : "RESEARCH"}
          </Badge>
          <Badge tone={PRODUCTION_TONE[signal.production]} size="sm">
            {signal.production === "REVIEW" ? "LIMITED / REVIEW" : "NO_BET"}
          </Badge>
          <Badge tone={FRESHNESS_TONE[signal.freshness]} size="sm">
            {signal.freshness}
          </Badge>
          <Badge tone={STATUS_TONE[signal.execution_status]} size="sm">
            {signal.execution_status === "MEASURED" ? "EXECUTADO" : "UNKNOWN"}
          </Badge>
        </div>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-ink-3">
          <dt className="text-ink-4">Melhor preço</dt>
          <dd className="num text-right">
            {signal.observed_price == null ? "—" : fmtOdd(signal.observed_price)}
          </dd>
          <dt className="text-ink-4">Preço selecionado</dt>
          <dd className="num text-right">
            {signal.selected_price == null ? "—" : fmtOdd(signal.selected_price)}
            <span className="text-ink-4"> @{signal.book || "?"}</span>
          </dd>
          <dt className="text-ink-4">Execução</dt>
          <dd className="num text-right">
            {signal.executed_price == null ? "UNKNOWN" : fmtOdd(signal.executed_price)}
          </dd>
          <dt className="text-ink-4">Fechamento</dt>
          <dd className="num text-right">
            {signal.closing_price == null ? "aguardando" : fmtOdd(signal.closing_price)}
          </dd>
          <dt className="text-ink-4">Fair price</dt>
          <dd className="num text-right">
            {signal.fair_prob == null ? "—" : fmtOdd(1 / signal.fair_prob)}
          </dd>
          <dt className="text-ink-4">Modelo</dt>
          <dd className="num text-right">{fmtPct(signal.model_prob)}</dd>
          <dt className="text-ink-4">Edge</dt>
          <dd className="num text-right">{fmtPct(signal.edge)}</dd>
          <dt className="text-ink-4">EV</dt>
          <dd className="num text-right">{fmtPct(signal.ev)}</dd>
          <dt className="text-ink-4">Spread</dt>
          <dd className="num text-right">{fmtNum(signal.spread)}</dd>
          <dt className="text-ink-4">Livros</dt>
          <dd className="num text-right">{signal.books_count}</dd>
          <dt className="text-ink-4">Stake</dt>
          <dd className="num text-right">{signal.stake === 0 ? "0 (NO_BET)" : signal.stake}</dd>
        </dl>

        {signal.research_reasons.length ? (
          <div className="rounded bg-warn-400/10 p-2 text-[11px] text-warn-200">
            <strong className="mr-1">RESEARCH:</strong>
            {signal.research_reasons.join(", ")}
          </div>
        ) : null}

        <p className="text-[10.5px] text-ink-4">
          Sinal informativo. NO_BET permanece enquanto qualquer bloco do gate
          estiver RED ou UNKNOWN e enquanto a execução real não for medida.
        </p>
      </div>
    </Card>
  );
}
