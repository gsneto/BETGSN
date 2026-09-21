"""BETGSN :: widgets — componentes visuais reutilizaveis.

Todos os componentes leem cor/fonte/metrica de ``betgsn.theme``. Nenhum
widget aqui conhece regra de negocio: recebem dados prontos e devolvem
eventos por callback.

Componentes:
    Tooltip        texto de ajuda em hover
    AccentButton   botao com hover/pressed/disabled/loading
    ToggleSwitch   chave booleana animada
    SelectField    seletor compacto de opcoes (menu popup)
    SearchField    campo de busca com placeholder e limpar
    ChipBar        chips de filtro (Todos/FORTE/MEDIA/...)
    KpiItem/KpiStrip  faixa de indicadores continua
    InsightsPanel  top oportunidades em blocos compactos
    Toast          notificacao discreta de sucesso/erro
    Column/Cell/DataGrid  grade quantitativa virtualizada com cabecalho fixo
"""

from __future__ import annotations

import bisect
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from . import theme

# --------------------------------------------------------------------------
# Tooltip
# --------------------------------------------------------------------------


class Tooltip:
    """Tooltip simples: aparece apos ``delay`` ms e some ao sair."""

    def __init__(self, widget: tk.Misc, text: str, *, delay: int = 420) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event: tk.Event) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self) -> None:
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(tip, bg=theme.COLORS["border"], padx=1, pady=1)
        frame.pack()
        tk.Label(
            frame,
            text=self.text,
            justify="left",
            bg=theme.COLORS["surface_3"],
            fg=theme.COLORS["text"],
            font=theme.FONTS["ui_sm"],
            padx=8,
            pady=5,
            wraplength=320,
        ).pack()
        self._tip = tip

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# --------------------------------------------------------------------------
# Botao
# --------------------------------------------------------------------------


