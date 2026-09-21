/**
 * PortfolioPage — tela PORTFÓLIO.
 *
 * Duas fontes, ambas do backend, sem nenhum cálculo local:
 *  - GET /api/portfolio/exposure     → exposição agregada das simples;
 *  - GET /api/portfolio/best-parlays → múltiplas candidatas já ranqueadas.
 *
 * O único trabalho feito aqui em cima dos dados recebidos é CONTAR
 * quantas pernas de uma múltipla pertencem ao mesmo jogo, para marcar as
 * linhas cujo EV é sabidamente não confiável (ver `correlatedMatches`).
 * Nenhuma probabilidade, EV ou stake é recalculado no frontend.
 */

import { useEffect, useMemo, useState } from "react";
import { fetchBestParlays, fetchExposure } from "@/api/portfolio";
import Badge from "@/components/ui/Badge";
import type { BadgeTone } from "@/components/ui/badgeTone";
import Card from "@/components/ui/Card";
import KpiCard from "@/components/ui/KpiCard";
import { Field, Input } from "@/components/ui/Input";
import SegmentedControl, { type Segment } from "@/components/ui/SegmentedControl";
import {
  EmptyState,
  ErrorPanel,
  KpiSkeleton,
  Table,
  TableShell,
  TableSkeleton,
  TBody,
  Td,
  Th,
  THead,
  Tooltip,
  Tr,
} from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import type { ParlayCandidate } from "@/types/portfolio";
import { cn } from "@/utils/cn";
import {
  fmtInt,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtPctSigned,
  signedColorClass,
} from "@/utils/format";

type MaxLegs = "2" | "3" | "4";

const LEG_SEGMENTS: Segment<MaxLegs>[] = [
  { value: "2", label: "2 pernas" },
  { value: "3", label: "até 3" },
  { value: "4", label: "até 4" },
];

/**
 * Jogos que aparecem em mais de uma perna. O endpoint gera múltiplas com
 * até `max_same_match` pernas do mesmo jogo e calcula a probabilidade
 * conjunta como produto simples das probabilidades — sem ajuste de
 * correlação. Logo, estas linhas têm EV superestimado.
 */
function correlatedMatches(p: ParlayCandidate): string[] {
  const counts = new Map<string, number>();
  for (const leg of p.legs) counts.set(leg.match, (counts.get(leg.match) ?? 0) + 1);
  return [...counts.entries()].filter(([, n]) => n > 1).map(([m]) => m);
}

/** Faixas de risco conforme o `risk_score` que o backend já entrega. */
function riskTone(score: number): BadgeTone {
  if (score < 0.3) return "positive";
  if (score < 0.6) return "warning";
  return "negative";
}

function riskLabel(score: number): string {
  if (score < 0.3) return "baixo";
  if (score < 0.6) return "médio";
  return "alto";
}

/** Chave estável: a combinação de pernas identifica unicamente a múltipla. */
function parlayKey(p: ParlayCandidate): string {
  return p.legs.map((l) => `${l.match}|${l.market}|${l.outcome}`).join("//");
}

