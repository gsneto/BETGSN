/**
 * BacktestConfigPanel — configuracao do teste.
 *
 * Reutiliza os parametros do Scanner (EV minimo, usar xG, confianca,
 * banca, Kelly, risco, exposicao) em vez de inventar configuracoes novas.
 * Os mercados vem do proprio motor (`markets.MARKET_GROUPS`), garantindo
 * que a UI nunca ofereca um mercado que o backend nao sabe calcular.
 */

import { useEffect, useState } from "react";
import Button from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Input";
import { InfoIcon } from "@/components/ui/icons";
import type { BacktestOptions, BacktestRequest } from "@/types/backtest";
import { cn } from "@/utils/cn";
import { fmtInt, fmtPct } from "@/utils/format";

const KELLY_OPTIONS = [
  { value: "0.10", label: "0.10 — muito conservador" },
  { value: "0.25", label: "0.25 — quarter-Kelly" },
  { value: "0.33", label: "0.33 — third-Kelly" },
  { value: "0.50", label: "0.50 — half-Kelly" },
  { value: "1.00", label: "1.00 — Kelly completo" },
];

const EXPOSURE_OPTIONS = [
  { value: "0.10", label: "10%" },
  { value: "0.25", label: "25%" },
  { value: "0.50", label: "50%" },
  { value: "1.00", label: "100%" },
];

const CONFIDENCE_OPTIONS = [
  { value: "FRACA", label: "FRACA ou melhor (EV ≥ 2%)" },
  { value: "MEDIA", label: "MÉDIA ou melhor (EV ≥ 4,5%)" },
  { value: "FORTE", label: "FORTE (EV ≥ 8%)" },
  { value: "DESCARTE", label: "Todas (sem filtro de confiança)" },
];

interface Props {
  options: BacktestOptions;
  running: boolean;
  onRun: (request: BacktestRequest) => void;
}

