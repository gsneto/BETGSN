from betgsn.models.walk_forward import windows, indices


def test_expanding_fixes_train_start_rolling_advances_and_unpublished_results_drop():
    """O que estas janelas de fato protegem (nome anterior prometia um
    embargo ENTRE blocos que nao existe — o harness nao tem gap; ver
    docstring de `betgsn.models.walk_forward`):

    - expanding mantem `train_start` fixo; rolling avanca com o tempo;
    - uma partida cujo resultado NAO foi publicado antes do fim do
      bloco fica de fora daquele bloco (selecao por disponibilidade,
      `available_times` no `indices`).
    """
    expanding=list(windows("2020-01-01","2025-01-01",365,180,180))
    rolling=list(windows("2020-01-01","2025-01-01",365,180,180,False))
    assert expanding[0].train_start == expanding[1].train_start
    assert rolling[1].train_start > rolling[0].train_start
    window=expanding[0]
    train,val,test=indices(window,["2020-12-29","2020-12-30"],["2020-12-30","2022-01-01"])
    assert train == [0]
    assert val == test == []
