/**
 * Testes do hook `useBacktestJob`.
 *
 * O backtest roda inteiro no backend; aqui so se acompanha o progresso.
 * Contrato: dispara, faz polling, e ao terminar avisa quem chamou. Em erro,
 * para o polling e expoe a mensagem.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as backtestApi from "@/api/backtest";
import { useBacktestJob } from "@/hooks/useBacktestJob";
import { backtestOptions, backtestJobStatus } from "@/test/fixtures";

vi.mock("@/api/backtest", () => ({
  startBacktest: vi.fn(),
  fetchBacktestStatus: vi.fn(),
}));

const request = backtestOptions.default_config;

/** Status idle para a consulta de reattach feita no mount do hook. */
const idleStatus = {
  job_id: null,
  phase: "idle" as const,
  done: 0,
  total: 0,
  progress: 0,
  message: "Pronto.",
  run_id: null,
  error: null,
};

describe("useBacktestJob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // por padrao o mount consulta o status e encontra nada em execucao
    vi.mocked(backtestApi.fetchBacktestStatus).mockResolvedValue(idleStatus);
  });

  afterEach(() => {
    vi.mocked(backtestApi.fetchBacktestStatus).mockReset();
    vi.mocked(backtestApi.startBacktest).mockReset();
    vi.useRealTimers();
  });

  it("comeca idle", () => {
    const { result } = renderHook(() => useBacktestJob());
    expect(result.current.status.phase).toBe("idle");
    expect(result.current.running).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("dispara o backtest e acompanha ate concluir", async () => {
    const onFinished = vi.fn();
    vi.mocked(backtestApi.startBacktest).mockResolvedValue({
      ...backtestJobStatus,
      phase: "preparing",
      done: 0,
      total: 0,
      progress: 0,
    });
    vi.mocked(backtestApi.fetchBacktestStatus)
      .mockResolvedValueOnce(idleStatus) // consulta de reattach no mount
      .mockResolvedValueOnce(backtestJobStatus)
      .mockResolvedValueOnce({
        ...backtestJobStatus,
        phase: "done",
        progress: 1,
        run_id: "bt_1",
        message: "Concluído",
      });

    const { result } = renderHook(() => useBacktestJob(onFinished));

    await act(async () => {
      await result.current.run(request);
    });
    expect(backtestApi.startBacktest).toHaveBeenCalledWith(request);

    // primeiro poll -> analyzing
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(result.current.running).toBe(true);
    expect(result.current.status.phase).toBe("analyzing");

    // segundo poll -> done
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(result.current.status.phase).toBe("done");
    expect(result.current.running).toBe(false);
    expect(onFinished).toHaveBeenCalledWith("bt_1");
  });

  it("para o polling e expoe o erro quando a execucao falha", async () => {
    vi.mocked(backtestApi.startBacktest).mockResolvedValue({
      ...backtestJobStatus,
      phase: "preparing",
      done: 0,
      total: 0,
    });
    vi.mocked(backtestApi.fetchBacktestStatus).mockResolvedValue({
      ...backtestJobStatus,
      phase: "error",
      error: "ValueError: mercado desconhecido",
      message: "Configuração inválida.",
    });

    const { result } = renderHook(() => useBacktestJob());
    await act(async () => {
      await result.current.run(request);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });

    expect(result.current.error).toBe("ValueError: mercado desconhecido");
    expect(result.current.running).toBe(false);

    // nao continua consultando depois do erro
    const callsAfterError = vi.mocked(backtestApi.fetchBacktestStatus).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(vi.mocked(backtestApi.fetchBacktestStatus).mock.calls.length).toBe(
      callsAfterError,
    );
  });

  it("reporta falha ao iniciar", async () => {
    const { ApiError } = await import("@/api/client");
    vi.mocked(backtestApi.startBacktest).mockRejectedValue(
      new ApiError(409, "ja existe um backtest em execucao"),
    );

    const { result } = renderHook(() => useBacktestJob());
    await act(async () => {
      await result.current.run(request);
    });

    expect(result.current.error).toBe("ja existe um backtest em execucao");
    expect(result.current.starting).toBe(false);
  });

  it("reset limpa o estado", async () => {
    vi.mocked(backtestApi.startBacktest).mockResolvedValue({
      ...backtestJobStatus,
      phase: "preparing",
      done: 0,
      total: 0,
    });
    const { result } = renderHook(() => useBacktestJob());
    await act(async () => {
      await result.current.run(request);
    });
    expect(result.current.status.phase).toBe("preparing");

    act(() => {
      result.current.reset();
    });
    expect(result.current.status.phase).toBe("idle");
    expect(result.current.error).toBeNull();
  });

  it("nao consulta o backend apos desmontar", async () => {
    vi.mocked(backtestApi.startBacktest).mockResolvedValue({
      ...backtestJobStatus,
      phase: "preparing",
      done: 0,
      total: 0,
    });
    vi.mocked(backtestApi.fetchBacktestStatus).mockResolvedValue(backtestJobStatus);

    const { result, unmount } = renderHook(() => useBacktestJob());
    await act(async () => {
      await result.current.run(request);
    });
    const before = vi.mocked(backtestApi.fetchBacktestStatus).mock.calls.length;

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(vi.mocked(backtestApi.fetchBacktestStatus).mock.calls.length).toBe(before);
  });

  it("reanexa um job em execucao no mount (troca de aba e volta)", async () => {
    // o backend ja esta rodando: o hook adota o estado e continua o
    // polling em vez de fingir IDLE
    vi.mocked(backtestApi.fetchBacktestStatus)
      .mockResolvedValueOnce({ ...backtestJobStatus, phase: "preparing", progress: 0.1 })
      .mockResolvedValueOnce(backtestJobStatus)
      .mockResolvedValueOnce({
        ...backtestJobStatus,
        phase: "done",
        progress: 1,
        run_id: "bt_reattach",
        message: "Concluído",
      });

    const onFinished = vi.fn();
    const { result } = renderHook(() => useBacktestJob(onFinished));
    // deixa a consulta de reattach do mount resolver
    await act(async () => {});

    // o mount adotou o job sem nenhum startBacktest
    expect(backtestApi.startBacktest).not.toHaveBeenCalled();
    expect(result.current.running).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(result.current.status.phase).toBe("analyzing");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(result.current.status.phase).toBe("done");
    expect(onFinished).toHaveBeenCalledWith("bt_reattach");
  });
});