export default function BacktestConfigPanel({ options, running, onRun }: Props) {
  const [config, setConfig] = useState<BacktestRequest>(options.default_config);
  const [minEvPct, setMinEvPct] = useState(
    (options.default_config.min_ev * 100).toFixed(1),
  );
  const [stakeCapPct, setStakeCapPct] = useState(
    (options.default_config.stake_cap * 100).toFixed(2),
  );

  // quando as opcoes chegam do backend, adota os defaults reais
  useEffect(() => {
    setConfig(options.default_config);
    setMinEvPct((options.default_config.min_ev * 100).toFixed(1));
    setStakeCapPct((options.default_config.stake_cap * 100).toFixed(2));
  }, [options]);

  const patch = (p: Partial<BacktestRequest>) =>
    setConfig((cur) => ({ ...cur, ...p }));

  const toggle = (list: string[], value: string, key: keyof BacktestRequest) => {
    const next = list.includes(value)
      ? list.filter((v) => v !== value)
      : [...list, value];
    patch({ [key]: next } as Partial<BacktestRequest>);
  };

  const submit = () => {
    const ev = Number(minEvPct.replace(",", ".")) / 100;
    const cap = Number(stakeCapPct.replace(",", ".")) / 100;
    onRun({
      ...config,
      min_ev: Number.isFinite(ev) && ev >= 0 ? ev : config.min_ev,
      stake_cap: Number.isFinite(cap) && cap > 0 ? cap : config.stake_cap,
      market_keys: config.market_keys.length
        ? config.market_keys
        : options.markets.map((m) => m.key),
    });
  };

  const activeOdds = options.odds_sources.find((s) => s.key === config.odds_source);
  const oddsNote = activeOdds?.note;

  return (
    <section className="panel flex flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div>
          <h2 className="label-caps">Configuração do teste</h2>
          <p className="mt-0.5 text-[11.5px] text-ink-4">
            Mesmos parâmetros do Scanner · schema {options.point_in_time_schema} ·
            modelo v{options.model_version}
          </p>
        </div>
        <Button
          variant="accent"
          size="md"
          loading={running}
          onClick={submit}
          className="tracking-[0.04em] uppercase"
        >
          {running ? "Executando" : "Executar backtest"}
        </Button>
      </header>

      <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[repeat(3,minmax(0,1fr))]">
        {/* ------------------------------------------------------ corpus */}
        <div className="flex flex-col gap-3">
          <p className="label-caps">Período</p>
          <div className="flex items-end gap-2">
            <Field label="Data inicial" htmlFor="bt-start" className="flex-1">
              <Input
                id="bt-start"
                type="date"
                mono
                min={options.min_date}
                max={options.max_date}
                value={config.start_date ?? options.min_date}
                onChange={(e) => patch({ start_date: e.target.value })}
              />
            </Field>
            <Field label="Data final" htmlFor="bt-end" className="flex-1">
              <Input
                id="bt-end"
                type="date"
                mono
                min={options.min_date}
                max={options.max_date}
                value={config.end_date ?? options.max_date}
                onChange={(e) => patch({ end_date: e.target.value })}
              />
            </Field>
          </div>
          <p className="text-[11px] text-ink-4">
            Histórico disponível: {options.min_date} → {options.max_date}
          </p>

          <Field
            label="Corpus histórico"
            htmlFor="bt-corpus"
            hint="Local = dataset sintético (seed fixo). Importado = temporadas reais baixadas da API-Football."
          >
            <Select
              id="bt-corpus"
              options={options.corpora.map((c) => ({
                value: c.key,
                label: c.available
                  ? `${c.label} — ${fmtInt(c.n_matches)} partidas`
                  : `${c.label} — indisponível`,
              }))}
              value={config.corpus_source}
              onChange={(e) =>
                patch({ corpus_source: e.target.value as BacktestRequest["corpus_source"] })
              }
            />
          </Field>
          <CorpusSummary options={options} />

          <Field
            label="Histórico mínimo antes de prever"
            htmlFor="bt-minhist"
            hint="Quantas partidas anteriores são exigidas antes de gerar um sinal. Abaixo disso o fit de ratings seria ruído."
          >
            <Input
              id="bt-minhist"
              mono
              inputMode="numeric"
              value={String(config.min_history)}
              onChange={(e) =>
                patch({ min_history: Math.max(1, Number(e.target.value) || 1) })
              }
            />
          </Field>

          <Field label="Fonte de odds" htmlFor="bt-odds">
            <Select
              id="bt-odds"
              options={options.odds_sources.map((s) => ({
                value: s.key,
                label: s.available ? s.label : `${s.label} — indisponível`,
              }))}
              value={config.odds_source}
              onChange={(e) =>
                patch({ odds_source: e.target.value as BacktestRequest["odds_source"] })
              }
            />
          </Field>
          {oddsNote ? (
            <p className="text-[11px] leading-relaxed text-ink-4">{oddsNote}</p>
          ) : null}

          {/* ------------------------------------ opções de odds reais (CSV) */}
          {config.odds_source === "football_data_uk" ? (
            <div className="flex flex-col gap-2.5 rounded-md border border-line-active/40 bg-accent-400/6 p-2.5">
              <Field
                label="Momento da linha"
                htmlFor="bt-closing"
                hint="Fechamento = preço no kickoff, a linha mais afiada do mercado (teste mais duro). Abertura = preço quando o mercado abriu (onde você apostaria cedo)."
              >
                <Select
                  id="bt-closing"
                  options={[
                    { value: "closing", label: "Fechamento (linha eficiente)" },
                    { value: "opening", label: "Abertura (preço inicial)" },
                  ]}
                  value={config.odds_closing ? "closing" : "opening"}
                  onChange={(e) => patch({ odds_closing: e.target.value === "closing" })}
                />
              </Field>

              <div>
                <p className="label-caps mb-1.5">
                  Bookmakers {config.odds_books.length === 0 ? "(todos)" : `(${config.odds_books.length})`}
                </p>
                {options.books.length === 0 ? (
                  <p className="text-[11px] text-ink-4">
                    Nenhum bookmaker indexado ainda.
                  </p>
                ) : (
                  <ul className="grid grid-cols-2 gap-1.5">
                    {options.books.map((book) => (
                      <li key={book}>
                        <CheckRow
                          checked={config.odds_books.includes(book)}
                          label={book}
                          onChange={() => toggle(config.odds_books, book, "odds_books")}
                        />
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-1.5 text-[11px] leading-relaxed text-ink-4">
                  Nenhum marcado = usa todas as casas do consenso. Marcar só{" "}
                  <span className="num">Pinnacle</span> é o teste mais duro: bater a
                  linha da Pinnacle é o padrão profissional.
                </p>
              </div>
            </div>
          ) : null}

          <OddsCacheSummary options={options} />
        </div>

        {/* -------------------------------------------------- competições */}
        <div className="flex flex-col gap-3">
          <p className="label-caps">Competições</p>
          <ul className="flex flex-col gap-1.5">
            {options.competitions.map((c) => (
              <li key={c}>
                <CheckRow
                  checked={
                    config.competitions.length === 0 || config.competitions.includes(c)
                  }
                  label={c}
                  onChange={() => toggle(config.competitions, c, "competitions")}
                />
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-ink-4">
            Nenhuma marcada = todas as competições do histórico.
          </p>

          <p className="label-caps mt-1">Mercados</p>
          <ul className="grid grid-cols-1 gap-1.5">
            {options.markets.map((m) => (
              <li key={m.key}>
                <CheckRow
                  checked={config.market_keys.includes(m.key)}
                  label={m.label}
                  onChange={() => toggle(config.market_keys, m.key, "market_keys")}
                />
              </li>
            ))}
          </ul>
        </div>

        {/* ------------------------------------------------- parâmetros */}
        <div className="flex flex-col gap-3">
          <p className="label-caps">Parâmetros do Scanner</p>

          <Field label="Banca virtual inicial" htmlFor="bt-bankroll">
            <Input
              id="bt-bankroll"
              mono
              inputMode="decimal"
              value={String(config.bankroll)}
              onChange={(e) =>
                patch({ bankroll: Number(e.target.value.replace(",", ".")) || 0 })
              }
            />
          </Field>

          <Field
            label="EV mínimo"
            htmlFor="bt-minev"
            hint="Mesmo filtro do Scanner: só entram sinais com EV ≥ este valor."
          >
            <Input
              id="bt-minev"
              mono
              suffix="%"
              inputMode="decimal"
              value={minEvPct}
              onChange={(e) => setMinEvPct(e.target.value)}
            />
          </Field>

          <Field
            label="Confiança mínima"
            htmlFor="bt-conf"
            hint="Classificação do sinal: FORTE ≥ 8% EV · MÉDIA ≥ 4,5% · FRACA ≥ 2%."
          >
            <Select
              id="bt-conf"
              options={CONFIDENCE_OPTIONS}
              value={config.min_confidence}
              onChange={(e) =>
                patch({
                  min_confidence: e.target.value as BacktestRequest["min_confidence"],
                })
              }
            />
          </Field>

          <Field label="Kelly fracionado" htmlFor="bt-kelly">
            <Select
              id="bt-kelly"
              options={KELLY_OPTIONS}
              value={config.kelly_fraction.toFixed(2)}
              onChange={(e) => patch({ kelly_fraction: Number(e.target.value) })}
            />
          </Field>

          <Field label="Risco por aposta" htmlFor="bt-cap">
            <Input
              id="bt-cap"
              mono
              suffix="%"
              inputMode="decimal"
              value={stakeCapPct}
              onChange={(e) => setStakeCapPct(e.target.value)}
            />
          </Field>

          <Field label="Exposição máxima / dia" htmlFor="bt-exp">
            <Select
              id="bt-exp"
              mono
              options={EXPOSURE_OPTIONS}
              value={config.max_exposure.toFixed(2)}
              onChange={(e) => patch({ max_exposure: Number(e.target.value) })}
            />
          </Field>

          <div className="flex flex-col gap-2 border-t border-line pt-3">
            <CheckRow
              checked={config.use_xg}
              label="Usar xG no ataque (blend 0.5)"
              onChange={(v) => patch({ use_xg: v })}
            />
            <CheckRow
              checked={config.apply_exposure_cap}
              label="Aplicar teto de exposição por dia"
              onChange={(v) => patch({ apply_exposure_cap: v })}
            />
          </div>

          <p className="mt-1 flex items-start gap-1.5 text-[11px] leading-relaxed text-ink-4">
            <InfoIcon className="mt-px size-3 shrink-0" />
            <span>
              Limiares ativos: FORTE {fmtPct(options.scanner_ev_thresholds.forte ?? 0.08)},
              MÉDIA {fmtPct(options.scanner_ev_thresholds.media ?? 0.045)}, FRACA{" "}
              {fmtPct(options.scanner_ev_thresholds.fraca ?? 0.02)}. Amostra mínima para
              conclusão: {fmtInt(options.min_sample)} sinais.
            </span>
          </p>
        </div>
      </div>
    </section>
  );
}

function CheckRow({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-[12.5px] transition-colors duration-150",
        checked
          ? "border-line-active/40 bg-accent-400/6 text-ink"
          : "border-line bg-surface-2 text-ink-3 hover:text-ink-2",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3.5 shrink-0 accent-[var(--color-accent-400)]"
      />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </label>
  );
}

/**
 * CorpusSummary — o que existe em cada corpus, com o fingerprint.
 *
 * O fingerprint muda quando os dados mudam: dois backtests com o mesmo
 * fingerprint são comparáveis diretamente; com fingerprints diferentes, a
 * comparação mistura efeito de código com efeito de dados.
 */
function CorpusSummary({ options }: { options: BacktestOptions }) {
  const imported = options.corpora.find((c) => c.key === "imported");
  const fduk = options.corpora.find((c) => c.key === "football_data_uk");
  if (!imported && !fduk) return null;

  return (
    <div className="flex flex-col gap-2">
      {fduk ? (
        <div
          className={cn(
            "rounded-md border px-2.5 py-2",
            fduk.available
              ? "border-pos-700/50 bg-pos-900/25"
              : "border-line bg-surface-2",
          )}
        >
          <p className="text-[11.5px] text-ink-2">
            <span className="label-caps me-1.5">Ligas europeias</span>
            {fduk.available
              ? `${fmtInt(fduk.n_matches)} partidas · ${fmtInt(fduk.competitions.length)} competições`
              : "indisponível"}
          </p>
          {fduk.available ? (
            <>
              <p className="mt-0.5 text-[11px] text-ink-4">
                {fduk.first_kickoff} → {fduk.last_kickoff} ·{" "}
                <span className="text-pos-400">COM odds reais</span>
              </p>
              {fduk.fingerprint ? (
                <p className="num mt-0.5 text-[10.5px] text-ink-4">
                  fingerprint {fduk.fingerprint}
                </p>
              ) : null}
            </>
          ) : (
            <p className="mt-0.5 text-[11px] leading-relaxed text-ink-4">{fduk.note}</p>
          )}
        </div>
      ) : null}

      {imported ? (
        <div className="rounded-md border border-line bg-surface-2 px-2.5 py-2">
          {imported.available ? (
            <>
              <p className="text-[11.5px] text-ink-2">
                <span className="label-caps me-1.5">API-Football</span>
                {fmtInt(imported.n_matches)} partidas ·{" "}
                {imported.first_kickoff} → {imported.last_kickoff}
              </p>
              <p className="mt-0.5 text-[11px] text-ink-4">
                {imported.competitions.join(", ")} · sem odds reais
              </p>
              {imported.fingerprint ? (
                <p className="num mt-0.5 text-[10.5px] text-ink-4">
                  fingerprint {imported.fingerprint}
                </p>
              ) : null}
            </>
          ) : (
            <p className="text-[11px] leading-relaxed text-ink-4">{imported.note}</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * OddsCacheSummary — quanto mercado real está disponível.
 *
 * Sem snapshots importados, a opção de odds reais não produz resultado: o
 * backtest falha explicitamente em vez de cair para o mercado sintético.
 */
function OddsCacheSummary({ options }: { options: BacktestOptions }) {
  const total = options.odds_cache.reduce((acc, c) => acc + c.snapshots, 0);
  return (
    <div className="rounded-md border border-line bg-surface-2 px-2.5 py-2">
      {total > 0 ? (
        <>
          <p className="text-[11.5px] text-ink-2">
            <span className="label-caps me-1.5">Cache de odds</span>
            {fmtInt(total)} snapshots reais
          </p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {options.odds_cache.map((c) => (
              <li key={c.sport_key} className="num text-[10.5px] text-ink-4">
                {c.sport_key}: {fmtInt(c.snapshots)} · {c.first?.slice(0, 10)} →{" "}
                {c.last?.slice(0, 10)}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-[11px] leading-relaxed text-ink-4">
          Nenhuma odd real importada. Para medir contra o mercado, defina{" "}
          <span className="num">BETGSN_ODDS_API_KEY</span> e rode{" "}
          <span className="num">python betgsn.py --import-odds</span>.
        </p>
      )}
    </div>
  );
}
