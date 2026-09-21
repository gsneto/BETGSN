/**
 * StatsPage — tela ESTATÍSTICAS.
 *
 * Paridade com a aba ESTATISTICAS da GUI legada (ratings por time) e
 * aproveitamento de dados que ja existiam no backend mas nao tinham
 * visualizacao: distribuicao de EV, breakdown por mercado, por
 * confianca e por casa (tudo de GET /api/stats).
 *
 * Nenhuma metrica nova e calculada aqui. "Amostra", "cobertura" e
 * "share" sao contagens e proporcoes diretas dos dados recebidos.
 */

import { useMemo, useState } from "react";
import { fetchStats } from "@/api/stats";
import Badge from "@/components/ui/Badge";
import { confidenceTone } from "@/components/ui/badgeTone";
import Card from "@/components/ui/Card";
import { ColumnChart, HorizontalBars } from "@/components/ui/Charts";
import KpiCard from "@/components/ui/KpiCard";
import { SearchInput } from "@/components/ui/Input";
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
  Tr,
} from "@/components/ui";
import { useApiResource } from "@/hooks/useApiResource";
import { useStore } from "@/store/context";
import type { TeamSnapshot } from "@/types/api";
import { cn } from "@/utils/cn";
import {
  confidenceLabel,
  fmtInt,
  fmtNum,
  fmtPct,
  fmtPctSigned,
  signedColorClass,
} from "@/utils/format";
import { sortBy, textFilter, toggleSort, type SortState } from "@/utils/table";

type SortKey =
  | "name"
  | "attack"
  | "defense"
  | "strength"
  | "goals_for"
  | "goals_against"
  | "xg_for"
  | "xg_against"
  | "corners_for"
  | "cards_for"
  | "form_points"
  | "matches_played";

const extractors: Record<SortKey, (t: TeamSnapshot) => number | string> = {
  name: (t) => t.name,
  attack: (t) => t.attack,
  defense: (t) => t.defense,
  strength: (t) => t.strength,
  goals_for: (t) => t.goals_for,
  goals_against: (t) => t.goals_against,
  xg_for: (t) => t.xg_for ?? -Infinity,
  xg_against: (t) => t.xg_against ?? Infinity,
  corners_for: (t) => t.corners_for,
  cards_for: (t) => t.cards_for,
  form_points: (t) => t.form_points,
  matches_played: (t) => t.matches_played,
};

