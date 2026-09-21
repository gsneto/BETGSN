"""BETGSN :: gui — dashboard desktop do preditor de apostas.

A interface e uma composicao de componentes (betgsn.widgets) sobre um
design system unico (betgsn.theme). Nenhuma regra de negocio vive aqui:
o calculo continua em engine/model/markets/signals/pipeline e a GUI so
apresenta o RunResult.

Estrutura visual:
    HeaderBar       marca, status de sinais e botao RECALCULAR
    ParameterBar    banca, kelly, EV minimo, risco por aposta, toggle xG
    TabBar          SINAIS / JOGOS / CASAS-ODDS / ESTATISTICAS / MODELO
    KpiStrip        contagem de sinais, lucro esperado e stake medio
    FilterBar       busca instantanea + chips de confianca + contagem
    SignalsTable    grade quantitativa com cabecalho fixo e detalhe expansivel
    InsightsPanel   top oportunidades segundo o modelo
    StatusBar       resumo de exposicao e lucro
    Toast           feedback discreto de recalculo
"""

from __future__ import annotations

import threading
import tkinter as tk
from statistics import mean

from . import theme
from .engine import scan_arbitrage
from .pipeline import FixtureAnalysis, RunResult, run
from .signals import Confidence, Signal
from .widgets import (
    AccentButton,
    Cell,
    Chip,
    ChipBar,
    Column,
    DataGrid,
    InsightItem,
    InsightsPanel,
    KpiItem,
    KpiStrip,
    SearchField,
    SelectField,
    TabBar,
    Toast,
    ToggleSwitch,
    Tooltip,
    ttk_scrollbar,
)

CONF_ORDER = {"FORTE": 3, "MEDIA": 2, "FRACA": 1, "DESCARTE": 0}
CONF_LABEL = {"FORTE": "FORTE", "MEDIA": "MÉDIA", "FRACA": "FRACA"}


class BetgsnApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        theme.init(self)
        self.title("BETGSN — Preditor Estatístico de Futebol")
        self.geometry("1440x900")
        self.minsize(1180, 700)
        self.configure(bg=theme.COLORS["bg"])

        self.result: RunResult | None = None
        self._analysis_by_match: dict[str, FixtureAnalysis] = {}
        self._max_ev = 0.0
        self._insight_signals: list[Signal] = []
        self._loading = False

        self.bankroll_var = tk.DoubleVar(value=1000.0)
        self.kelly_var = tk.StringVar(value="0.25")
        self.min_ev_var = tk.DoubleVar(value=2.0)
        self.stake_cap_var = tk.DoubleVar(value=1.0)
        self.xg_var = tk.BooleanVar(value=True)
        self.filter_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Pronto.")
        self.exposure_var = tk.StringVar(value="")
        self.header_status_var = tk.StringVar(value="aguardando cálculo")
        self.count_var = tk.StringVar(value="0 sinais")

        self._build_header()
        self._build_param_bar()
        self._build_tabbar()
        self._build_pages()
        self._build_statusbar()
        self.toast = Toast(self)

        self.filter_var.trace_add("write", lambda *_: self._refresh_signals_view())
        self._show_page("signals")
        self.after(250, self.recalculate)

    # ================================================================= header

    def _build_header(self) -> None:
        c = theme.COLORS
        bar = tk.Frame(self, bg=c["surface_1"], height=theme.DIMS["header"])
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self, bg=c["border"], height=1).pack(side="top", fill="x")

        left = tk.Frame(bar, bg=c["surface_1"])
        left.pack(side="left", fill="y", padx=(16, 0))
        logo = tk.Canvas(left, width=30, height=30, bg=c["surface_1"],
                         highlightthickness=0, bd=0)
        logo.pack(side="left", pady=18)
        theme.rounded_rect(logo, 1, 1, 29, 29, theme.RADIUS["md"],
                           fill=c["accent_wash"], outline=c["accent_dim"])
        logo.create_text(15, 15, text="B", fill=c["accent"],
                         font=theme.FONTS["h1"])

        titles = tk.Frame(left, bg=c["surface_1"])
        titles.pack(side="left", padx=(10, 0), pady=12)
        tk.Label(titles, text="BETGSN", bg=c["surface_1"], fg=c["text"],
                 font=theme.FONTS["brand"]).pack(anchor="w")
        tk.Label(titles,
                 text="Preditor estatístico — Poisson/Dixon-Coles + consenso de casas",
                 bg=c["surface_1"], fg=c["text_muted"],
                 font=theme.FONTS["ui_sm"]).pack(anchor="w")

        right = tk.Frame(bar, bg=c["surface_1"])
        right.pack(side="right", fill="y", padx=16)
        status = tk.Frame(right, bg=c["surface_1"])
        status.pack(side="left", pady=18, padx=(0, 16))
        self._status_dot = tk.Label(status, text="●", bg=c["surface_1"],
                                    fg=c["text_dim"], font=theme.FONTS["ui_sm"])
        self._status_dot.pack(side="left", padx=(0, 5))
        tk.Label(status, textvariable=self.header_status_var, bg=c["surface_1"],
                 fg=c["text_muted"], font=theme.FONTS["ui_sm"]).pack(side="left")

        self.recalc_btn = AccentButton(right, "RECALCULAR", self.recalculate, kind="primary")
        self.recalc_btn.pack(side="left", pady=17)
        Tooltip(self.recalc_btn,
                "Recalcula ratings, mercados e sinais com os parâmetros atuais.\n"
                "Atalho: F5")

        self.bind("<F5>", lambda e: self.recalculate())

    # ============================================================ parametros

    def _entry(self, parent: tk.Misc, var: tk.Variable, width: int) -> tk.Entry:
        c = theme.COLORS
        return tk.Entry(
            parent, textvariable=var, width=width, justify="left",
            bg=c["surface_2"], fg=c["text"], insertbackground=c["accent"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=c["border"], highlightcolor=c["accent_dim"],
            font=theme.FONTS["data"],
        )

    def _param_block(self, parent: tk.Misc, label: str, widget: tk.Misc,
                     tooltip: str = "") -> tk.Frame:
        c = theme.COLORS
        block = tk.Frame(parent, bg=c["surface_1"])
        lab = tk.Label(block, text=label, bg=c["surface_1"], fg=c["text_dim"],
                       font=theme.FONTS["label"])
        lab.pack(anchor="w")
        widget.pack(anchor="w", pady=(3, 0))
        if tooltip:
            Tooltip(lab, tooltip)
        return block

    def _separator(self, parent: tk.Misc) -> tk.Frame:
        c = theme.COLORS
        sep = tk.Frame(parent, bg=c["surface_1"])
        tk.Frame(sep, bg=c["border_soft"], width=1, height=30).pack(pady=14)
        return sep

    def _build_param_bar(self) -> None:
        c = theme.COLORS
        bar = tk.Frame(self, bg=c["surface_1"], height=theme.DIMS["param_bar"])
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self, bg=c["border"], height=1).pack(side="top", fill="x")

        row = tk.Frame(bar, bg=c["surface_1"])
        row.pack(side="left", fill="y", padx=16)

        banca = self._entry(row, self.bankroll_var, 10)
        self._param_block(row, "BANCA", banca,
                          "Banca atual. As stakes são calculadas como percentual dela.").pack(
            side="left", pady=8)

        row_sep = self._separator(row)
        row_sep.pack(side="left", padx=14)

        kelly = SelectField(
            row, self.kelly_var,
            ("0.10", "0.25", "0.33", "0.50", "1.00"),
            width=76, fmt=lambda v: f"{float(v):.2f}",
        )
        self._param_block(row, "KELLY FRAC.", kelly,
                          "Fração de Kelly aplicada. 0.25 = quarter-Kelly (conservador).").pack(
            side="left", pady=8)

        row_sep2 = self._separator(row)
        row_sep2.pack(side="left", padx=14)

        min_ev = self._entry(row, self.min_ev_var, 5)
        self._param_block(row, "EV MÍN.", min_ev,
                          "EV mínimo (em %) para um sinal entrar na lista.").pack(
            side="left", pady=8)

        row_sep3 = self._separator(row)
        row_sep3.pack(side="left", padx=14)

        cap = self._entry(row, self.stake_cap_var, 5)
        self._param_block(row, "RISCO/APOSTA", cap,
                          "Teto de risco por aposta, em % da banca.\n"
                          "Ex.: banca 1.000 e risco 1% → máximo 10 por aposta.").pack(
            side="left", pady=8)

        row_sep4 = self._separator(row)
        row_sep4.pack(side="left", padx=14)

        toggle = ToggleSwitch(row, self.xg_var)
        self._param_block(row, "USAR xG", toggle,
                          "Mistura xG aos gols no cálculo dos lambdas do modelo.").pack(
            side="left", pady=8)

    # ================================================================ abas

    def _build_tabbar(self) -> None:
        self.tabbar = TabBar(
            self,
            (
                ("signals", "SINAIS"),
                ("matches", "JOGOS"),
                ("odds", "CASAS / ODDS"),
                ("stats", "ESTATÍSTICAS"),
                ("model", "MODELO"),
            ),
            command=self._show_page,
        )
        self.tabbar.pack(side="top", fill="x")

    def _build_pages(self) -> None:
        self.pages: dict[str, tk.Frame] = {}
        container = tk.Frame(self, bg=theme.COLORS["bg"])
        container.pack(side="top", fill="both", expand=True)
        self._page_container = container

        self.pages["signals"] = self._page_signals(container)
        self.pages["matches"] = self._page_matches(container)
        self.pages["odds"] = self._page_odds(container)
        self.pages["stats"] = self._page_stats(container)
        self.pages["model"] = self._page_model(container)

    def _show_page(self, key: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.tabbar.set_active(key)

    # ============================================================== sinais

    def _page_signals(self, parent: tk.Misc) -> tk.Frame:
        c = theme.COLORS
        page = tk.Frame(parent, bg=c["bg"])

        self.kpis = KpiStrip(page)
        self.kpis.pack(side="top", fill="x")

        filterbar = tk.Frame(page, bg=c["surface_1"], height=theme.DIMS["filter_bar"])
        filterbar.pack(side="top", fill="x")
        filterbar.pack_propagate(False)
        tk.Frame(page, bg=c["border"], height=1).pack(side="top", fill="x")

        self.search = SearchField(filterbar, self.filter_var,
                                  placeholder="Buscar jogo, mercado, aposta ou casa...",
                                  width=330)
        self.search.pack(side="left", padx=(14, 12), pady=9)

        self.chips = ChipBar(
            filterbar,
            (
                Chip("all", "Todos", c["text_muted"]),
                Chip("FORTE", "FORTE", c["accent"]),
                Chip("MEDIA", "MÉDIA", c["secondary"]),
                Chip("FRACA", "FRACA", c["neutral"]),
            ),
            command=self._on_chip,
        )
        self.chips.pack(side="left", fill="y", pady=11)

        tk.Label(filterbar, textvariable=self.count_var, bg=c["surface_1"],
                 fg=c["text_muted"], font=theme.FONTS["data_sm"]).pack(
            side="right", padx=16)

        self.signals_grid = DataGrid(
            page,
            self._signal_columns(),
            detail=self._signal_detail,
            empty_title="Nenhum sinal encontrado",
            empty_hint="Tente alterar o filtro de confiança ou o EV mínimo.",
        )
        self.signals_grid.pack(side="top", fill="both", expand=True)

        self.insights = InsightsPanel(page, on_click=self._insight_click)
        self.insights.pack(side="bottom", fill="x")
        return page

    def _signal_columns(self) -> list[Column]:
        c = theme.COLORS

        def conf_cell(s: Signal) -> Cell:
            return Cell(
                text=f"● {CONF_LABEL.get(s.confidence.value, s.confidence.value)}",
                color=theme.confidence_color(s.confidence.value),
                font="ui_sm_bold", align="w",
            )

        def edge_cell(s: Signal) -> Cell:
            color = c["accent"] if s.edge > 0 else (c["negative"] if s.edge < 0 else c["text_muted"])
            return Cell(f"{s.edge * 100:+.1f}pp", color=color, font="data_bold")

        def ev_cell(s: Signal) -> Cell:
            mag = abs(s.ev) / self._max_ev if self._max_ev else 0.0
            return Cell(f"{s.ev * 100:+.1f}%", color=theme.signed_color(s.ev),
                        font="data_bold", bar=mag, bar_color=c["positive_dim"])

        def profit_cell(s: Signal) -> Cell:
            return Cell(f"{s.expected_profit:+.2f}",
                        color=theme.signed_color(s.expected_profit), font="data")

        return [
            Column("conf", "Confiança", 104, "w", "ui_sm_bold", conf_cell,
                   lambda s: CONF_ORDER.get(s.confidence.value, 0),
                   "Nível do sinal: FORTE (EV ≥ 8%), MÉDIA (≥ 4.5%), FRACA (≥ 2%)."),
            Column("match", "Jogo", 210, "w", "ui_bold",
                   lambda s: Cell(s.match, color=c["text"], font="ui_bold", align="w"),
                   lambda s: s.match, "Partida analisada pelo modelo."),
            Column("kick", "Data", 92, "w", "data_sm",
                   lambda s: Cell(s.kickoff, color=c["text_dim"], font="data_sm", align="w"),
                   lambda s: s.kickoff),
            Column("market", "Mercado", 156, "w", "ui_sm",
                   lambda s: Cell(s.market, color=c["text_muted"], font="ui_sm", align="w"),
                   lambda s: s.market, "Mercado em que o valor foi encontrado."),
            Column("outcome", "Aposta", 150, "w", "ui_sm_bold",
                   lambda s: Cell(s.outcome, color=c["text"], font="ui_sm_bold", align="w"),
                   lambda s: s.outcome, "Resultado sugerido pelo modelo."),
            Column("odd", "Odd", 64, "e", "data_bold",
                   lambda s: Cell(f"{s.best_odd:.2f}", color=c["text"], font="data_bold"),
                   lambda s: s.best_odd, "Melhor odd disponível entre as casas."),
            Column("book", "Casa", 100, "w", "ui_sm",
                   lambda s: Cell(s.best_book, color=c["text_muted"], font="ui_sm", align="w"),
                   lambda s: s.best_book, "Casa que oferece a melhor odd."),
            Column("pmodel", "P modelo", 82, "e", "data",
                   lambda s: Cell(f"{s.model_prob * 100:.1f}%", color=c["text"], font="data"),
                   lambda s: s.model_prob,
                   "Probabilidade calculada pelo modelo (Poisson/Dixon-Coles)."),
            Column("pmarket", "P mercado", 88, "e", "data",
                   lambda s: Cell(f"{s.market_prob * 100:.1f}%",
                                  color=c["text_muted"], font="data"),
                   lambda s: s.market_prob,
                   "Probabilidade implícita do consenso de casas, sem vig."),
            Column("edge", "Edge", 76, "e", "data_bold", edge_cell,
                   lambda s: s.edge,
                   "Diferença entre a probabilidade do modelo e a do mercado (pontos percentuais)."),
            Column("ev", "EV", 100, "e", "data_bold", ev_cell,
                   lambda s: s.ev,
                   "Valor esperado por unidade apostada: P modelo × odd − 1."),
            Column("stake", "Stake", 78, "e", "data",
                   lambda s: Cell(f"{s.stake:.2f}", color=c["text"], font="data"),
                   lambda s: s.stake,
                   "Valor sugerido para esta aposta, pela banca atual."),
            Column("stake_pct", "% banca", 80, "e", "data_sm",
                   lambda s: Cell(f"{s.stake_pct * 100:.2f}%",
                                  color=c["text_muted"], font="data_sm"),
                   lambda s: s.stake_pct,
                   "Stake como percentual da banca."),
            Column("exp_profit", "Lucro esp.", 94, "e", "data", profit_cell,
                   lambda s: s.expected_profit,
                   "Lucro esperado no longo prazo: stake × EV."),
            Column("win_profit", "Lucro se vencer", 104, "e", "data_sm",
                   lambda s: Cell(f"{s.gross_profit_if_win:+.2f}",
                                  color=c["text_muted"], font="data_sm"),
                   lambda s: s.gross_profit_if_win,
                   "Retorno bruto se esta aposta vencer."),
            Column("reason", "Racional", 250, "w", "ui_sm",
                   lambda s: Cell(s.rationale, color=c["text_dim"], font="ui_sm", align="w"),
                   None, "Resumo qualitativo do edge e do consenso entre casas."),
        ]

    def _signal_detail(self, s: Signal) -> list[tuple[str, str]]:
        a = self._analysis_by_match.get(s.match)
        pairs: list[tuple[str, str]] = [
            ("Confiança do modelo", CONF_LABEL.get(s.confidence.value, s.confidence.value)),
            ("P modelo", f"{s.model_prob * 100:.1f}%"),
            ("P mercado", f"{s.market_prob * 100:.1f}%"),
            ("Edge", f"{s.edge * 100:+.1f} pp"),
            ("Fair odd", f"{s.fair_odd:.2f}"),
            ("Odd mediana", f"{s.median_odd:.2f}"),
            ("Casas no consenso", f"{s.n_books}"),
            ("Kelly completo", f"{s.kelly * 100:.1f}%"),
            ("Stake", f"{s.stake:.2f} ({s.stake_pct * 100:.2f}% da banca)"),
            ("Lucro esperado", f"{s.expected_profit:+.2f}"),
            ("Lucro se vencer", f"{s.gross_profit_if_win:+.2f}"),
            ("Perda se perder", f"-{s.loss_if_lose:.2f}"),
            ("Casa / mercado", f"{s.best_book} · {s.market}"),
            ("Aposta", f"{s.outcome} @ {s.best_odd:.2f}"),
            ("Racional", s.rationale or "—"),
        ]
        if a is not None:
            pairs.extend([
                ("λ casa / fora", f"{a.lambdas[0]:.2f} / {a.lambdas[1]:.2f}"),
                ("xG criado casa", f"{a.ratings_home.xg_for:.2f}"),
                ("xG criado fora", f"{a.ratings_away.xg_for:.2f}"),
                ("Ataque casa / fora",
                 f"{a.ratings_home.attack:.2f} / {a.ratings_away.attack:.2f}"),
                ("Defesa casa / fora",
                 f"{a.ratings_home.defense:.2f} / {a.ratings_away.defense:.2f}"),
                ("Placar provável",
                 f"{a.top_scorelines[0][0]}-{a.top_scorelines[0][1]} "
                 f"({a.top_scorelines[0][2] * 100:.1f}%)" if a.top_scorelines else "—"),
            ])
        return pairs

    def _on_chip(self, key: str) -> None:
        self._refresh_signals_view()

    # ============================================================== jogos

    def _page_matches(self, parent: tk.Misc) -> tk.Frame:
        c = theme.COLORS
        page = tk.Frame(parent, bg=c["bg"])

        head = tk.Frame(page, bg=c["surface_1"], height=theme.DIMS["filter_bar"])
        head.pack(side="top", fill="x")
        head.pack_propagate(False)
        tk.Frame(page, bg=c["border"], height=1).pack(side="top", fill="x")
        tk.Label(head, text="PROBABILIDADES DO MODELO POR JOGO", bg=c["surface_1"],
                 fg=c["text_dim"], font=theme.FONTS["label"]).pack(side="left", padx=16, pady=15)
        tk.Label(head, text="λ = gols esperados  ·  1/X/2 = casa/empate/fora",
                 bg=c["surface_1"], fg=c["text_muted"],
                 font=theme.FONTS["ui_sm"]).pack(side="right", padx=16)

        grid = DataGrid(
            page,
            self._match_columns(),
            detail=self._match_detail,
            empty_title="Sem jogos analisados",
            empty_hint="Clique em RECALCULAR para gerar a análise.",
        )
        grid.pack(side="top", fill="both", expand=True)
        self.matches_grid = grid
        return page

    def _match_columns(self) -> list[Column]:
        c = theme.COLORS

        def prob_cell(key: str):
            def fn(a: FixtureAnalysis) -> Cell:
                m1 = a.markets.get("Resultado Final (1X2)", {})
                p = m1.get(key, 0.0)
                top = max(m1.values()) if m1 else 0.0
                color = c["accent"] if p and abs(p - top) < 1e-9 else c["text"]
                return Cell(f"{p * 100:.1f}", color=color, font="data")
            return fn

        def mk_cell(market: str, key: str, font: str = "data"):
            def fn(a: FixtureAnalysis) -> Cell:
                v = a.markets.get(market, {}).get(key, 0.0)
                return Cell(f"{v * 100:.1f}", color=c["text"], font=font)
            return fn

        return [
            Column("match", "Jogo", 220, "w", "ui_bold",
                   lambda a: Cell(f"{a.fixture.home} vs {a.fixture.away}",
                                  color=c["text"], font="ui_bold", align="w"),
                   lambda a: f"{a.fixture.home} vs {a.fixture.away}"),
            Column("kick", "Data", 96, "w", "data_sm",
                   lambda a: Cell(a.fixture.kickoff, color=c["text_dim"],
                                  font="data_sm", align="w"),
                   lambda a: a.fixture.kickoff),
            Column("round", "Rodada", 110, "w", "ui_sm",
                   lambda a: Cell(a.fixture.round_label, color=c["text_muted"],
                                  font="ui_sm", align="w"),
                   lambda a: a.fixture.round_label),
            Column("lam", "λ casa/fora", 116, "e", "data",
                   lambda a: Cell(f"{a.lambdas[0]:.2f} / {a.lambdas[1]:.2f}",
                                  color=c["text"], font="data"),
                   lambda a: a.lambdas[0]),
            Column("p1", "1 %", 66, "e", "data", prob_cell("1"), lambda a: a.markets.get("Resultado Final (1X2)", {}).get("1", 0.0)),
            Column("px", "X %", 66, "e", "data", prob_cell("X"), lambda a: a.markets.get("Resultado Final (1X2)", {}).get("X", 0.0)),
            Column("p2", "2 %", 66, "e", "data", prob_cell("2"), lambda a: a.markets.get("Resultado Final (1X2)", {}).get("2", 0.0)),
            Column("over25", "Over 2.5", 86, "e", "data", mk_cell("Total de Gols", "Over 2.5"),
                   lambda a: a.markets.get("Total de Gols", {}).get("Over 2.5", 0.0)),
            Column("btts", "BTTS", 72, "e", "data", mk_cell("Ambas Marcam", "BTTS Sim"),
                   lambda a: a.markets.get("Ambas Marcam", {}).get("BTTS Sim", 0.0)),
            Column("cantos", "Casa Ct>5.5", 104, "e", "data", mk_cell("Escanteios", "Casa Cantos Over 5.5"),
                   lambda a: a.markets.get("Escanteios", {}).get("Casa Cantos Over 5.5", 0.0)),
            Column("cartoes", "Cart >3.5", 92, "e", "data", mk_cell("Cartoes", "Cartoes Over 3.5"),
                   lambda a: a.markets.get("Cartoes", {}).get("Cartoes Over 3.5", 0.0)),
            Column("placar", "Placar provável", 150, "w", "data_sm",
                   lambda a: Cell(
                       f"{a.top_scorelines[0][0]}-{a.top_scorelines[0][1]} "
                       f"({a.top_scorelines[0][2] * 100:.1f}%)" if a.top_scorelines else "—",
                       color=c["text_muted"], font="data_sm", align="w"),
                   None),
        ]

    def _match_detail(self, a: FixtureAnalysis) -> list[tuple[str, str]]:
        hr, ar = a.ratings_home, a.ratings_away
        pairs = [
            ("xG criado casa", f"{hr.xg_for:.2f}"),
            ("xG criado fora", f"{ar.xg_for:.2f}"),
            ("xG concedido casa", f"{hr.xg_against:.2f}"),
            ("xG concedido fora", f"{ar.xg_against:.2f}"),
            ("Ataque casa", f"{hr.attack:.2f}"),
            ("Ataque fora", f"{ar.attack:.2f}"),
            ("Defesa casa", f"{hr.defense:.2f}"),
            ("Defesa fora", f"{ar.defense:.2f}"),
            ("Cantos casa / fora", f"{hr.corners_for:.1f} / {ar.corners_for:.1f}"),
            ("Cartões casa / fora", f"{hr.cards_for:.1f} / {ar.cards_for:.1f}"),
            ("Forma casa / fora", f"{hr.form_points:.2f} / {ar.form_points:.2f}"),
            ("Mercados com odds", f"{len(a.odds)}"),
        ]
        for i, (h, aw, p) in enumerate(a.top_scorelines[:4], 1):
            pairs.append((f"Placar #{i}", f"{h}-{aw}  ({p * 100:.1f}%)"))
        return pairs

    # ========================================================= casas / odds

    def _page_odds(self, parent: tk.Misc) -> tk.Frame:
        c = theme.COLORS
        page = tk.Frame(parent, bg=c["bg"])

        head = tk.Frame(page, bg=c["surface_1"], height=theme.DIMS["filter_bar"])
        head.pack(side="top", fill="x")
        head.pack_propagate(False)
        tk.Frame(page, bg=c["border"], height=1).pack(side="top", fill="x")
        tk.Label(head, text="COMPARAÇÃO DE ODDS ENTRE CASAS", bg=c["surface_1"],
                 fg=c["text_dim"], font=theme.FONTS["label"]).pack(side="left", padx=16, pady=15)

        self.match_var = tk.StringVar(value="")
        self.market_var = tk.StringVar(value="")
        tk.Label(head, text="JOGO", bg=c["surface_1"], fg=c["text_dim"],
                 font=theme.FONTS["label"]).pack(side="left", padx=(10, 6))
        self.match_select = SelectField(head, self.match_var, (), width=280,
                                        command=self._refresh_odds)
        self.match_select.pack(side="left", pady=11)
        tk.Label(head, text="MERCADO", bg=c["surface_1"], fg=c["text_dim"],
                 font=theme.FONTS["label"]).pack(side="left", padx=(14, 6))
        self.market_select = SelectField(head, self.market_var, (), width=190,
                                         command=self._refresh_odds)
        self.market_select.pack(side="left", pady=11)

        self.odds_grid = DataGrid(
            page, [], empty_title="Escolha um jogo",
            empty_hint="As odds multi-casa aparecem aqui.",
        )
        self.odds_grid.pack(side="top", fill="both", expand=True)

        arb_wrap = tk.Frame(page, bg=c["surface_1"])
        arb_wrap.pack(side="bottom", fill="x")
        tk.Frame(page, bg=c["border"], height=1).pack(side="bottom", fill="x")
        tk.Label(arb_wrap, text="ARBITRAGEM", bg=c["surface_1"], fg=c["text_dim"],
                 font=theme.FONTS["label"]).pack(anchor="w", padx=16, pady=(10, 2))
        self.arb_frame = tk.Frame(arb_wrap, bg=c["surface_1"])
        self.arb_frame.pack(fill="x", padx=16, pady=(0, 12))
        return page

    # ========================================================= estatisticas

    def _page_stats(self, parent: tk.Misc) -> tk.Frame:
        c = theme.COLORS
        page = tk.Frame(parent, bg=c["bg"])
        head = tk.Frame(page, bg=c["surface_1"], height=theme.DIMS["filter_bar"])
        head.pack(side="top", fill="x")
        head.pack_propagate(False)
        tk.Frame(page, bg=c["border"], height=1).pack(side="top", fill="x")
        tk.Label(head, text="RATINGS E ESTATÍSTICAS POR TIME", bg=c["surface_1"],
                 fg=c["text_dim"], font=theme.FONTS["label"]).pack(side="left", padx=16, pady=15)
        tk.Label(head, text="por jogo  ·  ataque/defesa relativo à média da liga",
                 bg=c["surface_1"], fg=c["text_muted"],
                 font=theme.FONTS["ui_sm"]).pack(side="right", padx=16)

        self.stats_grid = DataGrid(page, self._stats_columns(),
                                   empty_title="Sem estatísticas",
                                   empty_hint="Clique em RECALCULAR para gerar os ratings.")
        self.stats_grid.pack(side="top", fill="both", expand=True)
        return page

    def _stats_columns(self) -> list[Column]:
        c = theme.COLORS

        def num(attr: str, fmt: str = "{:.2f}", color_key: str = "text"):
            def fn(r) -> Cell:
                return Cell(fmt.format(getattr(r, attr)), color=c[color_key], font="data")
            return fn

        return [
            Column("team", "Time", 170, "w", "ui_bold",
                   lambda r: Cell(r.name, color=c["text"], font="ui_bold", align="w"),
                   lambda r: r.name),
            Column("att", "Ataque", 78, "e", "data", num("attack"), lambda r: r.attack),
            Column("def", "Defesa", 78, "e", "data", num("defense"), lambda r: r.defense),
            Column("str", "Força", 78, "e", "data_bold",
                   lambda r: Cell(f"{r.strength:.2f}", color=c["accent"], font="data_bold"),
                   lambda r: r.strength),
            Column("gf", "GP", 62, "e", "data", num("goals_for"), lambda r: r.goals_for),
            Column("ga", "GC", 62, "e", "data", num("goals_against"), lambda r: r.goals_against),
            Column("xgf", "xG+", 68, "e", "data", num("xg_for"), lambda r: r.xg_for),
            Column("xga", "xG-", 68, "e", "data", num("xg_against"), lambda r: r.xg_against),
            Column("cf", "Ct+", 62, "e", "data", num("corners_for", "{:.1f}"),
                   lambda r: r.corners_for),
            Column("ca", "Ct-", 62, "e", "data", num("corners_against", "{:.1f}"),
                   lambda r: r.corners_against),
            Column("kf", "Cart+", 68, "e", "data", num("cards_for", "{:.1f}"),
                   lambda r: r.cards_for),
            Column("ka", "Cart-", 68, "e", "data", num("cards_against", "{:.1f}"),
                   lambda r: r.cards_against),
            Column("pts", "Pts", 62, "e", "data", num("form_points"), lambda r: r.form_points),
            Column("n", "J", 50, "e", "data_sm", num("matches_played", "{:.0f}", "text_muted"),
                   lambda r: r.matches_played),
        ]

    # ============================================================== modelo

    def _page_model(self, parent: tk.Misc) -> tk.Frame:
        c = theme.COLORS
        page = tk.Frame(parent, bg=c["bg"])
        head = tk.Frame(page, bg=c["surface_1"], height=theme.DIMS["filter_bar"])
        head.pack(side="top", fill="x")
        head.pack_propagate(False)
        tk.Frame(page, bg=c["border"], height=1).pack(side="top", fill="x")
        tk.Label(head, text="COMO O MODELO FUNCIONA", bg=c["surface_1"],
                 fg=c["text_dim"], font=theme.FONTS["label"]).pack(side="left", padx=16, pady=15)

        wrap = tk.Frame(page, bg=c["bg"])
        wrap.pack(fill="both", expand=True, padx=14, pady=12)
        txt = tk.Text(wrap, bg=c["surface_1"], fg=c["text"], font=theme.FONTS["data_sm"],
                      relief="flat", bd=0, wrap="word", padx=18, pady=16,
                      insertbackground=c["accent"], selectbackground=c["surface_3"])
        bar = ttk_scrollbar(wrap, "vertical", txt.yview)
        txt.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("end", MODEL_DOC)
        txt.configure(state="disabled")
        return page

    # =========================================================== statusbar

    def _build_statusbar(self) -> None:
        c = theme.COLORS
        tk.Frame(self, bg=c["border"], height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(self, bg=c["surface_1"], height=theme.DIMS["status"])
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, textvariable=self.status_var, bg=c["surface_1"],
                 fg=c["text_muted"], font=theme.FONTS["ui_sm"], anchor="w").pack(
            side="left", padx=16, pady=5)
        tk.Label(bar, textvariable=self.exposure_var, bg=c["surface_1"],
                 fg=c["text_muted"], font=theme.FONTS["data_sm"], anchor="e").pack(
            side="right", padx=16, pady=5)

    # ============================================================ recalculo

    def recalculate(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.recalc_btn.set_loading(True)
        self.status_var.set("Recalculando modelo, mercados e sinais...")
        self._status_dot.configure(fg=theme.COLORS["secondary"])
        self.header_status_var.set("recalculando...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            res = run(
                bankroll=float(self.bankroll_var.get()),
                kelly_frac=float(self.kelly_var.get()),
                stake_cap=float(self.stake_cap_var.get()) / 100.0,
                min_ev=float(self.min_ev_var.get()) / 100.0,
                use_xg=bool(self.xg_var.get()),
                rounds=3,
            )
        except Exception as exc:  # nunca esconder o erro real
            self.after(0, lambda: self._on_error(exc))
            return
        self.after(0, lambda: self._on_done(res))

    def _on_error(self, exc: Exception) -> None:
        self._loading = False
        self.recalc_btn.set_loading(False)
        self._status_dot.configure(fg=theme.COLORS["negative"])
        self.header_status_var.set("erro no cálculo")
        self.status_var.set(f"ERRO: {exc}")
        self.toast.show(f"Falha no pipeline: {exc}", "error", duration=6000)

    def _on_done(self, res: RunResult) -> None:
        self._loading = False
        self.recalc_btn.set_loading(False)
        self.result = res
        self._analysis_by_match = {
            f"{a.fixture.home} vs {a.fixture.away}": a for a in res.analyses
        }
        self._max_ev = max((s.ev for s in res.report.signals), default=0.0) if res.report else 0.0

        self._refresh_signals_view()
        self._fill_matches()
        self._fill_stats()
        self._fill_match_options()

        n = len(res.report.signals) if res.report else 0
        self.status_var.set(
            f"OK — {len(res.analyses)} jogos, {n} sinais acima do EV mínimo. "
            f"Gerado em {res.report.generated_at if res.report else ''}."
        )
        if res.report:
            exp = res.report.total_exposure()
            note = ""
            if res.report.exposure_scaled_by < 0.999:
                note = f"  (escalado ×{res.report.exposure_scaled_by:.3f} para o teto de 25%)"
            self.exposure_var.set(
                f"Exposição {exp:.2f} ({exp / max(1.0, res.report.bankroll) * 100:.1f}%)  ·  "
                f"Lucro esperado {res.report.expected_profit:+.2f} "
                f"({res.report.expected_profit_pct * 100:+.2f}%)  ·  "
                f"Perda máxima {res.report.worst_case_loss:.2f}{note}"
            )
        self.toast.show("Sinais recalculados", "success")

    # ======================================================== visao de sinais

    def _filtered_signals(self) -> list[Signal]:
        if not self.result or not self.result.report:
            return []
        rows = list(self.result.report.signals)
        conf = self.chips.selected if hasattr(self, "chips") else "all"
        if conf != "all":
            rows = [s for s in rows if s.confidence.value == conf]
        query = (self.filter_var.get() or "").strip().lower()
        if query:
            def hit(s: Signal) -> bool:
                blob = " ".join((s.match, s.market, s.outcome, s.best_book,
                                 s.confidence.value, s.kickoff)).lower()
                return query in blob
            rows = [s for s in rows if hit(s)]
        return rows

    def _refresh_signals_view(self) -> None:
        rows = self._filtered_signals()
        grid = self.signals_grid
        grid.set_rows(rows)
        grid.reapply_sort()
        self.count_var.set(f"{len(rows)} sinal" + ("" if len(rows) == 1 else "s"))
        self._update_kpis(rows)
        self._update_insights(rows)

    def _update_kpis(self, rows: list[Signal]) -> None:
        c = theme.COLORS
        forte = [s for s in rows if s.confidence == Confidence.FORTE]
        media = [s for s in rows if s.confidence == Confidence.MEDIA]
        profit = sum(s.expected_profit for s in rows)
        stake_pct = mean([s.stake_pct for s in rows]) * 100 if rows else 0.0
        bankroll = self.result.report.bankroll if self.result and self.result.report else 0.0
        total = len(rows) or 1
        cumulative: list[float] = []
        acc = 0.0
        for s in sorted(rows, key=lambda s: s.ev, reverse=True):
            acc += s.expected_profit
            cumulative.append(acc)
        if not cumulative:
            cumulative = [0.0, 0.0]

        items = [
            KpiItem("Sinais FORTE", float(len(forte)), lambda v: f"{v:.0f}",
                    c["accent"], f"{len(forte) / total * 100:.0f}% da lista"),
            KpiItem("Sinais MÉDIA", float(len(media)), lambda v: f"{v:.0f}",
                    c["secondary"], f"{len(media) / total * 100:.0f}% da lista"),
            KpiItem("Lucro esperado", profit, lambda v: f"{v:+.2f}",
                    theme.signed_color(profit) if rows else c["text_muted"],
                    f"{profit / bankroll * 100:+.2f}% da banca" if bankroll else "—",
                    spark=cumulative),
            KpiItem("Stake médio", stake_pct, lambda v: f"{v:.2f}%",
                    c["text"], f"{len(rows)} sinais na lista"),
        ]
        self.kpis.set_items(items)

    def _update_insights(self, rows: list[Signal]) -> None:
        top = sorted(rows, key=lambda s: s.ev, reverse=True)[:5]
        self._insight_signals = top
        items = [
            InsightItem(rank=i, match=s.match, outcome=s.outcome, book=s.best_book,
                        odd=s.best_odd, ev=s.ev, confidence=s.confidence.value)
            for i, s in enumerate(top, 1)
        ]
        self.insights.set_items(items)
        forte = sum(1 for s in rows if s.confidence == Confidence.FORTE)
        self._status_dot.configure(
            fg=theme.COLORS["accent"] if forte else theme.COLORS["text_dim"])
        self.header_status_var.set(
            f"{forte} sinal{'is' if forte != 1 else ''} forte{'s' if forte != 1 else ''}"
            if rows else "sem sinais no filtro"
        )

    def _insight_click(self, rank: int) -> None:
        if rank < 1 or rank > len(self._insight_signals):
            return
        sig = self._insight_signals[rank - 1]
        self._show_page("signals")
        grid = self.signals_grid
        if sig not in grid.rows:
            self.chips.set_selected("all")
            self.filter_var.set("")
            self._refresh_signals_view()
        grid.select(sig)

    # ============================================================== fillers

    def _fill_matches(self) -> None:
        rows = self.result.analyses if self.result else []
        self.matches_grid.set_rows(rows)

    def _fill_stats(self) -> None:
        rows = sorted(self.result.ratings.values(), key=lambda r: r.strength, reverse=True) \
            if self.result else []
        self.stats_grid.set_rows(rows)

    def _fill_match_options(self) -> None:
        if not self.result:
            return
        keys = [f"{a.fixture.home} vs {a.fixture.away}" for a in self.result.analyses]
        self.match_select.set_options(keys)
        if keys and self.match_var.get() not in keys:
            self.match_var.set(keys[0])
        self._refresh_odds()

    def _refresh_odds(self) -> None:
        if not self.result:
            return
        key = self.match_var.get()
        analysis = self._analysis_by_match.get(key)
        if analysis is None:
            return
        markets = list(analysis.odds.keys())
        self.market_select.set_options(markets)
        if markets and self.market_var.get() not in markets:
            self.market_var.set(markets[0])
        market = self.market_var.get()
        books = analysis.odds.get(market, {})

        outcomes: list[str] = []
        for book_map in books.values():
            for oc in book_map:
                if oc not in outcomes:
                    outcomes.append(oc)
        outcomes.sort()

        c = theme.COLORS
        cols = [Column("casa", "Casa", 160, "w", "ui_bold",
                       lambda r: Cell(r.get("casa", ""),
                                      color=c["accent"] if r.get("best") else c["text"],
                                      font="ui_bold", align="w"),
                       lambda r: r.get("casa", ""))]
        for oc in outcomes:
            cols.append(Column(
                f"oc::{oc}", oc, 110, "center", "data",
                (lambda o: lambda r: Cell(r.get(o, "—"),
                                          color=c["accent"] if r.get(f"best::{o}") else c["text"],
                                          font="data_bold" if r.get(f"best::{o}") else "data",
                                          align="center"))(oc),
                lambda r, o=oc: _odd_num(r.get(o)),
            ))
        cols.append(Column("melhor", "Melhor odd", 150, "w", "data_sm",
                           lambda r: Cell(r.get("melhor", ""), color=c["positive"],
                                          font="data_sm", align="w"), None))

        best: dict[str, tuple[float, str]] = {}
        rows: list[dict] = []
        for book, book_map in books.items():
            row: dict = {"casa": book}
            for oc in outcomes:
                odd = book_map.get(oc)
                row[oc] = f"{odd:.2f}" if odd else "—"
                if odd and (oc not in best or odd > best[oc][0]):
                    best[oc] = (odd, book)
            rows.append(row)

        best_row: dict = {"casa": "MELHOR", "best": True}
        for oc in outcomes:
            if oc in best:
                best_row[oc] = f"{best[oc][0]:.2f}"
                best_row[f"best::{oc}"] = True
            else:
                best_row[oc] = "—"
        rows.append(best_row)

        for oc in outcomes:
            if oc in best:
                odd, book = best[oc]
                rows.append({"casa": f"→ {book}", "melhor": f"{oc} @ {odd:.2f}"})

        self.odds_grid.set_columns(cols)
        self.odds_grid.set_rows(rows)

        arb = scan_arbitrage(books, total_stake=float(self.bankroll_var.get()))
        for child in self.arb_frame.winfo_children():
            child.destroy()
        if arb.arbitrage:
            tk.Label(self.arb_frame,
                     text=f"ARBITRAGEM DETECTADA — margem garantida de {arb.margin * 100:.2f}%",
                     bg=c["surface_1"], fg=c["accent"],
                     font=theme.FONTS["ui_bold"]).pack(anchor="w")
            for leg in arb.legs:
                tk.Label(self.arb_frame,
                         text=f"   {leg.outcome} @ {leg.book}  odd {leg.odd:.2f}  →  "
                              f"stake {leg.stake:.2f}  (retorno {leg.payout:.2f})",
                         bg=c["surface_1"], fg=c["text"],
                         font=theme.FONTS["data_sm"]).pack(anchor="w")
        else:
            tk.Label(self.arb_frame,
                     text=f"Sem arbitragem neste mercado. Margem das melhores odds: "
                          f"{arb.margin * 100:+.2f}% (negativo = sem arb).",
                     bg=c["surface_1"], fg=c["text_muted"],
                     font=theme.FONTS["data_sm"]).pack(anchor="w")


def _odd_num(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return -1.0


MODEL_DOC = """BETGSN — COMO O MODELO FUNCIONA

1. FORCA DOS TIMES (fit_ratings)
   A partir do historico, cada time recebe um rating de ATAQUE e DEFESA por
   ponto fixo, no estilo Maher. attack ~ 1.0 e a media da liga. attack 1.30 =
   cria ~30% mais gols que a media. defense 1.20 = concede ~20% mais.

2. GOLS ESPERADOS (expected_goals)
       lambda_casa = attack_casa * defense_fora * media_liga * vantagem_casa
       lambda_fora = attack_fora * defense_casa * media_liga
   O ataque mistura GOLS e xG (blend 0.5 por padrao). xG e mais estavel:
   gol depende de sorte/defesa; xG mede a qualidade das chances.

3. MATRIZ DE PLACAR (build_score_matrix)
   Poisson bivariado com correcao Dixon-Coles (rho = -0.05) para placares
   baixos. Dessa matriz saem TODAS as probabilidades: 1X2, over/under,
   BTTS, handicaps, total por time.

4. CANTOS E CARTOES
   Contagens modeladas por Poisson de taxa (ataque + defesa adversaria)/2.
   Cartoes ajustaveis por rigor do arbitro (ref_strictness).

5. MERCADO x MODELO (engine.evaluate_market)
   Para cada resultado: pega a MELHOR odd entre as casas, tira o vig do
   consenso (mediana) e compara:
       edge = prob_modelo - prob_mercado
       EV   = prob_modelo * melhor_odd - 1
   EV > 0 = aposta com valor esperado positivo. EV positivo NAO garante
   ganhar a aposta; garante retorno positivo no longo prazo se a prob do
   modelo estiver certa.

6. SINAL E STAKE (signals + engine.stake_plan)
   FORTE  EV >= 8%   |  MEDIA EV >= 4.5%  |  FRACA EV >= 2%
   Stake por Kelly fracionado (padrao quarter-Kelly) com teto configuravel
   no campo "RISCO/APOSTA" (padrao 1%) e exposicao total de 25% da banca.
   Ex.: banca 1.000 e risco 1% -> teto 10; banca 2.500 -> teto 25.
   A proxima aposta sempre usa a banca atualizada: crescimento composto.
   A grade mostra em cada sinal: % da banca, stake, lucro esperado
   (stake * EV), lucro bruto se vencer e perda maxima se perder.

7. MATEMATICA DO LUCRO
   EV por unidade = prob_modelo * odd - 1
   lucro esperado = stake * EV
   lucro se vencer = stake * (odd - 1)
   perda se perder = stake
   exposicao total = soma das stakes
   crescimento composto: banca_nova = banca_atual + lucro_real
   EV positivo nao e lucro garantido na aposta individual; e media esperada
   sob uma probabilidade calibrada. O backtest e obrigatorio para confiar.

8. COMO LER A GRADE
   P modelo (texto primario) x P mercado (texto apagado) -> Edge -> EV.
   Edge e EV alinhados a direita para comparacao vertical entre linhas.
   Clique numa linha para expandir os detalhes; duplo clique faz o mesmo.
   Clique no cabecalho para ordenar; botao direito copia a linha.

LIMITES — leia antes de apostar:
  - O dataset local e SINTETICO (seed fixo). Serve para testar o pipeline,
    nao para apostar dinheiro real.
  - Para dados reais, configure BETGSN_ODDS_API_KEY e BETGSN_APIFOOTBALL_KEY
    (python betgsn.py --providers). Sem chave, o app roda offline.
  - Edge publicado morre: se todos usarem o mesmo modelo, o mercado se ajusta.
  - Multiplas longas multiplicam a margem da casa junto com a odd.
  - Nenhum modelo garante lucro. Gestao de banca e metade do jogo.
"""


def main() -> None:
    app = BetgsnApp()
    app.mainloop()


if __name__ == "__main__":
    main()
