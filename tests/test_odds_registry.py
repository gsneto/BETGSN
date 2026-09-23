"""Testes do registry de providers de odds (FASE B — Task B.2).

Cada teste constroi um registry NOVO (padrao `_fresh_registry` da FASE
A): o cache de `lookup` e por instancia, e o teste nao pode depender
de estado de outro teste. O pin de compatibilidade no final garante
que `providers.configured_odds_providers` — agora um delegado do
registry — entrega exatamente a lista de antes (nomes, ordem e
instancias), para que `OddsService.from_env` continue identico.
"""
from __future__ import annotations

import pytest

from betgsn.odds_registry import (
    OddsProviderRegistry,
    ProviderSpec,
    default_odds_registry,
)
from betgsn.providers import (
    OddsApiProvider,
    ParlayApiProvider,
    configured_odds_providers,
)


class FakeOddsProvider:
    """Provider falso de TESTE — so o que o registry observa: um objeto."""

    def __init__(self, name: str) -> None:
        self.name = name


def _spec(
    name: str,
    factory,
    priority: int = 100,
    features: tuple[str, ...] = (),
) -> ProviderSpec:
    return ProviderSpec(
        name=name, factory=factory, priority=priority, features=features
    )


def _fresh_registry() -> OddsProviderRegistry:
    """Registry novo com tres providers fake, prioridades embaralhadas."""
    registry = OddsProviderRegistry()
    registry.register(
        _spec("gamma", lambda: FakeOddsProvider("gamma"), priority=30)
    )
    registry.register(
        _spec("alpha", lambda: FakeOddsProvider("alpha"), priority=10)
    )
    registry.register(
        _spec("beta", lambda: FakeOddsProvider("beta"), priority=20)
    )
    return registry


# ==========================================================================
# Registry vazio
# ==========================================================================


def test_empty_registry_has_no_names_and_no_providers():
    registry = OddsProviderRegistry()
    assert registry.names() == []
    assert registry.available_providers() == []


# ==========================================================================
# Registro, ordem e duplicatas
# ==========================================================================


def test_specs_ordered_by_priority_then_name():
    registry = OddsProviderRegistry()
    # mesma prioridade: desempate pelo nome (deterministico)
    registry.register(_spec("zeta", lambda: None, priority=10))
    registry.register(_spec("beta", lambda: None, priority=10))
    registry.register(_spec("alpha", lambda: None, priority=1))
    assert [s.name for s in registry.specs()] == ["alpha", "beta", "zeta"]
    assert registry.names() == ["alpha", "beta", "zeta"]


def test_register_duplicate_name_raises_valueerror():
    registry = OddsProviderRegistry()
    registry.register(_spec("dup", lambda: FakeOddsProvider("dup")))
    with pytest.raises(ValueError):
        registry.register(_spec("dup", lambda: FakeOddsProvider("dup")))


def test_metadata_returns_registered_spec():
    registry = _fresh_registry()
    spec = registry.metadata("alpha")
    assert isinstance(spec, ProviderSpec)
    assert spec.name == "alpha"
    assert spec.priority == 10
    assert callable(spec.factory)


def test_metadata_unknown_name_raises_keyerror():
    registry = _fresh_registry()
    with pytest.raises(KeyError):
        registry.metadata("nao-registrado")


# ==========================================================================
# lookup: construcao, cache e ausencia
# ==========================================================================


def test_lookup_unknown_name_raises_keyerror():
    registry = _fresh_registry()
    with pytest.raises(KeyError):
        registry.lookup("nao-registrado")


def test_lookup_builds_once_and_caches():
    registry = OddsProviderRegistry()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return FakeOddsProvider("alpha")

    registry.register(_spec("alpha", factory))
    first = registry.lookup("alpha")
    second = registry.lookup("alpha")
    assert isinstance(first, FakeOddsProvider)
    assert first is second
    assert calls["n"] == 1


def test_factory_returning_none_means_not_configured():
    registry = _fresh_registry()
    registry.register(_spec("vazio", lambda: None, priority=5))

    available = registry.available_providers()
    assert [name for name, _ in available] == ["alpha", "beta", "gamma"]
    assert registry.lookup("vazio") is None


# ==========================================================================
# available_providers: so configurados, em ordem de prioridade
# ==========================================================================


def test_available_providers_preserves_priority_order():
    registry = _fresh_registry()
    pairs = registry.available_providers()
    assert [name for name, _ in pairs] == ["alpha", "beta", "gamma"]
    for name, provider in pairs:
        assert provider.name == name
    assert all(isinstance(p, FakeOddsProvider) for _, p in pairs)


# ==========================================================================
# Registry padrao + pin de compatibilidade com providers.py
# ==========================================================================


#: Variaveis de TODOS os providers de odds do registry padrao. Os testes
#: abaixo controlam explicitamente quem esta configurado — sem limpar as
#: chaves dos providers novos, um .env real da maquina invalidaria as
#: premissas (hermeticidade).
_ALL_ODDS_ENV_VARS = (
    "BETGSN_ODDS_API_KEY",
    "BETGSN_PARLAY_API_KEY",
    "BETGSN_PARLAY_API_BASE",
    "BETGSN_ODDSPAPI_API_KEY",
    "ODDSPAPI_API_KEY",
    "BETGSN_ODDS_API_IO_KEY",
    "ODDS_API_IO_KEY",
    "BETGSN_OPTICODDS_API_KEY",
    "OPTICODDS_API_KEY",
)


def test_default_registry_unconfigured_env_excludes_everything(monkeypatch):
    for var in _ALL_ODDS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert default_odds_registry().available_providers() == []


def test_default_registry_order_and_types(monkeypatch):
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay.invalid/api/")
    for var in _ALL_ODDS_ENV_VARS[3:]:
        monkeypatch.delenv(var, raising=False)

    pairs = default_odds_registry().available_providers()
    assert [name for name, _ in pairs] == ["The Odds API", "ParlayAPI"]
    assert isinstance(pairs[0][1], OddsApiProvider)
    assert isinstance(pairs[1][1], ParlayApiProvider)

    # registro novo le o ambiente na hora: sem chave, nada fica disponivel
    for var in _ALL_ODDS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert [name for name, _ in default_odds_registry().available_providers()] == []


def test_configured_odds_providers_matches_default_registry(monkeypatch):
    """Pin de compatibilidade: o delegado devolve a mesma lista do registry."""
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay.invalid/api/")
    for var in _ALL_ODDS_ENV_VARS[3:]:
        monkeypatch.delenv(var, raising=False)

    pairs = configured_odds_providers()
    assert [name for name, _ in pairs] == ["The Odds API", "ParlayAPI"]
    assert isinstance(pairs[0][1], OddsApiProvider)
    assert isinstance(pairs[1][1], ParlayApiProvider)