export default function StatsPage() {
  const { dataVersion } = useStore();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortState<SortKey>>({
    key: "strength",
    direction: "desc",
  });

  const { data, initialLoading, error, reload } = useApiResource(
    (signal) => fetchStats(signal),
    [dataVersion],
  );

  const teams = useMemo(() => {
    const filtered = textFilter(data?.teams ?? [], query, (t) => [t.name]);
    return sortBy(filtered, extractors[sort.key], sort.direction);
  }, [data, query, sort]);

  const onSort = (key: SortKey, defaultDir: "asc" | "desc" = "desc") =>
    setSort((cur) => toggleSort(cur, key, defaultDir));

  if (initialLoading) {
    return (
      <div className="flex flex-col gap-3">
        <KpiSkeleton />
        <TableSkeleton rows={12} cols={9} />
      </div>
    );
  }

  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />;
  if (!data) return null;

  const totalSignals = data.by_market.reduce((acc, m) => acc + m.signals, 0);

  const head = (
    key: SortKey,
    label: string,
    width: number,
    align: "start" | "end" = "end",
    dir: "asc" | "desc" = "desc",
    title?: string,
  ) => (
    <Th
      width={width}
      align={align}
      sortable
      active={sort.key === key}
      direction={sort.direction}
      onClick={() => onSort(key, dir)}
      title={title}
    >
      {label}
    </Th>
  );

  return (
    <div className="flex flex-col gap-3">
      {error ? <ErrorPanel message={error} onRetry={reload} /> : null}

      {/* ---------------------------------------------------------- KPIs */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <KpiCard
          label="Amostra histórica"
          value={fmtInt(data.n_history_matches)}
          context={`${fmtInt(data.n_fixtures)} jogos futuros analisados`}
          tooltip="Partidas usadas para ajustar os ratings de ataque e defesa."
        />
        <KpiCard
          label="Média de gols da liga"
          value={fmtNum(data.league_goals)}
          context="gols por jogo (ambos os times)"
          tone="accent"
          tooltip="Base do cálculo de λ. Sai do próprio histórico, não é fixada."
        />
        <KpiCard
          label="Vantagem de casa"
          value={`×${fmtNum(data.home_advantage)}`}
          context="fator multiplicativo do mandante"
          tooltip="λ casa = ataque × defesa adversária × base × vantagem de casa."
        />
        <KpiCard
          label="Times avaliados"
          value={fmtInt(data.teams.length)}
          context={`${fmtInt(totalSignals)} sinais distribuídos em ${fmtInt(data.by_market.length)} mercados`}
          tone="info"
          tooltip="Cada time recebe um rating de ataque e um de defesa por ponto fixo."
        />
      </div>

      {/* -------------------------------------------------- distribuicoes */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Card
          title="Distribuição de EV"
          hint="Quantos sinais caem em cada faixa de valor esperado"
        >
          <ColumnChart
            data={data.ev_distribution.map((b) => ({
              label: b.label,
              value: b.count,
              tone: b.lower >= 0.08 ? "positive" : b.lower >= 0.045 ? "info" : "neutral",
            }))}
            formatValue={(v) => fmtInt(v)}
          />
        </Card>

        <Card
          title="Sinais por mercado"
          hint="Onde o modelo encontra valor com mais frequência"
        >
          <HorizontalBars
            data={data.by_market.map((m) => ({
              label: m.market,
              value: m.signals,
              tone: "accent" as const,
            }))}
            formatValue={(v) => fmtInt(v)}
          />
        </Card>

        <Card title="Sinais por casa" hint="Distribuição dos sinais pela melhor odd">
          <HorizontalBars
            data={data.by_book.map((b) => ({
              label: b.book,
              value: b.signals,
              tone: "info" as const,
            }))}
            formatValue={(v) => fmtInt(v)}
          />
        </Card>
      </div>

      {/* ------------------------------------------------ breakdown tabelas */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card title="Performance por mercado" padded={false}>
          <div className="p-3">
            <TableShell className="max-h-[300px]">
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start" width={180}>
                      Mercado
                    </Th>
                    <Th align="end" width={80}>
                      Sinais
                    </Th>
                    <Th align="end" width={90} title="EV médio dos sinais deste mercado">
                      EV médio
                    </Th>
                    <Th align="end" width={90} title="Edge médio em pontos percentuais">
                      Edge médio
                    </Th>
                    <Th align="end" width={90} title="Melhor EV encontrado no mercado">
                      Melhor EV
                    </Th>
                    <Th align="end" width={100} title="Lucro esperado somado">
                      Lucro esp.
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {data.by_market.map((m) => (
                    <Tr key={m.market}>
                      <Td align="start" className="max-w-[180px]">
                        <span className="block truncate text-ink" title={m.market}>
                          {m.market}
                        </span>
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtInt(m.signals)}
                      </Td>
                      <Td mono className={signedColorClass(m.avg_ev)}>
                        {fmtPctSigned(m.avg_ev)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {fmtPct(m.avg_edge)}
                      </Td>
                      <Td mono className="text-pos-400">
                        {fmtPctSigned(m.best_ev)}
                      </Td>
                      <Td mono className={signedColorClass(m.expected_profit)}>
                        {fmtNum(m.expected_profit)}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
          </div>
        </Card>

        <Card title="Performance por confiança" padded={false}>
          <div className="p-3">
            <TableShell>
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    <Th align="start" width={120}>
                      Nível
                    </Th>
                    <Th align="end" width={80}>
                      Sinais
                    </Th>
                    <Th align="end" width={90}>
                      EV médio
                    </Th>
                    <Th align="end" width={80}>
                      Odd média
                    </Th>
                    <Th align="end" width={100}>
                      Stake total
                    </Th>
                    <Th align="end" width={100}>
                      Lucro esp.
                    </Th>
                  </Tr>
                </THead>
                <TBody>
                  {data.by_confidence.map((c) => (
                    <Tr key={c.confidence}>
                      <Td align="start">
                        <Badge tone={confidenceTone(c.confidence)} dot size="sm">
                          {confidenceLabel(c.confidence)}
                        </Badge>
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtInt(c.signals)}
                      </Td>
                      <Td mono className={signedColorClass(c.avg_ev)}>
                        {fmtPctSigned(c.avg_ev)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(c.avg_odd)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(c.total_stake)}
                      </Td>
                      <Td mono className={signedColorClass(c.expected_profit)}>
                        {fmtNum(c.expected_profit)}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableShell>
            <p className="mt-2 text-[11px] text-ink-4">
              Limiares: FORTE EV ≥ {fmtPct(0.08, 1)} · MÉDIA ≥ {fmtPct(0.045, 1)} · FRACA ≥{" "}
              {fmtPct(0.02, 1)}
            </p>
          </div>
        </Card>
      </div>

      {/* ------------------------------------------------- ratings por time */}
      <Card
        title="Ratings e estatísticas por time"
        hint="Por jogo · ataque/defesa relativo à média da liga (1,00 = média)"
        padded={false}
        action={
          <SearchInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClear={() => setQuery("")}
            placeholder="Buscar time…"
            aria-label="Buscar time"
            className="w-[220px]"
          />
        }
      >
        <div className="p-3">
          <TableShell className="max-h-[480px]">
            {teams.length === 0 ? (
              <EmptyState title="Nenhum time encontrado" hint="Ajuste a busca." />
            ) : (
              <Table>
                <THead>
                  <Tr className="h-[34px] hover:bg-transparent">
                    {head("name", "Time", 170, "start", "asc")}
                    {head("attack", "Ataque", 82, "end", "desc", "Força ofensiva relativa à média")}
                    {head("defense", "Defesa", 82, "end", "asc", "Fragilidade defensiva (menor = melhor)")}
                    {head("strength", "Força", 82, "end", "desc", "Índice único: ataque / defesa")}
                    {head("goals_for", "GP", 64, "end", "desc", "Gols pró por jogo")}
                    {head("goals_against", "GC", 64, "end", "asc", "Gols contra por jogo")}
                    {head("xg_for", "xG+", 68, "end", "desc", "xG criado por jogo")}
                    {head("xg_against", "xG−", 68, "end", "asc", "xG concedido por jogo")}
                    {head("corners_for", "Ct+", 64, "end", "desc", "Cantos a favor por jogo")}
                    {head("cards_for", "Cart+", 68, "end", "desc", "Cartões por jogo")}
                    {head("form_points", "Pts", 64, "end", "desc", "Média de pontos por jogo")}
                    {head("matches_played", "J", 52, "end", "desc", "Jogos na amostra")}
                  </Tr>
                </THead>
                <TBody>
                  {teams.map((t) => (
                    <Tr key={t.name}>
                      <Td align="start" className="font-medium text-ink">
                        {t.name}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(t.attack)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(t.defense)}
                      </Td>
                      <Td mono className={cn("font-semibold", "text-accent-300")}>
                        {fmtNum(t.strength)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(t.goals_for)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(t.goals_against)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {fmtNum(t.xg_for)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {fmtNum(t.xg_against)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {fmtNum(t.corners_for, 1)}
                      </Td>
                      <Td mono className="text-ink-3">
                        {fmtNum(t.cards_for, 1)}
                      </Td>
                      <Td mono className="text-ink-2">
                        {fmtNum(t.form_points)}
                      </Td>
                      <Td mono className="text-ink-4">
                        {fmtInt(t.matches_played)}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            )}
          </TableShell>
        </div>
      </Card>
    </div>
  );
}