class AccentButton(tk.Canvas):
    """Botao desenhado em canvas, com estados completos.

    kind:
        primary -> preenchido dourado (acao principal)
        ghost   -> contorno, fundo de superficie
        subtle  -> sem contorno, texto discreto
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        kind: str = "primary",
        width: int | None = None,
        height: int | None = None,
        font_key: str = "ui_bold",
    ) -> None:
        self.text = text
        self.command = command
        self.kind = kind
        self.font_key = font_key
        fnt = theme.FONTS[font_key]
        self._pad_x = 18 if kind == "primary" else 14
        w = width or (fnt.measure(text) + self._pad_x * 2)
        h = height or theme.DIMS["button_h"]
        super().__init__(
            master,
            width=w,
            height=h,
            bg=theme.COLORS["surface_1"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._state = "normal"  # normal | hover | pressed | disabled | loading
        self._spinner_angle = 0
        self._spinner_job: str | None = None
        self._loading_text = "Recalculando..."
        self._bind_events()
        self._draw()

    # ------------------------------------------------------------------ api
    def set_text(self, text: str) -> None:
        self.text = text
        if self._state != "loading":
            self._draw()

    def set_state(self, state: str) -> None:
        """normal | hover | pressed | disabled | loading"""
        if state == self._state:
            return
        self._state = state
        if state == "disabled":
            self.configure(cursor="arrow")
        elif state == "loading":
            self.configure(cursor="watch")
            self._start_spinner()
        else:
            self.configure(cursor="hand2")
            self._stop_spinner()
        self._draw()

    def set_loading(self, loading: bool, text: str = "Recalculando...") -> None:
        self._loading_text = text
        self.set_state("loading" if loading else "normal")

    # -------------------------------------------------------------- interno
    def _bind_events(self) -> None:
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _interactive(self) -> bool:
        return self._state not in ("disabled", "loading")

    def _on_enter(self, _e: tk.Event) -> None:
        if self._interactive():
            self._state = "hover"
            self._draw()

    def _on_leave(self, _e: tk.Event) -> None:
        if self._interactive():
            self._state = "normal"
            self._draw()

    def _on_press(self, _e: tk.Event) -> None:
        if self._interactive():
            self._state = "pressed"
            self._draw()

    def _on_release(self, _e: tk.Event) -> None:
        if self._interactive():
            self._state = "hover"
            self._draw()
            self.command()

    def _start_spinner(self) -> None:
        if self._spinner_job is not None:
            return
        self._tick()

    def _stop_spinner(self) -> None:
        if self._spinner_job is not None:
            try:
                self.after_cancel(self._spinner_job)
            except tk.TclError:
                pass
            self._spinner_job = None

    def _tick(self) -> None:
        self._spinner_angle = (self._spinner_angle + 30) % 360
        self._draw()
        self._spinner_job = self.after(45, self._tick)

    def _palette(self) -> tuple[str, str, str]:
        """(fundo, texto, contorno) para o estado atual."""
        c = theme.COLORS
        if self._state == "disabled":
            if self.kind == "primary":
                return c["surface_3"], c["text_dim"], c["surface_3"]
            return c["surface_1"], c["text_dim"], c["border_soft"]
        if self._state == "loading":
            return theme.mix(c["accent"], c["bg"], 0.45), c["bg"], c["accent_dim"]
        if self.kind == "primary":
            if self._state == "hover":
                return theme.mix(c["accent"], "#FFFFFF", 0.10), "#1A1206", c["accent"]
            if self._state == "pressed":
                return theme.mix(c["accent"], c["bg"], 0.22), "#1A1206", c["accent"]
            return c["accent"], "#1A1206", c["accent"]
        if self.kind == "ghost":
            fill = c["surface_3"] if self._state in ("hover", "pressed") else c["surface_2"]
            return fill, c["text"], c["border"]
        fill = c["surface_2"] if self._state in ("hover", "pressed") else c["surface_1"]
        return fill, c["text_muted"], fill

    def _draw(self) -> None:
        self.delete("all")
        w = int(self.winfo_reqwidth())
        h = int(self.winfo_reqheight())
        fill, fg, outline = self._palette()
        theme.rounded_rect(
            self, 0.5, 0.5, w - 0.5, h - 0.5,
            theme.RADIUS["md"], fill=fill, outline=outline,
        )
        cx = w / 2
        if self._state == "loading":
            r = 7
            sx = cx - theme.FONTS[self.font_key].measure(self._loading_text) / 2 - 14
            self.create_arc(
                sx - r, h / 2 - r, sx + r, h / 2 + r,
                start=self._spinner_angle, extent=270, style="arc",
                outline=fg, width=2,
            )
            self.create_text(cx + 8, h / 2, text=self._loading_text,
                             fill=fg, font=theme.FONTS[self.font_key])
            return
        self.create_text(cx, h / 2, text=self.text, fill=fg,
                         font=theme.FONTS[self.font_key])


# --------------------------------------------------------------------------
# Toggle
# --------------------------------------------------------------------------


class ToggleSwitch(tk.Canvas):
    """Chave booleana animada, estilo terminal moderno."""

    W = 38
    H = 20

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            master, width=self.W, height=self.H,
            bg=theme.COLORS["surface_1"], highlightthickness=0, bd=0,
            cursor="hand2",
        )
        self.var = variable
        self.command = command
        self._pos = 1.0 if self.var.get() else 0.0
        self._job: str | None = None
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _e: tk.Event | None = None) -> None:
        self.var.set(not self.var.get())
        self._animate(1.0 if self.var.get() else 0.0)
        if self.command:
            self.command()

    def _animate(self, target: float) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        start = self._pos
        steps = 6

        def step(i: int) -> None:
            t = i / steps
            self._pos = start + (target - start) * (1 - (1 - t) ** 3)
            self._draw()
            if i < steps:
                self._job = self.after(16, lambda: step(i + 1))
            else:
                self._pos = target
                self._job = None
                self._draw()

        step(1)

    def _draw(self) -> None:
        self.delete("all")
        c = theme.COLORS
        on = self._pos > 0.5
        track = theme.mix(c["surface_3"], c["accent"], self._pos)
        theme.rounded_rect(self, 0.5, 2.5, self.W - 0.5, self.H - 2.5,
                           theme.RADIUS["pill"], fill=track, outline="")
        pad = 3
        knob_d = self.H - 2 * pad - 1
        x = pad + self._pos * (self.W - knob_d - 2 * pad)
        knob = "#1A1206" if on else c["text_muted"]
        self.create_oval(x, pad + 0.5, x + knob_d, pad + 0.5 + knob_d,
                         fill=knob, outline="")


# --------------------------------------------------------------------------
# Seletor
# --------------------------------------------------------------------------


class SelectField(tk.Canvas):
    """Seletor compacto: mostra o valor atual e abre menu popup ao clicar."""

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.StringVar,
        options: Sequence[str],
        *,
        command: Callable[[], None] | None = None,
        width: int = 92,
        fmt: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(
            master, width=width, height=theme.DIMS["input_h"],
            bg=theme.COLORS["surface_1"], highlightthickness=0, bd=0,
            cursor="hand2",
        )
        self.var = variable
        self.options = list(options)
        self.command = command
        self.fmt = fmt or (lambda v: v)
        self._hover = False
        self.bind("<Button-1>", self._popup)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def set_options(self, options: Sequence[str]) -> None:
        self.options = list(options)
        self._draw()

    def _enter(self, _e: tk.Event) -> None:
        self._hover = True
        self._draw()

    def _leave(self, _e: tk.Event) -> None:
        self._hover = False
        self._draw()

    def _popup(self, _e: tk.Event) -> None:
        if not self.options:
            return
        c = theme.COLORS
        menu = tk.Menu(
            self, tearoff=0, bg=c["surface_2"], fg=c["text"],
            activebackground=c["surface_3"], activeforeground=c["accent"],
            borderwidth=0, relief="flat", font=theme.FONTS["data"],
        )
        for opt in self.options:
            menu.add_command(label=f"  {self.fmt(opt)}  ",
                             command=lambda o=opt: self._choose(o))
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2)
        finally:
            menu.grab_release()

    def _choose(self, value: str) -> None:
        self.var.set(value)
        if self.command:
            self.command()

    def _draw(self) -> None:
        self.delete("all")
        c = theme.COLORS
        w = int(self.winfo_reqwidth())
        h = int(self.winfo_reqheight())
        fill = c["surface_3"] if self._hover else c["surface_2"]
        theme.rounded_rect(self, 0.5, 0.5, w - 0.5, h - 0.5,
                           theme.RADIUS["sm"], fill=fill, outline=c["border"])
        txt = self.fmt(self.var.get())
        self.create_text(9, h / 2, text=txt, anchor="w", fill=c["text"],
                         font=theme.FONTS["data_bold"])
        ax = w - 12
        self.create_polygon(ax - 4, h / 2 - 1, ax + 4, h / 2 - 1, ax, h / 2 + 3,
                            fill=c["text_muted"], outline="")


# --------------------------------------------------------------------------
# Busca
# --------------------------------------------------------------------------


class SearchField(tk.Frame):
    """Campo de busca com placeholder, icone e botao de limpar."""

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.StringVar,
        *,
        placeholder: str = "Buscar...",
        width: int = 260,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, bg=theme.COLORS["border"], padx=1, pady=1)
        self.var = variable
        self.placeholder = placeholder
        self.command = command
        self._focused = False
        inner = tk.Frame(self, bg=theme.COLORS["surface_2"])
        inner.pack(fill="both", expand=True)
        self._icon = tk.Label(inner, text="⌕", bg=theme.COLORS["surface_2"],
                              fg=theme.COLORS["text_dim"], font=theme.FONTS["ui"])
        self._icon.pack(side="left", padx=(7, 3), pady=2)
        field = tk.Frame(inner, bg=theme.COLORS["surface_2"])
        field.pack(side="left", fill="both", expand=True, pady=4)
        self.entry = tk.Entry(
            field, textvariable=variable, width=1,
            bg=theme.COLORS["surface_2"], fg=theme.COLORS["text"],
            insertbackground=theme.COLORS["accent"], relief="flat",
            highlightthickness=0, bd=0, font=theme.FONTS["ui"],
        )
        self.entry.pack(fill="both", expand=True)
        # Placeholder e um label sobreposto: NUNCA escrever no Entry, porque
        # ele esta ligado a textvariable e o texto viraria filtro de verdade.
        self._ph = tk.Label(field, text=placeholder, bg=theme.COLORS["surface_2"],
                            fg=theme.COLORS["text_dim"], font=theme.FONTS["ui"],
                            cursor="xterm")
        self._ph.bind("<Button-1>", lambda e: self.focus())
        self._clear = tk.Label(inner, text="✕", bg=theme.COLORS["surface_2"],
                               fg=theme.COLORS["surface_2"], font=theme.FONTS["ui_sm"],
                               cursor="hand2")
        self._clear.pack(side="right", padx=(3, 7))
        self._clear.bind("<Button-1>", lambda e: self._do_clear())
        self.configure(width=width)
        self.pack_propagate(False)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        variable.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _on_focus_in(self, _e: tk.Event) -> None:
        self._focused = True
        self._sync()

    def _on_focus_out(self, _e: tk.Event) -> None:
        self._focused = False
        self._sync()

    def _do_clear(self) -> None:
        self.var.set("")
        self.focus()
        if self.command:
            self.command()

    def _sync(self) -> None:
        has_text = bool(self.var.get())
        if has_text or self._focused:
            self._ph.place_forget()
        else:
            self._ph.place(x=1, rely=0.5, anchor="w")
        self._clear.configure(fg=theme.COLORS["text_dim"] if has_text
                              else theme.COLORS["surface_2"])

    def focus(self) -> None:
        self.entry.focus_set()


# --------------------------------------------------------------------------
# Chips
# --------------------------------------------------------------------------


@dataclass
class Chip:
    key: str
    label: str
    color: str = theme.COLORS["text_muted"]


class ChipBar(tk.Canvas):
    """Fila de chips de filtro com selecao unica."""

    def __init__(
        self,
        master: tk.Misc,
        chips: Sequence[Chip],
        *,
        command: Callable[[str], None],
        height: int | None = None,
    ) -> None:
        self.chips = list(chips)
        self.command = command
        self.selected = self.chips[0].key if self.chips else ""
        h = height or theme.DIMS["chip_h"]
        super().__init__(master, height=h, bg=theme.COLORS["surface_1"],
                         highlightthickness=0, bd=0)
        self._rects: list[tuple[float, float, float, float, Chip]] = []
        self._hover: str | None = None
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<Configure>", lambda e: self._draw())

    def set_selected(self, key: str) -> None:
        self.selected = key
        self._draw()

    def _layout(self) -> list[tuple[float, float, float, float, Chip]]:
        fnt = theme.FONTS["ui_sm"]
        out = []
        x = 0.0
        for chip in self.chips:
            w = fnt.measure(chip.label) + 22
            out.append((x, 0.0, x + w, float(self.winfo_height()), chip))
            x += w + 6
        return out

    def _hit(self, x: float, y: float) -> Chip | None:
        for x1, y1, x2, y2, chip in self._rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return chip
        return None

    def _click(self, event: tk.Event) -> None:
        chip = self._hit(event.x, event.y)
        if chip:
            self.selected = chip.key
            self._draw()
            self.command(chip.key)

    def _motion(self, event: tk.Event) -> None:
        chip = self._hit(event.x, event.y)
        key = chip.key if chip else None
        if key != self._hover:
            self._hover = key
            self.configure(cursor="hand2" if chip else "arrow")
            self._draw()

    def _leave(self, _e: tk.Event) -> None:
        self._hover = None
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        c = theme.COLORS
        self._rects = self._layout()
        h = self.winfo_height() or theme.DIMS["chip_h"]
        for x1, _, x2, _, chip in self._rects:
            active = chip.key == self.selected
            hover = chip.key == self._hover
            if active:
                fill = theme.mix(c["surface_2"], chip.color, 0.16)
                outline = theme.mix(c["surface_2"], chip.color, 0.55)
                fg = chip.color
            elif hover:
                fill, outline, fg = c["surface_3"], c["border"], c["text"]
            else:
                fill, outline, fg = c["surface_1"], c["border_soft"], c["text_muted"]
            theme.rounded_rect(self, x1, 2, x2, h - 2, theme.RADIUS["pill"],
                               fill=fill, outline=outline)
            self.create_text((x1 + x2) / 2, h / 2, text=chip.label,
                             fill=fg, font=theme.FONTS["ui_sm_bold"])


# --------------------------------------------------------------------------
# KPI strip
# --------------------------------------------------------------------------


@dataclass
class KpiItem:
    label: str
    value: float
    fmt: Callable[[float], str]
    color: str
    sub: str = ""
    spark: list[float] = field(default_factory=list)


class KpiStrip(tk.Canvas):
    """Painel continuo de indicadores, dividido por bordas sutis."""

    def __init__(self, master: tk.Misc, *, height: int | None = None) -> None:
        h = height or theme.DIMS["kpi"]
        super().__init__(master, height=h, bg=theme.COLORS["surface_1"],
                         highlightthickness=0, bd=0)
        self.items: list[KpiItem] = []
        self._shown: list[float] = []
        self._anim_job: str | None = None
        self.bind("<Configure>", lambda e: self._draw())

    def set_items(self, items: Sequence[KpiItem], *, animate: bool = True) -> None:
        self.items = list(items)
        if not animate:
            self._shown = [it.value for it in self.items]
            self._draw()
            return
        if len(self._shown) != len(self.items):
            self._shown = [0.0] * len(self.items)
        self._animate()

    def _animate(self) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
        starts = list(self._shown)
        targets = [it.value for it in self.items]
        steps = 14

        def step(i: int) -> None:
            t = i / steps
            eased = 1 - (1 - t) ** 3
            self._shown = [s + (g - s) * eased for s, g in zip(starts, targets)]
            self._draw()
            if i < steps:
                self._anim_job = self.after(18, lambda: step(i + 1))
            else:
                self._shown = targets
                self._anim_job = None
                self._draw()

        step(1)

    def _draw(self) -> None:
        self.delete("all")
        c = theme.COLORS
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or not self.items:
            return
        n = len(self.items)
        cw = w / n
        for i, item in enumerate(self.items):
            x1 = i * cw
            if i:
                self.create_line(x1, 10, x1, h - 10, fill=c["border_soft"])
            shown = self._shown[i] if i < len(self._shown) else item.value
            pad = 16
            self.create_text(x1 + pad, 15, text=item.label.upper(), anchor="w",
                             fill=c["text_dim"], font=theme.FONTS["label"])
            self.create_text(x1 + pad, 36, text=item.fmt(shown), anchor="w",
                             fill=item.color, font=theme.FONTS["data_lg"])
            if item.sub:
                self.create_text(x1 + pad, h - 13, text=item.sub, anchor="w",
                                 fill=c["text_muted"], font=theme.FONTS["ui_sm"])
            if item.spark:
                self._spark(item.spark, x1 + cw - pad - 92, h - 26, 92, 18, item.color)
        self.create_line(0, h - 0.5, w, h - 0.5, fill=c["border"])

    def _spark(self, values: Sequence[float], x: float, y: float,
               w: float, h: float, color: str) -> None:
        if len(values) < 2:
            return
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        pts: list[float] = []
        for i, v in enumerate(values):
            px = x + (i / (len(values) - 1)) * w
            py = y + h - ((v - lo) / span) * h
            pts.extend((px, py))
        self.create_line(*pts, fill=theme.mix(theme.COLORS["surface_1"], color, 0.7),
                         width=1.4, smooth=True)
        poly = pts + [x + w, y + h, x, y + h]
        self.create_polygon(*poly, fill=theme.mix(theme.COLORS["surface_1"], color, 0.12),
                            outline="")


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------


@dataclass
class InsightItem:
    rank: int
    match: str
    outcome: str
    book: str
    odd: float
    ev: float
    confidence: str


class InsightsPanel(tk.Canvas):
    """Top oportunidades em blocos compactos (nao e uma segunda tabela)."""

    def __init__(self, master: tk.Misc, *, height: int | None = None,
                 on_click: Callable[[int], None] | None = None) -> None:
        h = height or theme.DIMS["insights"]
        super().__init__(master, height=h, bg=theme.COLORS["surface_1"],
                         highlightthickness=0, bd=0)
        self.items: list[InsightItem] = []
        self.on_click = on_click
        self._boxes: list[tuple[float, float, float, float, int]] = []
        self._hover: int | None = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def set_items(self, items: Sequence[InsightItem]) -> None:
        self.items = list(items)
        self._draw()

    def _hit(self, x: float, y: float) -> int | None:
        for x1, y1, x2, y2, rank in self._boxes:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return rank
        return None

    def _motion(self, event: tk.Event) -> None:
        rank = self._hit(event.x, event.y)
        if rank != self._hover:
            self._hover = rank
            self.configure(cursor="hand2" if rank else "arrow")
            self._draw()

    def _leave(self, _e: tk.Event) -> None:
        self._hover = None
        self._draw()

    def _click(self, event: tk.Event) -> None:
        rank = self._hit(event.x, event.y)
        if rank is not None and self.on_click:
            self.on_click(rank)

    def _draw(self) -> None:
        self.delete("all")
        c = theme.COLORS
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        self._boxes = []
        self.create_text(16, 14, text="TOP OPORTUNIDADES", anchor="w",
                         fill=c["text_dim"], font=theme.FONTS["label"])
        self.create_text(w - 16, 14, text="segundo o modelo", anchor="e",
                         fill=c["text_dim"], font=theme.FONTS["ui_sm"])
        if not self.items:
            self.create_text(w / 2, h / 2 + 8, text="Sem oportunidades acima do EV minimo",
                             fill=c["text_dim"], font=theme.FONTS["ui"])
            return
        top = 28
        n = len(self.items)
        avail = w - 32
        gap = 10
        cw = (avail - gap * (n - 1)) / n
        for i, item in enumerate(self.items):
            x1 = 16 + i * (cw + gap)
            x2 = x1 + cw
            hover = self._hover == item.rank
            fill = c["surface_3"] if hover else c["surface_2"]
            theme.rounded_rect(self, x1, top, x2, h - 10, theme.RADIUS["md"],
                               fill=fill, outline=c["border_soft"])
            self.create_line(x1 + 1, top + 8, x1 + 1, h - 18,
                             fill=theme.confidence_color(item.confidence), width=2)
            self.create_text(x1 + 12, top + 13, text=f"#{item.rank}", anchor="w",
                             fill=c["accent"], font=theme.FONTS["data_bold"])
            self.create_text(x2 - 10, top + 13, text=f"EV {item.ev * 100:+.1f}%",
                             anchor="e", fill=theme.signed_color(item.ev),
                             font=theme.FONTS["data_bold"])
            self.create_text(x1 + 12, top + 33,
                             text=theme.truncate(item.match, "ui_sm_bold", int(cw) - 24),
                             anchor="w", fill=c["text"], font=theme.FONTS["ui_sm_bold"])
            self.create_text(x1 + 12, top + 52,
                             text=theme.truncate(item.outcome, "data_sm", int(cw) - 24),
                             anchor="w", fill=c["text_muted"], font=theme.FONTS["data_sm"])
            self.create_text(x1 + 12, top + 70,
                             text=theme.truncate(f"{item.book} @ {item.odd:.2f}",
                                                 "data_sm", int(cw) - 24),
                             anchor="w", fill=c["text_dim"], font=theme.FONTS["data_sm"])
            self._boxes.append((x1, top, x2, h - 10, item.rank))


# --------------------------------------------------------------------------
# Toast
# --------------------------------------------------------------------------


class Toast(tk.Frame):
    """Notificacao discreta ancorada no canto inferior direito."""

    KINDS = {
        "success": ("positive", "✓"),
        "error": ("negative", "!"),
        "info": ("secondary", "i"),
    }

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=theme.COLORS["border"], padx=1, pady=1)
        inner = tk.Frame(self, bg=theme.COLORS["surface_2"])
        inner.pack(fill="both", expand=True)
        self._accent = tk.Frame(inner, width=3, bg=theme.COLORS["positive"])
        self._accent.pack(side="left", fill="y")
        self._icon = tk.Label(inner, text="✓", bg=theme.COLORS["surface_2"],
                              fg=theme.COLORS["positive"], font=theme.FONTS["ui_bold"])
        self._icon.pack(side="left", padx=(9, 6), pady=10)
        self._text = tk.Label(inner, text="", bg=theme.COLORS["surface_2"],
                              fg=theme.COLORS["text"], font=theme.FONTS["ui"],
                              justify="left", anchor="w")
        self._text.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=10)
        self._job: str | None = None
        self._visible = False

    def show(self, text: str, kind: str = "success", *, duration: int = 3400) -> None:
        color_key, icon = self.KINDS.get(kind, self.KINDS["info"])
        color = theme.COLORS[color_key]
        self._accent.configure(bg=color)
        self._icon.configure(text=icon, fg=color)
        self._text.configure(text=text)
        self._visible = True
        self.place(relx=1.0, rely=1.0, anchor="se", x=-18, y=-38)
        self.lift()
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = self.after(duration, self.hide)

    def hide(self) -> None:
        self._visible = False
        self.place_forget()


# --------------------------------------------------------------------------
# Grade de dados
# --------------------------------------------------------------------------


@dataclass
class Cell:
    """Uma celula: texto + estilo. ``bar`` desenha magnitude 0..1 sob o texto."""

    text: str = ""
    color: str = theme.COLORS["text"]
    font: str = "data"
    align: str = "e"
    bar: float | None = None
    bar_color: str | None = None


@dataclass
class Column:
    key: str
    title: str
    width: int
    align: str = "e"
    font: str = "data"
    cell: Callable[[Any], Cell] = field(default=lambda row: Cell())
    sort_key: Callable[[Any], Any] | None = None
    tooltip: str = ""


class DataGrid(tk.Frame):
    """Grade virtualizada com cabecalho fixo, ordenacao, hover, selecao e detalhe.

    Só as linhas visiveis sao desenhadas, entao a grade continua fluida com
    centenas ou milhares de registros. O cabecalho vive num canvas separado
    e permanece fixo enquanto o corpo rola.
    """

    def __init__(
        self,
        master: tk.Misc,
        columns: Sequence[Column],
        *,
        detail: Callable[[Any], list[tuple[str, str]]] | None = None,
        empty_title: str = "Nada para mostrar",
        empty_hint: str = "",
        on_select: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(master, bg=theme.COLORS["bg"])
        self.columns = list(columns)
        self.detail = detail
        self.empty_title = empty_title
        self.empty_hint = empty_hint
        self.on_select = on_select

        self._rows: list[Any] = []
        self._items: list[tuple[str, Any, int]] = []  # (kind, row, height)
        self._offsets: list[int] = []
        self._expanded: set[int] = set()
        self._selected: Any = None
        self._hover: int = -1
        self._sort_key: str | None = None
        self._sort_desc = True
        self._tooltip: Tooltip | None = None

        self._build()
        self.set_rows([])

    # -------------------------------------------------------------- construcao
    def _build(self) -> None:
        c = theme.COLORS
        self.header = tk.Canvas(self, height=theme.DIMS["grid_header"], bg=c["surface_2"],
                                highlightthickness=0, bd=0)
        self.header.pack(side="top", fill="x")
        self.header.bind("<Button-1>", self._header_click)
        self.header.bind("<Motion>", self._header_motion)
        self.header.bind("<Leave>", lambda e: self.configure(cursor="arrow"))
        self.header.bind("<Configure>", lambda e: self._draw_header())

        body_wrap = tk.Frame(self, bg=c["bg"])
        body_wrap.pack(side="top", fill="both", expand=True)
        self.body = tk.Canvas(body_wrap, bg=c["row_even"], highlightthickness=0, bd=0,
                              takefocus=1, yscrollincrement=1, xscrollincrement=1)
        self.vbar = ttk_scrollbar(body_wrap, "vertical", self.body.yview)
        self.body.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self._on_xscroll)
        self.vbar.pack(side="right", fill="y")
        self.body.pack(side="left", fill="both", expand=True)

        self.hbar = ttk_scrollbar(self, "horizontal", self.body.xview)
        self.hbar.pack(side="bottom", fill="x")

        self.body.bind("<Configure>", lambda e: self.redraw())
        self.body.bind("<MouseWheel>", self._wheel)
        self.body.bind("<Motion>", self._motion)
        self.body.bind("<Leave>", self._leave)
        self.body.bind("<Button-1>", self._click)
        self.body.bind("<Double-Button-1>", self._double_click)
        self.body.bind("<Button-3>", self._context)
        self.body.bind("<Up>", lambda e: self._move(-1))
        self.body.bind("<Down>", lambda e: self._move(1))
        self.body.bind("<Return>", lambda e: self._toggle_selected())
        self.body.bind("<Control-c>", lambda e: self._copy_selected())

    # ------------------------------------------------------------------ api
    def set_columns(self, columns: Sequence[Column]) -> None:
        self.columns = list(columns)
        self._sort_key = None
        self._draw_header()
        self.redraw()

    def set_rows(self, rows: Sequence[Any], *, keep_scroll: bool = False) -> None:
        self._rows = list(rows)
        self._expanded.clear()
        self._selected = None
        self._reflow()
        if not keep_scroll:
            self.body.yview_moveto(0)
        self.redraw()

    def set_empty(self, title: str, hint: str = "") -> None:
        self.empty_title, self.empty_hint = title, hint

    @property
    def selected_row(self) -> Any:
        return self._selected

    def expand(self, row: Any, *, scroll: bool = True) -> None:
        if id(row) in self._expanded:
            return
        self._expanded.add(id(row))
        self._reflow()
        if scroll:
            self._scroll_to(row)
        self.redraw()

    def clear_selection(self) -> None:
        self._selected = None
        self.redraw()

    @property
    def rows(self) -> list[Any]:
        """Linhas atualmente exibidas (na ordem da grade)."""
        return list(self._rows)

    @property
    def sort_key(self) -> str | None:
        return self._sort_key

    def is_expanded(self, row: Any) -> bool:
        return id(row) in self._expanded

    def select(self, row: Any, *, expand: bool = True) -> None:
        """Seleciona uma linha, opcionalmente expandindo os detalhes."""
        self._selected = row
        if expand and self.detail and not self.is_expanded(row):
            self._expanded.add(id(row))
            self._reflow()
            self._scroll_to(row)
        self.redraw()

    def reapply_sort(self) -> None:
        """Reaplica a ordenacao ativa depois de trocar as linhas."""
        if self._sort_key:
            self._apply_sort()

    # --------------------------------------------------------------- layout
    def _total_width(self) -> int:
        return sum(c.width for c in self.columns) + 2

    def _col_x(self, index: int) -> int:
        return sum(c.width for c in self.columns[:index])

    def _reflow(self) -> None:
        self._items = []
        for row in self._rows:
            self._items.append(("data", row, theme.DIMS["grid_row"]))
            if id(row) in self._expanded and self.detail:
                pairs = self.detail(row)
                lines = max(1, (len(pairs) + 3) // 4)
                height = 14 + lines * 30 + 10
                self._items.append(("detail", row, height))
        self._offsets = [0]
        for _, _, h in self._items:
            self._offsets.append(self._offsets[-1] + h)
        self.body.configure(scrollregion=(0, 0, self._total_width(), max(1, self._offsets[-1])))
        self.header.configure(scrollregion=(0, 0, self._total_width(), theme.DIMS["grid_header"]))

    def _scroll_to(self, row: Any) -> None:
        for i, (_, r, _) in enumerate(self._items):
            if r is row:
                total = self._offsets[-1] or 1
                self.body.yview_moveto(self._offsets[i] / total)
                return

    def _visible_range(self) -> tuple[int, int]:
        top = self.body.canvasy(0)
        bottom = top + self.body.winfo_height()
        first = bisect.bisect_right(self._offsets, top) - 1
        last = bisect.bisect_left(self._offsets, bottom)
        return max(0, first), min(len(self._items), last + 1)

    # -------------------------------------------------------------- desenho
    def _draw_header(self) -> None:
        cv = self.header
        cv.delete("all")
        c = theme.COLORS
        h = theme.DIMS["grid_header"]
        for i, col in enumerate(self.columns):
            x = self._col_x(i)
            active = self._sort_key == col.key
            fg = c["accent"] if active else c["text_muted"]
            self._header_text(cv, col.title.upper(), x, col.width, h, col.align,
                              theme.FONTS["label"], fg)
            if active:
                ax = x + col.width - 14 if col.align == "e" else x + col.width - 14
                if self._sort_desc:
                    cv.create_polygon(ax - 4, h / 2 - 1, ax + 4, h / 2 - 1, ax, h / 2 + 4,
                                      fill=c["accent"], outline="")
                else:
                    cv.create_polygon(ax - 4, h / 2 + 4, ax + 4, h / 2 + 4, ax, h / 2 - 1,
                                      fill=c["accent"], outline="")
        cv.create_line(0, h - 0.5, self._total_width(), h - 0.5, fill=c["border"])
        cv.create_line(0, h - 1.5, self._total_width(), h - 1.5, fill=c["border_soft"])

    def _header_text(self, cv: tk.Canvas, text: str, x: int, w: int, h: int,
                     align: str, font: Any, fill: str) -> None:
        pad = 10
        if align == "e":
            cv.create_text(x + w - pad, h / 2, text=text, anchor="e", fill=fill, font=font)
        elif align == "center":
            cv.create_text(x + w / 2, h / 2, text=text, fill=fill, font=font)
        else:
            cv.create_text(x + pad, h / 2, text=text, anchor="w", fill=fill, font=font)

    def _draw_empty(self, w: int, h: int) -> None:
        c = theme.COLORS
        cy = h / 2 - 10
        self.body.create_oval(w / 2 - 15, cy - 15, w / 2 + 15, cy + 15,
                              outline=c["border"], width=1)
        self.body.create_text(w / 2, cy, text="⌕", fill=c["text_dim"],
                              font=theme.FONTS["h1"])
        self.body.create_text(w / 2, cy + 38, text=self.empty_title,
                              fill=c["text_muted"], font=theme.FONTS["ui_bold"])
        if self.empty_hint:
            self.body.create_text(w / 2, cy + 58, text=self.empty_hint,
                                  fill=c["text_dim"], font=theme.FONTS["ui_sm"])

    def redraw(self) -> None:
        cv = self.body
        cv.delete("all")
        w = max(1, cv.winfo_width())
        h = max(1, cv.winfo_height())
        if not self._items:
            self._draw_empty(w, h)
            return
        c = theme.COLORS
        first, last = self._visible_range()
        total_w = self._total_width()
        for idx in range(first, last):
            kind, row, height = self._items[idx]
            y = self._offsets[idx]
            if kind == "detail":
                self._draw_detail(row, y, height, total_w)
                continue
            self._draw_row(idx, row, y, height, w, total_w)

    def _draw_row(self, idx: int, row: Any, y: int, height: int, w: int, total_w: int) -> None:
        cv = self.body
        c = theme.COLORS
        selected = row is self._selected
        hovered = idx == self._hover
        if selected:
            bg = c["row_selected"]
        elif hovered:
            bg = c["row_hover"]
        else:
            bg = c["row_even"] if (idx % 2 == 0) else c["row_odd"]
        cv.create_rectangle(0, y, max(w, total_w), y + height, fill=bg, outline="")
        cv.create_line(0, y + height - 0.5, max(w, total_w), y + height - 0.5,
                       fill=c["border_soft"])
        first_col = self.columns[0] if self.columns else None
        if first_col is not None:
            cell = first_col.cell(row)
            accent = cell.color if cell.color != c["text"] else c["border"]
            cv.create_rectangle(0, y, 2.5, y + height, fill=accent, outline="")
        for i, col in enumerate(self.columns):
            x = self._col_x(i)
            if x > self.body.canvasx(w):
                break
            if x + col.width < self.body.canvasx(0):
                continue
            cell = col.cell(row)
            self._draw_cell(cell, x, y, col.width, height, col.align)
        if selected:
            cv.create_rectangle(0, y + 1, max(w, total_w), y + height - 1,
                                outline=c["focus_ring"], width=1)

    def _draw_cell(self, cell: Cell, x: int, y: int, w: int, h: int, align: str) -> None:
        cv = self.body
        pad = 10
        if cell.bar is not None:
            bw = max(0.0, min(1.0, cell.bar)) * (w - 2 * pad)
            bar_color = cell.bar_color or cell.color
            cv.create_rectangle(x + pad, y + h - 6, x + pad + bw, y + h - 4,
                                fill=theme.mix(theme.COLORS["surface_2"], bar_color, 0.55),
                                outline="")
        fnt = theme.FONTS[cell.font]
        max_px = w - 2 * pad
        text = theme.truncate(cell.text, cell.font, max_px)
        if align == "e":
            cv.create_text(x + w - pad, y + h / 2, text=text, anchor="e",
                           fill=cell.color, font=fnt)
        elif align == "center":
            cv.create_text(x + w / 2, y + h / 2, text=text, fill=cell.color, font=fnt)
        else:
            cv.create_text(x + pad, y + h / 2, text=text, anchor="w",
                           fill=cell.color, font=fnt)

    def _draw_detail(self, row: Any, y: int, height: int, total_w: int) -> None:
        cv = self.body
        c = theme.COLORS
        cv.create_rectangle(0, y, total_w, y + height, fill=c["surface_2"], outline="")
        cv.create_line(0, y + 0.5, total_w, y + 0.5, fill=c["border"])
        cv.create_line(0, y + height - 0.5, total_w, y + height - 0.5, fill=c["border_soft"])
        cv.create_rectangle(0, y, 2.5, y + height, fill=c["accent"], outline="")
        pairs = self.detail(row) if self.detail else []
        if not pairs:
            return
        cols = 4
        pad = 16
        col_w = (total_w - 2 * pad) / cols
        for i, (label, value) in enumerate(pairs):
            r, cc = divmod(i, cols)
            x = pad + cc * col_w
            yy = y + 12 + r * 30
            cv.create_text(x, yy, text=label.upper(), anchor="w",
                           fill=c["text_dim"], font=theme.FONTS["label"])
            cv.create_text(x, yy + 14, text=value, anchor="w",
                           fill=c["text"], font=theme.FONTS["data_sm"])

    # --------------------------------------------------------------- eventos
    def _on_yscroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        self.redraw()

    def _on_xscroll(self, first: str, last: str) -> None:
        self.hbar.set(first, last)
        self.header.xview_moveto(first)
        self.redraw()

    def _wheel(self, event: tk.Event) -> str:
        delta = -1 if event.delta > 0 else 1
        self.body.yview_scroll(delta * 3, "units")
        return "break"

    def _index_at(self, y: int) -> int:
        cy = self.body.canvasy(y)
        i = bisect.bisect_right(self._offsets, cy) - 1
        return i if 0 <= i < len(self._items) else -1

    def _motion(self, event: tk.Event) -> None:
        idx = self._index_at(event.y)
        if idx != self._hover:
            self._hover = idx
            self.redraw()

    def _leave(self, _e: tk.Event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.redraw()

    def _click(self, event: tk.Event) -> None:
        self.body.focus_set()
        idx = self._index_at(event.y)
        if idx < 0:
            return
        kind, row, _ = self._items[idx]
        if kind == "detail":
            return
        if row is self._selected:
            self._toggle(row)
        else:
            self._selected = row
            if self.on_select:
                self.on_select(row)
        self.redraw()

    def _double_click(self, event: tk.Event) -> None:
        idx = self._index_at(event.y)
        if idx < 0:
            return
        kind, row, _ = self._items[idx]
        if kind == "data":
            self._toggle(row)

    def _toggle(self, row: Any) -> None:
        if not self.detail:
            return
        if id(row) in self._expanded:
            self._expanded.discard(id(row))
        else:
            self._expanded.add(id(row))
        self._reflow()
        self.redraw()

    def _toggle_selected(self) -> None:
        if self._selected is not None:
            self._toggle(self._selected)

    def _move(self, step: int) -> None:
        if not self._rows:
            return
        if self._selected is None:
            self._selected = self._rows[0]
        else:
            try:
                i = self._rows.index(self._selected)
            except ValueError:
                i = 0
            i = max(0, min(len(self._rows) - 1, i + step))
            self._selected = self._rows[i]
        self._scroll_to(self._selected)
        if self.on_select:
            self.on_select(self._selected)
        self.redraw()

    def _header_click(self, event: tk.Event) -> None:
        x = self.header.canvasx(event.x)
        for i, col in enumerate(self.columns):
            if self._col_x(i) <= x <= self._col_x(i) + col.width:
                if col.sort_key is None:
                    return
                if self._sort_key == col.key:
                    self._sort_desc = not self._sort_desc
                else:
                    self._sort_key = col.key
                    self._sort_desc = True
                self._apply_sort()
                return

    def _header_motion(self, event: tk.Event) -> None:
        x = self.header.canvasx(event.x)
        for i, col in enumerate(self.columns):
            if self._col_x(i) <= x <= self._col_x(i) + col.width:
                self.configure(cursor="hand2" if col.sort_key else "arrow")
                if col.tooltip:
                    self._show_tooltip(col.tooltip)
                else:
                    self._hide_tooltip()
                return

    def _show_tooltip(self, text: str) -> None:
        if self._tooltip is None:
            self._tooltip = Tooltip(self.header, text, delay=350)
        self._tooltip.set_text(text)

    def _hide_tooltip(self) -> None:
        if self._tooltip is not None:
            self._tooltip._hide()

    def _apply_sort(self) -> None:
        col = next((c for c in self.columns if c.key == self._sort_key), None)
        if col is None or col.sort_key is None:
            return
        self._rows.sort(key=col.sort_key, reverse=self._sort_desc)
        self._expanded.clear()
        self._reflow()
        self.body.yview_moveto(0)
        self._draw_header()
        self.redraw()

    # ------------------------------------------------------------ clipboard
    def _row_text(self, row: Any) -> str:
        parts = []
        for col in self.columns:
            parts.append(f"{col.title}: {col.cell(row).text}")
        return " | ".join(parts)

    def _copy_selected(self) -> str:
        if self._selected is not None:
            self.clipboard_clear()
            self.clipboard_append(self._row_text(self._selected))
        return "break"

    def _context(self, event: tk.Event) -> None:
        idx = self._index_at(event.y)
        if idx < 0:
            return
        kind, row, _ = self._items[idx]
        if kind == "detail":
            return
        self._selected = row
        self.redraw()
        c = theme.COLORS
        menu = tk.Menu(self, tearoff=0, bg=c["surface_2"], fg=c["text"],
                       activebackground=c["surface_3"], activeforeground=c["accent"],
                       borderwidth=0, relief="flat", font=theme.FONTS["ui"])
        menu.add_command(label="  Copiar linha  ",
                         command=lambda: (self.clipboard_clear(),
                                          self.clipboard_append(self._row_text(row))))
        menu.add_command(label="  Expandir / recolher detalhes  ",
                         command=lambda: self._toggle(row))
        if self.on_select:
            menu.add_command(label="  Selecionar  ",
                             command=lambda: self.on_select(row))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


# --------------------------------------------------------------------------
# Navegacao
# --------------------------------------------------------------------------


class TabBar(tk.Canvas):
    """Abas com indicador inferior e mudanca sutil de fundo na ativa."""

    def __init__(
        self,
        master: tk.Misc,
        tabs: Sequence[tuple[str, str]],
        *,
        command: Callable[[str], None],
        height: int | None = None,
    ) -> None:
        self.tabs = list(tabs)
        self.command = command
        self.active = self.tabs[0][0] if self.tabs else ""
        h = height or theme.DIMS["tabs"]
        super().__init__(master, height=h, bg=theme.COLORS["bg"],
                         highlightthickness=0, bd=0)
        self._boxes: list[tuple[float, float, float, float, str]] = []
        self._hover: str | None = None
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<Configure>", lambda e: self._draw())

    def set_active(self, key: str) -> None:
        self.active = key
        self._draw()

    def _layout(self) -> list[tuple[float, float, float, float, str]]:
        fnt = theme.FONTS["ui_bold"]
        out = []
        x = 4.0
        h = float(self.winfo_height() or theme.DIMS["tabs"])
        for key, label in self.tabs:
            w = fnt.measure(label) + 30
            out.append((x, 0.0, x + w, h, key))
            x += w + 2
        return out

    def _hit(self, x: float, y: float) -> str | None:
        for x1, y1, x2, y2, key in self._boxes:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _click(self, event: tk.Event) -> None:
        key = self._hit(event.x, event.y)
        if key and key != self.active:
            self.active = key
            self._draw()
            self.command(key)

    def _motion(self, event: tk.Event) -> None:
        key = self._hit(event.x, event.y)
        if key != self._hover:
            self._hover = key
            self.configure(cursor="hand2" if key else "arrow")
            self._draw()

    def _leave(self, _e: tk.Event) -> None:
        self._hover = None
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        c = theme.COLORS
        self._boxes = self._layout()
        h = self.winfo_height() or theme.DIMS["tabs"]
        labels = dict(self.tabs)
        self.create_line(0, h - 0.5, self.winfo_width(), h - 0.5, fill=c["border"])
        for x1, _, x2, _, key in self._boxes:
            active = key == self.active
            hover = key == self._hover
            if active:
                self.create_rectangle(x1, 0, x2, h, fill=c["surface_1"], outline="")
                self.create_rectangle(x1 + 8, h - 2, x2 - 8, h, fill=c["accent"], outline="")
                fg = c["accent"]
            elif hover:
                self.create_rectangle(x1, 0, x2, h, fill=c["surface_1"], outline="")
                fg = c["text"]
            else:
                fg = c["text_muted"]
            self.create_text((x1 + x2) / 2, h / 2, text=labels[key], fill=fg,
                             font=theme.FONTS["ui_bold"])


def ttk_scrollbar(master: tk.Misc, orient: str, command: Callable) -> Any:
    """Scrollbar ttk com o estilo do design system."""
    from tkinter import ttk

    style = "Betgsn.Vertical.TScrollbar" if orient == "vertical" else "Betgsn.Horizontal.TScrollbar"
    return ttk.Scrollbar(master, orient=orient, command=command, style=style)
