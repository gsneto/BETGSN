/**
 * Tons semanticos de badge. Separado de `Badge.tsx` para que o modulo do
 * componente exporte apenas componentes (fast refresh preservado) e para
 * que outros modulos possam mapear nivel -> tom sem importar JSX.
 */

export type BadgeTone =
  | "accent"
  | "positive"
  | "negative"
  | "info"
  | "neutral"
  | "warning";

/** Mapeia o nivel de confianca do backend para o tom semantico. */
export function confidenceTone(level: string): BadgeTone {
  switch (level) {
    case "FORTE":
      return "accent";
    case "MEDIA":
      return "info";
    case "FRACA":
      return "neutral";
    default:
      return "neutral";
  }
}
