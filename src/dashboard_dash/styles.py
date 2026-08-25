"""
styles.py  —  Shared inline style dicts and CSS-in-Python helpers
"""
from src.dashboard_dash.theme import COLORS

# ── Glassmorphism card ─────────────────────────────────────────────────────────
GLASS_CARD = {
    "background": COLORS["glass_bg"],
    "border":     f"1px solid {COLORS['glass_border']}",
    "backdropFilter": "blur(12px)",
    "borderRadius": "12px",
    "padding":    "20px",
}

# ── KPI metric card ────────────────────────────────────────────────────────────
KPI_CARD = {
    **GLASS_CARD,
    "textAlign": "center",
    "minHeight": "110px",
    "display":   "flex",
    "flexDirection": "column",
    "justifyContent": "center",
    "gap": "6px",
    "transition": "border-color 0.2s ease",
}

# ── Section heading ────────────────────────────────────────────────────────────
SECTION_TITLE = {
    "color":        COLORS["text_primary"],
    "fontWeight":   700,
    "fontSize":     "16px",
    "letterSpacing":"0.04em",
    "textTransform":"uppercase",
    "marginBottom": "12px",
}

# ── Chart container ────────────────────────────────────────────────────────────
CHART_BOX = {
    **GLASS_CARD,
    "padding": "16px",
    "minHeight": "280px",
}

# ── Sidebar link ───────────────────────────────────────────────────────────────
NAV_LINK = {
    "borderRadius": "8px",
    "padding":      "10px 14px",
    "marginBottom": "4px",
    "color":        COLORS["text_secondary"],
    "fontWeight":   500,
    "fontSize":     "14px",
}

NAV_LINK_ACTIVE = {
    **NAV_LINK,
    "background": f"linear-gradient(90deg, rgba(0,212,255,0.12) 0%, rgba(8,145,178,0.06) 100%)",
    "color":      COLORS["accent_cyan"],
    "borderLeft": f"2px solid {COLORS['accent_cyan']}",
}

# ── Filter sidebar ─────────────────────────────────────────────────────────────
FILTER_LABEL = {
    "color":      COLORS["text_secondary"],
    "fontSize":   "11px",
    "fontWeight": 600,
    "letterSpacing": "0.06em",
    "textTransform": "uppercase",
    "marginBottom": "6px",
}

# ── Table ─────────────────────────────────────────────────────────────────────
TABLE_STYLE = {
    "width":       "100%",
    "borderCollapse": "collapse",
    "fontSize":    "13px",
    "color":       COLORS["text_primary"],
}

TABLE_HEADER = {
    "background":  COLORS["bg_surface"],
    "color":       COLORS["text_secondary"],
    "padding":     "10px 14px",
    "textAlign":   "left",
    "fontWeight":  600,
    "fontSize":    "11px",
    "letterSpacing": "0.06em",
    "textTransform": "uppercase",
    "borderBottom": f"1px solid {COLORS['border']}",
}

TABLE_ROW = {
    "borderBottom": f"1px solid {COLORS['border']}",
    "padding":     "9px 14px",
    "transition":  "background 0.15s",
}

# ── Badge variants ─────────────────────────────────────────────────────────────
def badge_style(color: str = "cyan") -> dict:
    palette = {
        "cyan":  (COLORS["accent_cyan"],  "rgba(0,212,255,0.12)"),
        "teal":  (COLORS["accent_teal"],  "rgba(8,145,178,0.12)"),
        "green": (COLORS["accent_green"], "rgba(16,185,129,0.12)"),
        "amber": (COLORS["accent_amber"], "rgba(245,158,11,0.12)"),
        "red":   (COLORS["accent_red"],   "rgba(239,68,68,0.12)"),
    }
    fg, bg = palette.get(color, palette["cyan"])
    return {
        "background":  bg,
        "color":       fg,
        "border":      f"1px solid {fg}22",
        "borderRadius":"6px",
        "padding":     "2px 8px",
        "fontSize":    "11px",
        "fontWeight":  600,
        "display":     "inline-block",
    }


def gradient_text(text: str, from_color: str = "#00d4ff", to_color: str = "#0891b2") -> dict:
    """Return style dict for gradient text."""
    return {
        "background": f"linear-gradient(90deg, {from_color}, {to_color})",
        "WebkitBackgroundClip": "text",
        "WebkitTextFillColor": "transparent",
        "fontWeight": 700,
    }


# ── Scrollable container ───────────────────────────────────────────────────────
SCROLLABLE = {
    "overflowY": "auto",
    "overflowX": "hidden",
    "maxHeight": "calc(100vh - 160px)",
    "scrollbarWidth": "thin",
    "scrollbarColor": f"{COLORS['border']} transparent",
}

# ── Page wrapper ───────────────────────────────────────────────────────────────
PAGE_WRAPPER = {
    "padding":   "24px",
    "minHeight": "100vh",
    "background": COLORS["bg_base"],
}
