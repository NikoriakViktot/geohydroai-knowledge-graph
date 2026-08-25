"""
layout.py  —  App shell: sidebar nav + page content area
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS, MANTINE_THEME
from src.dashboard_dash.styles import GLASS_CARD

_NAV_ITEMS = [
    {"href": "/",              "label": "Overview",        "icon": "○"},
    {"href": "/scientometrics","label": "Scientometrics",  "icon": "◈"},
    {"href": "/methods",       "label": "Methods & Sensors","icon": "◆"},
    {"href": "/topics",        "label": "Topics & Trends", "icon": "◉"},
    {"href": "/citations",     "label": "Citation Network","icon": "⬡"},
    {"href": "/geospatial",    "label": "Geospatial",      "icon": "◎"},
    {"href": "/quality",       "label": "Data Quality",    "icon": "◇"},
    {"href": "/explorer",      "label": "Paper Explorer",  "icon": "◑"},
    {"href": "/flood",         "label": "Flood Mapping",   "icon": "◍"},
    {"href": "/analytics",     "label": "Analytics",       "icon": "◬"},
    {"href": "/research",      "label": "Research",        "icon": "◭"},
    {"href": "/notebooks",     "label": "Notebooks",       "icon": "◻"},
]


def _nav_link(item: dict, current: str) -> html.A:
    active  = current == item["href"] or (item["href"] != "/" and current.startswith(item["href"]))
    bg      = "rgba(0,212,255,0.08)" if active else "transparent"
    color   = COLORS["accent_cyan"] if active else COLORS["text_secondary"]
    border  = f"2px solid {COLORS['accent_cyan']}" if active else "2px solid transparent"

    return html.A(
        html.Div([
            html.Span(item["icon"], style={"fontSize": "14px", "minWidth": "20px", "textAlign": "center"}),
            html.Span(item["label"], style={"fontSize": "13px", "fontWeight": 600 if active else 400}),
        ], style={
            "display":    "flex",
            "alignItems": "center",
            "gap":        "10px",
            "padding":    "9px 14px",
            "borderRadius": "8px",
            "background": bg,
            "color":      color,
            "borderLeft": border,
            "transition": "all 0.15s",
            "marginBottom": "2px",
        }),
        href=item["href"],
        style={"textDecoration": "none"},
    )


def sidebar(pathname: str) -> html.Div:
    return html.Div([
        # Logo area
        html.Div([
            html.Div([
                html.Span("⬡", style={
                    "fontSize": "22px", "color": COLORS["accent_cyan"],
                    "fontWeight": 800,
                }),
                html.Div([
                    html.Div("GeoHydroAI", style={
                        "color": COLORS["text_primary"], "fontWeight": 800,
                        "fontSize": "14px", "letterSpacing": "0.01em",
                    }),
                    html.Div("Research Analytics", style={
                        "color": COLORS["text_secondary"], "fontSize": "10px",
                        "letterSpacing": "0.05em", "textTransform": "uppercase",
                    }),
                ]),
            ], style={"display": "flex", "alignItems": "center", "gap": "10px"}),
        ], style={
            "padding":        "18px 16px 16px",
            "borderBottom":   f"1px solid {COLORS['border']}",
            "marginBottom":   "12px",
        }),

        # Nav items
        html.Div([
            _nav_link(item, pathname) for item in _NAV_ITEMS
        ], style={"padding": "0 10px"}),

        # Footer
        html.Div([
            html.Div(style={"borderTop": f"1px solid {COLORS['border']}", "marginTop": "auto"}),
            html.Div([
                html.Span("v1.0 · Parquet layer", style={
                    "color": COLORS["text_dim"], "fontSize": "10px",
                }),
            ], style={"padding": "14px 16px 8px"}),
        ], style={"position": "absolute", "bottom": "0", "left": "0", "right": "0"}),
    ], style={
        "background":  COLORS["bg_sidebar"],
        "height":      "100vh",
        "position":    "fixed",
        "top":         "0",
        "left":        "0",
        "width":       "220px",
        "borderRight": f"1px solid {COLORS['border']}",
        "overflowY":   "auto",
        "zIndex":      "100",
    })


def build_layout() -> dmc.MantineProvider:
    return dmc.MantineProvider(
        theme=MANTINE_THEME,
        forceColorScheme="dark",
        children=html.Div([
            dcc.Location(id="url", refresh=False),

            # ── Global filter store ────────────────────────────────────────────
            dcc.Store(id="filter-store", data={
                "year_min":    1995,
                "year_max":    2025,
                "countries":   [],
                "study_types": [],
                "min_citations": 0,
                "has_doi":     False,
                "has_openalex": False,
            }),
            dcc.Store(id="filter-options-store", data={}),

            # ── Sidebar (static — rebuilt on URL change) ────────────────────────
            html.Div(id="sidebar-container"),

            # ── Main content area ──────────────────────────────────────────────
            html.Div([
                # Page content
                html.Div(id="page-content"),
            ], style={
                "marginLeft":  "220px",
                "minHeight":   "100vh",
                "background":  COLORS["bg_base"],
            }),
        ], style={
            "background": COLORS["bg_base"],
            "minHeight":  "100vh",
            "fontFamily": "Inter, system-ui, sans-serif",
        }),
    )
