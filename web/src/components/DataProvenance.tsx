import type { Provenance } from "@/types/api";

export default function DataProvenance({ data, updated }: { data?: Provenance; updated: string }) {
  return <section aria-label="Proveniência dos dados" className="rounded border border-line p-3 text-xs text-ink-3">
    <span>Fonte: {data?.source ?? "não informada"} · </span>
    <span>Atualizado: {updated} · </span>
    <span>Modelo: {data?.model_version ?? "não informado"} · </span>
    <span>Calibração: {data?.calibration_status ?? "não informada"} · </span>
    <span>xG: {data?.xg_status ?? "UNAVAILABLE"} ({data?.xg_source ?? "sem fonte"}) · </span>
    <span>Timestamp das odds: {data?.odds_timestamp ?? "indisponível"}</span>
  </section>;
}
