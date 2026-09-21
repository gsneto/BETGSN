"""BETGSN — preditor estatistico de futebol para apostas.

Modulos:
    engine    matematica de odds, valor, Kelly, arbitragem, multiplas
    model     forca de times, Poisson/Dixon-Coles, cantos e cartoes
    markets   probabilidades por mercado a partir da matriz de placar
    data      dataset local (times, historico sintetico, odds de exemplo)
    providers fontes reais opcionais (The Odds API, API-Football, Football-Data)
    signals   gerador de sinais e dicas ranqueadas por EV
    pipeline  orquestracao: dados -> modelo -> mercados -> sinais
    theme     design system: cor, fonte, espacamento e metricas
    widgets   componentes visuais reutilizaveis do dashboard
    gui       interface desktop (Tkinter)
"""

__version__ = "1.1.0"
__all__ = ["engine", "model", "markets", "data", "providers", "signals",
           "pipeline", "theme", "widgets", "gui"]
