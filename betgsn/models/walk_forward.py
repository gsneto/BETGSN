"""Janelas temporais sem shuffle, com seleção por disponibilidade dos dados.

LIMITACOES EMBUTIDAS (Fase D, documentadas — nao sao bugs):

- NAO existe gap/embargo ENTRE os blocos train/validation/test: os
  limites sao estritamente adjacentes. A protecao contra vazamento vem
  do embargo de publicacao de resultados (`temporal.result_time`,
  disponibilidade = inicio do dia do kickoff + 2 dias quando a fonte
  nao informa hora de publicacao), aplicado via `available_times` no
  `indices` — uma partida cujo resultado nao foi publicado antes do
  fim do bloco fica FORA daquele bloco.
- O criterio de janelas OOS do promotion gate (`n_windows`,
  `evidencia_oos_janelas`) NAO e alimentado pelas tools atuais
  (`tools/cross_season_gate.py`, `tools/quant_validation_report.py`
  chamam `evaluate_promotion` sem `n_windows`): o criterio passa como
  "nao declarado", nao bloqueante.

Nao criar gap artificial aqui sem evidencia de que o embargo de
publicacao e insuficiente — mudar a matematica exige medir primeiro.
"""
from dataclasses import dataclass
from datetime import timedelta
from ..timeutil import parse_kickoff


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: str
    train_end: str
    validation_end: str
    test_end: str


def windows(start, end, train_days=1095, validation_days=365, test_days=365, expanding=True):
    if min(train_days, validation_days, test_days) < 1:
        raise ValueError("janelas devem ser positivas")
    first, last = parse_kickoff(start), parse_kickoff(end)
    train_end = first + timedelta(days=train_days)
    while train_end + timedelta(days=validation_days) < last:
        lower = first if expanding else train_end-timedelta(days=train_days)
        val_end = train_end + timedelta(days=validation_days)
        test_end = min(last, val_end + timedelta(days=test_days))
        yield WalkForwardWindow(*(d.isoformat() for d in (lower, train_end, val_end, test_end)))
        train_end += timedelta(days=test_days)


def indices(window, kickoff_times, available_times):
    if len(kickoff_times) != len(available_times):
        raise ValueError("timestamps desalinhados")
    bounds = list(map(parse_kickoff, (window.train_start,window.train_end,window.validation_end,window.test_end)))
    result = [[],[],[]]
    for i,(kickoff,available) in enumerate(zip(kickoff_times,available_times)):
        k,a=parse_kickoff(kickoff),parse_kickoff(available)
        for j in range(3):
            if bounds[j] <= k < bounds[j+1] and a < bounds[j+1]:
                result[j].append(i)
    return tuple(result)
