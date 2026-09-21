/**
 * ControlBar — parametros do modelo.
 *
 * Paridade com a ParameterBar da GUI Tkinter legada (betgsn/gui.py):
 * BANCA, KELLY FRAC., EV MIN., RISCO/APOSTA e USAR xG. Mesmos campos,
 * mesmos significados, mesma unidade. O que muda e o acabamento.
 *
 * Os valores em percentual sao editados em % (como na GUI antiga) e
 * convertidos para fracao ao enviar, porque o contrato da API usa fracao.
 * Isso e conversao de unidade, nao calculo estatistico.
 */

import { useEffect, useState } from "react";
import Button from "@/components/ui/Button";
import { Field, Input, Select, Switch } from "@/components/ui/Input";
import { Tooltip } from "@/components/ui/States";
import { InfoIcon } from "@/components/ui/icons";
import { useStore } from "@/store/context";
import { fmtPct } from "@/utils/format";

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

export default function ControlBar() {
  const { config, setConfig, recalculate, recalculating, summary } = useStore();

  // campos numericos ficam em estado local de texto para permitir edicao
  // parcial ("1.0") sem que o parse intermediario zere o valor
  const [bankroll, setBankroll] = useState(String(config.bankroll));
  const [minEv, setMinEv] = useState((config.min_ev * 100).toFixed(1));
  const [stakeCap, setStakeCap] = useState((config.stake_cap * 100).toFixed(2));

  // ressincroniza quando o backend devolve a configuracao efetiva
  useEffect(() => {
    setBankroll(String(config.bankroll));
    setMinEv((config.min_ev * 100).toFixed(1));
    setStakeCap((config.stake_cap * 100).toFixed(2));
  }, [config.bankroll, config.min_ev, config.stake_cap]);

  const commit = () => {
    const b = Number(bankroll.replace(",", "."));
    const ev = Number(minEv.replace(",", ".")) / 100;
    const cap = Number(stakeCap.replace(",", ".")) / 100;
    setConfig({
      bankroll: Number.isFinite(b) && b > 0 ? b : config.bankroll,
      min_ev: Number.isFinite(ev) && ev >= 0 ? ev : config.min_ev,
      stake_cap: Number.isFinite(cap) && cap > 0 ? cap : config.stake_cap,
    });
  };

  const applyAndRun = () => {
    const b = Number(bankroll.replace(",", "."));
    const ev = Number(minEv.replace(",", ".")) / 100;
    const cap = Number(stakeCap.replace(",", ".")) / 100;
    void recalculate({
      bankroll: Number.isFinite(b) && b > 0 ? b : config.bankroll,
      min_ev: Number.isFinite(ev) && ev >= 0 ? ev : config.min_ev,
      stake_cap: Number.isFinite(cap) && cap > 0 ? cap : config.stake_cap,
    });
  };

  const onEnter = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") applyAndRun();
  };

  return (
    <section className="flex flex-wrap items-end gap-x-4 gap-y-3 border-b border-line bg-surface-1/60 px-4 py-2.5">
      <Field
        label="Banca"
        htmlFor="ctl-bankroll"
        hint="Banca atual. As stakes são calculadas como percentual dela."
        className="w-[120px]"
      >
        <Input
          id="ctl-bankroll"
          mono
          inputMode="decimal"
          value={bankroll}
          onChange={(e) => setBankroll(e.target.value)}
          onBlur={commit}
          onKeyDown={onEnter}
        />
      </Field>

      <Field
        label="Kelly frac."
        htmlFor="ctl-kelly"
        hint="Fração de Kelly aplicada ao stake."
        className="w-[196px]"
      >
        <Select
          id="ctl-kelly"
          options={KELLY_OPTIONS}
          value={config.kelly_fraction.toFixed(2)}
          onChange={(e) => setConfig({ kelly_fraction: Number(e.target.value) })}
        />
      </Field>

      <Field
        label="EV mín."
        htmlFor="ctl-minev"
        hint="EV mínimo para um sinal entrar na lista."
        className="w-[96px]"
      >
        <Input
          id="ctl-minev"
          mono
          suffix="%"
          inputMode="decimal"
          value={minEv}
          onChange={(e) => setMinEv(e.target.value)}
          onBlur={commit}
          onKeyDown={onEnter}
        />
      </Field>

      <Field
        label="Risco/aposta"
        htmlFor="ctl-cap"
        hint="Teto por aposta, em % da banca. Banca 1.000 e 1% → máximo 10."
        className="w-[104px]"
      >
        <Input
          id="ctl-cap"
          mono
          suffix="%"
          inputMode="decimal"
          value={stakeCap}
          onChange={(e) => setStakeCap(e.target.value)}
          onBlur={commit}
          onKeyDown={onEnter}
        />
      </Field>

      <Field
        label="Exposição máx."
        htmlFor="ctl-exposure"
        hint="Teto da soma de todas as stakes, em % da banca."
        className="w-[96px]"
      >
        <Select
          id="ctl-exposure"
          mono
          options={EXPOSURE_OPTIONS}
          value={config.max_exposure.toFixed(2)}
          onChange={(e) => setConfig({ max_exposure: Number(e.target.value) })}
        />
      </Field>

      <div className="flex flex-col gap-1">
        <span className="label-caps inline-flex items-center gap-1">
          Usar xG
          <Tooltip content="Mistura xG aos gols no cálculo dos lambdas (blend 0.5). xG é mais estável que gol puro.">
            <InfoIcon className="size-3 text-ink-4" />
          </Tooltip>
        </span>
        <div className="flex h-[34px] items-center">
          <Switch
            id="ctl-xg"
            label="Usar xG no modelo"
            checked={config.use_xg}
            onChange={(next) => setConfig({ use_xg: next })}
          />
        </div>
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-3">
        {summary ? (
          <Tooltip content="Exposição total das stakes sobre a banca, já limitada pelo teto configurado.">
            <span className="hidden flex-col items-end lg:flex">
              <span className="label-caps leading-none">Exposição</span>
              <span className="num mt-1 text-body leading-none font-semibold text-ink">
                {fmtPct(summary.kpis.total_exposure_pct)}
              </span>
            </span>
          </Tooltip>
        ) : null}
        <Button
          variant="outline"
          size="md"
          loading={recalculating}
          onClick={applyAndRun}
        >
          Aplicar parâmetros
        </Button>
      </div>
    </section>
  );
}
