/**
 * Badges de frescor e status do terminal em tempo real.
 *
 * FRESH/RECENT/STALE/UNKNOWN vem do backend, calculados pela IDADE REAL
 * da observacao — nunca pelo momento da captura.
 */
import Badge from "@/components/ui/Badge";
import type { BadgeTone } from "@/components/ui/badgeTone";
import type { FreshnessState, MatchStatus, SignalStatus } from "@/types/realtime";

const freshnessTone: Record<FreshnessState, BadgeTone> = {
  FRESH: "positive",
  RECENT: "info",
  STALE: "warning",
  UNKNOWN: "neutral",
};

const freshnessLabel: Record<FreshnessState, string> = {
  FRESH: "FRESCO",
  RECENT: "RECENTE",
  STALE: "ATRASADO",
  UNKNOWN: "DESCONHECIDO",
};

export function FreshnessBadge({ state, ageSeconds }: { state: FreshnessState; ageSeconds?: number | null }) {
  const age =
    ageSeconds == null
      ? ""
      : ageSeconds < 0
        ? " (futuro?)"
        : ` (${ageSeconds < 90 ? `${Math.round(ageSeconds)}s` : `${Math.round(ageSeconds / 60)}min`})`;
  return (
    <Badge tone={freshnessTone[state]} size="sm" dot>
      {freshnessLabel[state] + age}
    </Badge>
  );
}

const matchStatusTone: Record<MatchStatus, BadgeTone> = {
  PRE_MATCH: "info",
  IN_PLAY: "positive",
  FINISHED: "neutral",
  UNKNOWN: "warning",
};

const matchStatusLabel: Record<MatchStatus, string> = {
  PRE_MATCH: "PRÉ-JOGO",
  IN_PLAY: "EM ANDAMENTO",
  FINISHED: "ENCERRADO",
  UNKNOWN: "HORÁRIO ?",
};

export function MatchStatusBadge({ status, matched }: { status: MatchStatus; matched: boolean }) {
  if (!matched) {
    return (
      <Badge tone="warning" size="sm">
        UNMATCHED
      </Badge>
    );
  }
  return (
    <Badge tone={matchStatusTone[status]} size="sm">
      {matchStatusLabel[status]}
    </Badge>
  );
}

const signalStatusTone: Record<SignalStatus, BadgeTone> = {
  ACTIVE: "positive",
  STALE: "warning",
  EXPIRED: "neutral",
};

export function SignalStatusBadge({ status }: { status: SignalStatus }) {
  return (
    <Badge tone={signalStatusTone[status]} size="sm">
      {status}
    </Badge>
  );
}

/** Mapa de rotulo curto por tipo de sinal (Fase 11). */
export const SIGNAL_TYPE_LABEL: Record<string, string> = {
  STALE_PRICE: "PREÇO PARADO",
  BOOKMAKER_OUTLIER: "OUTLIER",
  CONSENSUS_MOVE: "CONSENSO",
  BOOKMAKER_LEAD: "LIDER",
  BOOKMAKER_LAG: "ATRASADO",
  RAPID_CONVERGENCE: "CONVERGÊNCIA",
  DISPERSION_SPIKE: "DISPERSÃO",
  PRICE_REVERSAL: "REVERSÃO",
  BEST_PRICE_GAP: "GAP MELHOR PREÇO",
};
