"""BETGSN :: theme — design system unico da interface.

Centraliza cor, fonte, espacamento, raio e altura de componente. Nenhum
widget deve declarar cor ou fonte propria: tudo sai daqui, para que a
aplicacao inteira pareca o mesmo produto.

Uso:
    from . import theme
    theme.init(root)                 # resolve fontes e estiliza o ttk
    frame = tk.Frame(root, bg=theme.COLORS["surface_1"])
    canvas.create_text(..., font=theme.FONTS["data"])

``init`` precisa de um root Tk vivo porque a resolucao de familia de fonte
depende do sistema (``tkinter.font.families``). Chamar duas vezes e seguro:
a segunda chamada e um no-op barato.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# --------------------------------------------------------------------------
# Cor
# --------------------------------------------------------------------------

COLORS: dict[str, str] = {
    # superficies
    "bg": "#0A0D13",
    "surface_1": "#10141C",
    "surface_2": "#161C26",
    "surface_3": "#1B222E",
    "border": "#232B38",
    "border_soft": "#1A212C",
    # texto
    "text": "#E9EDF3",
    "text_muted": "#7C8698",
    "text_dim": "#4E5563",
    # semantica
    "accent": "#F2B84B",          # sinal FORTE, elemento primario
    "accent_dim": "#8A6B2A",
    "accent_wash": "#1E1A12",
    "secondary": "#5B8DEF",       # sinal MEDIA, informacao auxiliar
    "secondary_dim": "#2F4C80",
    "secondary_wash": "#121722",
    "positive": "#34D399",        # EV positivo, lucro esperado
    "positive_dim": "#1F6B52",
    "negative": "#F0607A",        # risco, erro, EV negativo
    "negative_dim": "#7A2F3E",
    "neutral": "#9AA4B4",
    # estados de linha
    "row_even": "#10141C",
    "row_odd": "#0D1118",
    "row_hover": "#171E2A",
    "row_selected": "#1C2533",
    "focus_ring": "#3A4757",
}

# --------------------------------------------------------------------------
# Espacamento, raio e metrica de componente (densidade alta, app de dados)
# --------------------------------------------------------------------------

SPACING: dict[str, int] = {
    "xxs": 2,
    "xs": 4,
    "sm": 6,
    "md": 10,
    "lg": 14,
    "xl": 20,
    "xxl": 28,
}

RADIUS: dict[str, int] = {
    "sm": 3,
    "md": 5,
    "lg": 8,
    "pill": 999,
}

DIMS: dict[str, int] = {
    "header": 66,
    "param_bar": 58,
    "tabs": 40,
    "kpi": 68,
    "filter_bar": 46,
    "grid_header": 32,
    "grid_row": 38,
    "grid_detail_line": 34,
    "insights": 116,
    "status": 28,
    "button_h": 32,
    "input_h": 28,
    "chip_h": 24,
    "toast_w": 300,
    "toast_h": 42,
}

# --------------------------------------------------------------------------
# Fonte
# --------------------------------------------------------------------------

# Pilhas de familia: primeira instalada vence. Space Grotesk / IBM Plex Mono
# raramente existem no Windows limpo, entao a pilha cai para as familias
# nativas de melhor legibilidade mantendo a mesma linguagem visual
# (humanista para UI, monoespacada de alto contraste para numero).
_UI_STACK = (
    "Space Grotesk",
    "Segoe UI Variable Display",
    "Segoe UI",
    "Inter",
    "Bahnschrift",
    "Tahoma",
)
_MONO_STACK = (
    "IBM Plex Mono",
    "JetBrains Mono",
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "Lucida Console",
    "Courier New",
)

FAMILIES: dict[str, str] = {"ui": "Segoe UI", "mono": "Consolas"}

#: fontes prontas para uso em widget e canvas (objetos tkfont.Font, medem texto)
FONTS: dict[str, tkfont.Font] = {}

_SPECS: dict[str, tuple[str, int, str]] = {
    # role            familia  tamanho  peso
    "brand": ("ui", 15, "bold"),
    "h1": ("ui", 12, "bold"),
    "h2": ("ui", 10, "bold"),
    "ui": ("ui", 10, "normal"),
    "ui_bold": ("ui", 10, "bold"),
    "ui_sm": ("ui", 9, "normal"),
    "ui_sm_bold": ("ui", 9, "bold"),
    "label": ("ui", 8, "bold"),        # micro-label maiusculo dos parametros
    "data": ("mono", 10, "normal"),
    "data_bold": ("mono", 10, "bold"),
    "data_sm": ("mono", 9, "normal"),
    "data_lg": ("mono", 15, "bold"),   # valor de KPI
    "data_xs": ("mono", 8, "normal"),
}

_initialised = False


def _pick_family(candidates: tuple[str, ...], installed: set[str], fallback: str) -> str:
    for name in candidates:
        if name in installed:
            return name
    return fallback


def init(root: tk.Misc, *, force: bool = False) -> None:
    """Resolve familias de fonte e aplica o tema ttk. Idempotente."""
    global _initialised
    if _initialised and not force:
        return
    installed = set(tkfont.families(root))
    FAMILIES["ui"] = _pick_family(_UI_STACK, installed, "Segoe UI")
    FAMILIES["mono"] = _pick_family(_MONO_STACK, installed, "Consolas")
    for role, (family_key, size, weight) in _SPECS.items():
        FONTS[role] = tkfont.Font(
            root=root, family=FAMILIES[family_key], size=size, weight=weight
        )
    _style_ttk(root)
    _initialised = True


def _style_ttk(root: tk.Misc) -> None:
    """Estiliza os widgets ttk que sobrevivem (scrollbar, entry, combobox).

    O objetivo e nao existir "widget antigo dentro de tela nova": a
    scrollbar e o campo de texto tem que nascer do mesmo design system.
    """
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass

    c = COLORS
    st.configure(".", background=c["bg"], foreground=c["text"], font=FONTS["ui"])
    st.configure("TFrame", background=c["bg"])
    st.configure("TLabel", background=c["bg"], foreground=c["text"])

    for orient in ("Vertical", "Horizontal"):
        st.configure(
            f"Betgsn.{orient}.TScrollbar",
            troughcolor=c["bg"],
            background=c["surface_3"],
            darkcolor=c["surface_3"],
            lightcolor=c["surface_3"],
            bordercolor=c["bg"],
            arrowcolor=c["text_dim"],
            gripcount=0,
            relief="flat",
            borderwidth=0,
            arrowsize=11,
            width=11,
        )
        st.map(
            f"Betgsn.{orient}.TScrollbar",
            background=[("active", c["border"]), ("pressed", c["accent_dim"])],
            arrowcolor=[("active", c["text_muted"])],
        )

    st.configure(
        "Betgsn.TEntry",
        fieldbackground=c["surface_2"],
        background=c["surface_2"],
        foreground=c["text"],
        bordercolor=c["border"],
        lightcolor=c["border"],
        darkcolor=c["border"],
        insertcolor=c["accent"],
        borderwidth=1,
        relief="flat",
        padding=(6, 3),
    )
    st.map(
        "Betgsn.TEntry",
        bordercolor=[("focus", c["accent_dim"])],
        lightcolor=[("focus", c["accent_dim"])],
        darkcolor=[("focus", c["accent_dim"])],
    )


# --------------------------------------------------------------------------
# Helpers de cor e desenho
# --------------------------------------------------------------------------


def confidence_color(level: str) -> str:
    """Cor canonica de um nivel de confianca (dourado/azul/neutro)."""
    return {
        "FORTE": COLORS["accent"],
        "MEDIA": COLORS["secondary"],
        "FRACA": COLORS["neutral"],
    }.get(level.upper(), COLORS["text_muted"])


def confidence_wash(level: str) -> str:
    return {
        "FORTE": COLORS["accent_wash"],
        "MEDIA": COLORS["secondary_wash"],
    }.get(level.upper(), COLORS["surface_2"])


def signed_color(value: float, *, neutral_zero: bool = True) -> str:
    """Verde para positivo, vermelho para negativo, cinza para zero."""
    if value > 0:
        return COLORS["positive"]
    if value < 0:
        return COLORS["negative"]
    return COLORS["text_muted"] if neutral_zero else COLORS["text"]


def mix(color_a: str, color_b: str, t: float) -> str:
    """Interpola dois #rrggbb. t=0 -> color_a, t=1 -> color_b."""
    t = max(0.0, min(1.0, t))
    a = tuple(int(color_a[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(color_b[i : i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: int,
    **kwargs,
) -> int:
    """Retangulo de canto arredondado como poligono suavizado.

    Canvas do Tk nao tem primitiva de canto redondo; o poligono com
    ``smooth=True`` da o mesmo resultado com um unico item (importante para
    manter o numero de itens baixo em telas densas).
    """
    r = max(0, min(radius, int(abs(x2 - x1) / 2), int(abs(y2 - y1) / 2)))
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


def truncate(text: str, font_key: str, max_px: int) -> str:
    """Corta texto com elipse para caber em max_px, medindo na fonte real."""
    fnt = FONTS[font_key]
    if max_px <= 0 or not text:
        return ""
    if fnt.measure(text) <= max_px:
        return text
    ell = "…"
    ell_w = fnt.measure(ell)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fnt.measure(text[:mid]) + ell_w <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + ell) if lo else ""