export default function PortfolioPage() {
  const { dataVersion } = useStore();

  const [maxLegs, setMaxLegs] = useState<MaxLegs>("3");
  // texto cru do campo (permite "-", "0," etc. enquanto o usuario digita)
  const [minEvText, setMinEvText] = useState("0");
  // valor efetivamente enviado a API, com debounce para nao disparar um
  // request por tecla
  const [minEv, setMinEv] = useState(0);

  useEffect(() => {
    const parsed = Number(minEvText.replace(",", "."));
    const next = Number.isFinite(parsed) ? parsed / 100 : 0;
    const timer = window.setTimeout(() => setMinEv(next), 400);
    return () => window.clearTimeout(timer);
  }, [minEvText]);

  const exposure = useApiResource((signal) => fetchExposure(signal), [dataVersion]);

  const parlays = useApiResource(
    (signal) => fetchBestParlays({ max_legs: Number(maxLegs), min_ev: minEv }, signal),
    [dataVersion, maxLegs, minEv],
  );

  const rows = useMemo(
    () =>
      (parlays.data ?? []).map((p) => ({
        parlay: p,
        key: parlayKey(p),
        correlated: correlatedMatches(p),
      })),
    [parlays.data],
  );

  const exp = exposure.data;

  const disclaimer = (
    <Card className="border-warn-500/40 bg-warn-900/20">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-[5px] size-[7px] shrink-0 rounded-full bg-warn-400"
        />
        <div className="min-w-0 text-body leading-relaxed text-ink-2">
          <p className="font-medium text-warn-300">
            Como ler a probabilidade conjunta destas múltiplas
          </p>
          <p className="mt-1">
            A probabilidade conjunta é calculada pelo backend como o{" "}
            <strong className="font-medium text-ink">produto simples</strong> das
            probabilidades de cada perna, ou seja, assume que as pernas são
            independentes entre si. Essa premissa é razoável para pernas de{" "}
            <strong className="font-medium text-ink">jogos diferentes</strong>.
          </p>
          <p className="mt-1">
            Pernas do <strong className="font-medium text-ink">mesmo jogo</strong> são
            correlacionadas (por exemplo, "casa vence" e "mais de 2,5 gols" no mesmo
            confronto não são eventos independentes). Nesses casos a probabilidade
            conjunta exibida — e portanto o EV, o Kelly e o stake derivados dela —{" "}
            <strong className="font-medium text-warn-300">não é confiável</strong> e
            costuma ser otimista demais. As linhas afetadas estão marcadas com um
            indicador âmbar.
          </p>
        </div>
      </div>
    </Card>
  );

  if (exposure.initialLoading && parlays.initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <KpiSkeleton />
        <TableSkeleton rows={10} cols={8} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* ---------------------------------------------------- exposicao */}
      {exposure.error && !exp ? (
        <ErrorPanel message={exposure.error} onRetry={exposure.reload} />
      ) : null}

      {exposure.initialLoading ? (
        <KpiSkeleton />
      ) : exp ? (
        <>
          {exposure.error ? (
            <ErrorPanel message={exposure.error} onRetry={exposure.reload} />
          ) : null}

          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <KpiCard
              label="Exposição total"
              value={fmtMoney(exp.total_exposure)}
              context={`distribuída em ${fmtInt(Object.keys(exp.by_match).length)} jogos`}
              tooltip="Soma dos stakes das apostas simples do snapshot atual."
            />
            <KpiCard
              label="Exposição % da banca"
              value={fmtPct(exp.total_exposure_pct)}
              context="fração da banca comprometida"
              tone={exp.within_limits ? "accent" : "negative"}
              tooltip="Percentual da banca alocado. Comparado ao limite configurado no backend."
            />
            <KpiCard
              label="Nº de apostas"
              value={fmtInt(exp.n_bets)}
              context="apostas simples com stake > 0"
              tone="info"
              tooltip="Quantidade de sinais que receberam stake no dimensionamento."
            />
            <KpiCard
              label="Dentro dos limites"
              value={exp.within_limits ? "Sim" : "Não"}
              context={
                exp.within_limits
                  ? "nenhuma violação reportada"
                  : `${fmtInt(exp.violations.length)} violação(ões) reportada(s)`
              }
              tone={exp.within_limits ? "positive" : "negative"}
              tooltip="Veredito do backend sobre os limites de exposição total e stake individual."
            />
          </div>

          {exp.violations.length > 0 ? (
            <Card
              title="Violações de limite"
              hint="Reportadas pelo backend em /api/portfolio/exposure"
              className="border-warn-500/40 bg-warn-900/20"
            >
              <ul className="flex flex-col gap-1.5">
                {exp.violations.map((v) => (
                  <li key={v} className="flex items-start gap-2 text-body text-ink-2">
                    <span
                      aria-hidden
                      className="mt-[7px] size-[5px] shrink-0 rounded-full bg-warn-400"
                    />
                    <span className="min-w-0 break-words">{v}</span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </>
      ) : null}

      {/* ----------------------------------------------------- múltiplas */}
      {disclaimer}

      <Card
        title="Melhores múltiplas"
        hint="Combinações geradas e ranqueadas pelo backend a partir dos sinais atuais"
        padded={false}
        action={
          <div className="flex items-end gap-3">
            <Field label="Máx. pernas" className="w-[230px]">
              <SegmentedControl
                segments={LEG_SEGMENTS}
                value={maxLegs}
                onChange={setMaxLegs}
                ariaLabel="Número máximo de pernas"
              />
            </Field>
            <Field label="EV mínimo" htmlFor="portfolio-min-ev" className="w-[110px]">
              <Input
                id="portfolio-min-ev"
                type="number"
                step="0.5"
                mono
                suffix="%"
                value={minEvText}
                onChange={(e) => setMinEvText(e.target.value)}
                aria-label="EV mínimo em percentual"
              />
            </Field>
          </div>
        }
      >
        <div className="p-3">
          {parlays.error && !parlays.data ? (
            <ErrorPanel message={parlays.error} onRetry={parlays.reload} />
          ) : parlays.initialLoading ? (
            <TableSkeleton rows={8} cols={8} />
          ) : (
            <>
              {parlays.error ? (
                <ErrorPanel
                  message={parlays.error}
                  onRetry={parlays.reload}
                  className="mb-3"
                />
              ) : null}

              <TableShell className="max-h-[560px]">
                {rows.length === 0 ? (
                  <EmptyState
                    title="Nenhuma múltipla com EV acima do filtro"
                    hint="Reduza o EV mínimo ou aumente o número de pernas. O backend só retorna combinações que passam no filtro."
                  />
                ) : (
                  <Table>
                    <THead>
                      <Tr className="h-[34px] hover:bg-transparent">
                        <Th align="start" width={92}>
                          Categoria
                        </Th>
                        <Th align="start" width={330}>
                          Pernas
                        </Th>
                        <Th align="end" width={110} title="Produto das odds das pernas">
                          Odd combinada
                        </Th>
                        <Th
                          align="end"
                          width={120}
                          title="Produto das probabilidades do modelo (assume independência)"
                        >
                          Prob. conjunta
                        </Th>
                        <Th align="end" width={92} title="Valor esperado da múltipla">
                          EV
                        </Th>
                        <Th align="end" width={92} title="Fração de Kelly calculada no backend">
                          Kelly
                        </Th>
                        <Th align="end" width={100} title="Stake sugerido pelo backend">
                          Stake
                        </Th>
                        <Th align="end" width={110} title="Score de risco calculado no backend">
                          Risco
                        </Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {rows.map(({ parlay: p, key, correlated }) => (
                        <Tr key={key} className="h-auto">
                          <Td align="start" className="py-2 align-top">
                            <div className="flex items-center gap-1.5">
                              <Badge tone="neutral" size="sm">
                                {p.category}
                              </Badge>
                              {correlated.length > 0 ? (
                                <Tooltip
                                  content={`Pernas correlacionadas no mesmo jogo: ${correlated.join(", ")}. O EV exibido não é confiável.`}
                                >
                                  <span
                                    role="img"
                                    aria-label="pernas correlacionadas no mesmo jogo"
                                    className="size-[7px] shrink-0 rounded-full bg-warn-400"
                                  />
                                </Tooltip>
                              ) : null}
                            </div>
                          </Td>

                          <Td align="start" className="py-2 align-top whitespace-normal">
                            <div className="flex flex-col gap-0.5">
                              {p.legs.map((leg, i) => (
                                <span
                                  key={`${leg.match}|${leg.market}|${leg.outcome}|${i}`}
                                  className="block text-[11.5px] leading-snug text-ink-2"
                                >
                                  <span className="text-ink">{leg.match}</span>
                                  <span className="text-ink-4"> · </span>
                                  {leg.outcome}
                                  <span className="text-ink-4"> @ </span>
                                  <span className="num text-ink-2">{fmtNum(leg.odd)}</span>
                                </span>
                              ))}
                            </div>
                          </Td>

                          <Td mono className="py-2 align-top text-ink">
                            {fmtNum(p.combined_odd)}
                          </Td>
                          <Td mono className="py-2 align-top text-ink-2">
                            {fmtPct(p.joint_probability)}
                          </Td>
                          <Td
                            mono
                            className={cn(
                              "py-2 align-top font-semibold",
                              signedColorClass(p.ev),
                            )}
                          >
                            {fmtPctSigned(p.ev)}
                          </Td>
                          <Td mono className="py-2 align-top text-ink-3">
                            {fmtPct(p.kelly)}
                          </Td>
                          <Td mono className="py-2 align-top text-ink-2">
                            {fmtMoney(p.stake)}
                          </Td>
                          <Td className="py-2 align-top">
                            <Badge tone={riskTone(p.risk_score)} size="sm" dot>
                              {riskLabel(p.risk_score)} {fmtNum(p.risk_score)}
                            </Badge>
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                )}
              </TableShell>

              {rows.length > 0 ? (
                <p className="mt-2 text-[11px] text-ink-4">
                  Odd combinada, probabilidade conjunta, EV, Kelly, stake e risco vêm
                  prontos de <span className="num">/api/portfolio/best-parlays</span>. O
                  backend limita a resposta às 20 melhores por EV.
                </p>
              ) : null}
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
