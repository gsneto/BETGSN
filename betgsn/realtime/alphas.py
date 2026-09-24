"""BETGSN :: realtime.alphas — registro de alphas do terminal em tempo real.

Um alpha e uma HIPOTESE documentada sobre de onde vem informacao no
mercado — nao e uma estrategia de aposta. Alphas registram: o que
esperam observar, de que dado dependem, como geram sinal e qual o
estado de validacao (nenhuma promessa, nenhum marketing).

Nenhuma alpha e obrigatoria a gerar aposta: no BETGSN o output e
SINAL INFORMATIVO. A elegibilidade de producao continua decidida pelo
promotion gate (bloqueado: NO BET).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class AlphaSpec:
    id: str
    name: str
    version: str
    status: str
    hypothesis: str
    required_data: Tuple[str, ...]
    signal_types: Tuple[str, ...]
    explanation: str

    def to_dict(self) -> dict:
        return {
            "alpha_id": self.id,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "hypothesis": self.hypothesis,
            "required_data": list(self.required_data),
            "signal_types": list(self.signal_types),
            "explanation": self.explanation,
        }


#: Tipos de sinal que o engine emite (Fase 11/12).
SIGNAL_STALE_PRICE = "STALE_PRICE"
SIGNAL_BOOKMAKER_OUTLIER = "BOOKMAKER_OUTLIER"
SIGNAL_CONSENSUS_MOVE = "CONSENSUS_MOVE"
SIGNAL_BOOKMAKER_LEAD = "BOOKMAKER_LEAD"
SIGNAL_BOOKMAKER_LAG = "BOOKMAKER_LAG"
SIGNAL_RAPID_CONVERGENCE = "RAPID_CONVERGENCE"
SIGNAL_DISPERSION_SPIKE = "DISPERSION_SPIKE"
SIGNAL_PRICE_REVERSAL = "PRICE_REVERSAL"
SIGNAL_BEST_PRICE_GAP = "BEST_PRICE_GAP"

#: Todos os tipos emitidos pelo signal engine (para validacao de nome).
SIGNAL_TYPES: Tuple[str, ...] = (
    SIGNAL_STALE_PRICE,
    SIGNAL_BOOKMAKER_OUTLIER,
    SIGNAL_CONSENSUS_MOVE,
    SIGNAL_BOOKMAKER_LEAD,
    SIGNAL_BOOKMAKER_LAG,
    SIGNAL_RAPID_CONVERGENCE,
    SIGNAL_DISPERSION_SPIKE,
    SIGNAL_PRICE_REVERSAL,
    SIGNAL_BEST_PRICE_GAP,
)

_BOOKMAKER_MICROSTRUCTURE = AlphaSpec(
    id="bookmaker_microstructure",
    name="Bookmaker Microstructure",
    version="1.0",
    status="INFORMATIONAL",
    hypothesis=(
        "Casas diferentes atualizam em ritmos diferentes: quem move "
        "primeiro (lead) e quem continua cotando preco antigo (lag/outlier/"
        "stale) revela a estrutura do mercado."
    ),
    required_data=("odds por casa", "timestamp por quote", "freshness"),
    signal_types=(
        SIGNAL_BOOKMAKER_OUTLIER,
        SIGNAL_BOOKMAKER_LEAD,
        SIGNAL_BOOKMAKER_LAG,
        SIGNAL_STALE_PRICE,
    ),
    explanation=(
        "Compara cada casa contra a mediana das DEMAIS casas no mesmo "
        "instante. Nao ha nocao de 'preco certo': o sinal aponta "
        "assimetria observavel e explica quem, para onde e quando."
    ),
)

_MOVEMENT_ALPHA = AlphaSpec(
    id="movement",
    name="Movement & Divergence",
    version="1.0",
    status="INFORMATIONAL",
    hypothesis=(
        "Quando varias casas movem na mesma direcao num intervalo curto, "
        "o mercado esta reprecificando; reversoes e convergencias rapidas "
        "sao observaveis e mensuraveis."
    ),
    required_data=("historico de precos por linha", "timestamps reais"),
    signal_types=(
        SIGNAL_CONSENSUS_MOVE,
        SIGNAL_PRICE_REVERSAL,
        SIGNAL_RAPID_CONVERGENCE,
        SIGNAL_DISPERSION_SPIKE,
    ),
    explanation=(
        "Contabiliza movimentos POR CASA com direcao e magnitude; "
        "'consenso' exige N casas minimas na mesma direcao na mesma "
        "janela — nunca uma casa so."
    ),
)

_LINE_SHOPPING_ALPHA = AlphaSpec(
    id="line_shopping",
    name="Line Shopping",
    version="1.0",
    status="INFORMATIONAL",
    hypothesis=(
        "A diferenca entre a melhor odd e a mediana do mercado e ganho "
        "de preco puro, obtido sem prever nada."
    ),
    required_data=("odds por casa", "mediana entre casas"),
    signal_types=(SIGNAL_BEST_PRICE_GAP,),
    explanation=(
        "Nao gera recomendacao: mostra QUEM oferece o melhor preco, o "
        "tamanho do gap contra a mediana e a idade das quotes envolvidas."
    ),
)

_MARKET_RESIDUAL_ALPHA = AlphaSpec(
    id="market_residual",
    name="Model minus Market (Residual)",
    version="1.0",
    status="EXPERIMENTAL",
    hypothesis=(
        "A diferenca entre preco de modelo e probabilidade justa do "
        "mercado e informacao quantitativa — NAO recomendacao de aposta."
    ),
    required_data=("snapshot do modelo", "market fair (devig)"),
    signal_types=(),
    explanation=(
        "O terminal apresenta MARKET, MODEL e DIFFERENCE separados. A "
        "evidencia OOS atual (Etapa 19) mostra que os modelos "
        "experimentais NAO superam o mercado: o residual e mostrado "
        "como informacao, nunca como edge validado."
    ),
)

_LINEUP_SHOCK_ALPHA = AlphaSpec(
    id="lineup_shock",
    name="Lineup Shock",
    version="0.1",
    status="REGISTERED_NO_GENERATOR",
    hypothesis=(
        "Escalacoes inesperadas proximas do kickoff reprecificam o "
        "mercado antes dos modelos locais."
    ),
    required_data=("escalacoes point-in-time", "odds em janela curta"),
    signal_types=(),
    explanation=(
        "Alpha registrada SEM gerador em tempo real: a operacao atual "
        "nao possui fonte de escalacao PIT. Nenhum sinal e fabricado."
    ),
)

_ALTERNATIVE_MARKET_ALPHA = AlphaSpec(
    id="alternative_market",
    name="Alternative Market",
    version="0.1",
    status="REGISTERED_NO_GENERATOR",
    hypothesis=(
        "Mercados secundarios (cantos, cartoes, BTTS) podem divergir do "
        "1X2 em momentos especificos."
    ),
    required_data=("odds de mercados alternativos", "modelo por mercado"),
    signal_types=(),
    explanation=(
        "Alpha registrada SEM gerador em tempo real no terminal: a "
        "cobertura capturada hoje e 1X2/totais/BTTS e nenhum modelo "
        "validado existe para esses mercados ao vivo."
    ),
)


def default_alphas() -> dict[str, AlphaSpec]:
    """Registro canonico de alphas do terminal em tempo real."""
    return {
        alpha.id: alpha
        for alpha in (
            _BOOKMAKER_MICROSTRUCTURE,
            _MOVEMENT_ALPHA,
            _LINE_SHOPPING_ALPHA,
            _MARKET_RESIDUAL_ALPHA,
            _LINEUP_SHOCK_ALPHA,
            _ALTERNATIVE_MARKET_ALPHA,
        )
    }
