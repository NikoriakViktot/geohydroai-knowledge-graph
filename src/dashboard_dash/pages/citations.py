"""
citations.py  —  Ego-centric citation network page (dash-cytoscape)
"""
from dash import html, dcc
import dash_mantine_components as dmc
import dash_cytoscape as cyto

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header
from src.dashboard_dash.components.empty_states import no_data

# Load extra layouts
cyto.load_extra_layouts()

# ── Cytoscape stylesheet ───────────────────────────────────────────────────────
CYTO_STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "background-color":   COLORS["accent_teal"],
            "label":              "data(label)",
            "color":              COLORS["text_primary"],
            "font-size":          "10px",
            "text-valign":        "bottom",
            "text-halign":        "center",
            "text-margin-y":      "4px",
            "text-max-width":     "100px",
            "text-wrap":          "ellipsis",
            "width":              "data(size)",
            "height":             "data(size)",
            "border-color":       COLORS["bg_base"],
            "border-width":       "2px",
        },
    },
    {
        "selector": "node[type='paper']",
        "style": {
            "background-color": COLORS["accent_cyan"],
            "shape": "ellipse",
        },
    },
    {
        "selector": "node[type='reference']",
        "style": {
            "background-color": COLORS["accent_teal"],
            "shape": "diamond",
        },
    },
    {
        "selector": "node[type='root']",
        "style": {
            "background-color": COLORS["accent_amber"],
            "border-color":     COLORS["accent_amber"],
            "border-width":     "3px",
            "width": "42px",
            "height": "42px",
        },
    },
    {
        "selector": "edge",
        "style": {
            "line-color":      COLORS["border_light"],
            "target-arrow-color": COLORS["border_light"],
            "target-arrow-shape": "triangle",
            "curve-style":    "bezier",
            "width":          1.5,
            "opacity":        0.6,
            "arrow-scale":    0.8,
        },
    },
    {
        "selector": ":selected",
        "style": {
            "border-color": COLORS["accent_amber"],
            "border-width": "3px",
        },
    },
]


def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Citation Network", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P(
                "Ego-centric citation exploration · select a paper to render its citation neighbourhood",
                style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
            ),
        ], style={"marginBottom": "24px"}),

        # ── Controls ───────────────────────────────────────────────────────────
        html.Div([
            dmc.Grid([
                dmc.GridCol([
                    html.P("Search paper by title or DOI", style={
                        "color": COLORS["text_secondary"], "fontSize": "11px",
                        "fontWeight": 600, "textTransform": "uppercase",
                        "letterSpacing": "0.06em", "margin": "0 0 6px 0",
                    }),
                    dcc.Input(
                        id="citation-search",
                        type="text",
                        placeholder="e.g. flood risk assessment, HEC-RAS…",
                        debounce=True,
                        style={
                            "width": "100%", "padding": "10px 14px",
                            "background": COLORS["bg_surface"],
                            "border": f"1px solid {COLORS['border']}",
                            "borderRadius": "8px", "color": COLORS["text_primary"],
                            "fontSize": "13px", "outline": "none",
                        },
                    ),
                ], span=6),
                dmc.GridCol([
                    html.P("Select paper", style={
                        "color": COLORS["text_secondary"], "fontSize": "11px",
                        "fontWeight": 600, "textTransform": "uppercase",
                        "letterSpacing": "0.06em", "margin": "0 0 6px 0",
                    }),
                    dmc.Select(
                        id="citation-paper-select",
                        data=[],
                        placeholder="Choose from search results…",
                        searchable=True,
                        clearable=True,
                        styles={
                            "input":   {"backgroundColor": COLORS["bg_surface"], "borderColor": COLORS["border"], "color": COLORS["text_primary"], "fontSize": "12px"},
                            "dropdown": {"backgroundColor": COLORS["bg_card"]},
                        },
                    ),
                ], span=4),
                dmc.GridCol([
                    html.P("Max nodes", style={
                        "color": COLORS["text_secondary"], "fontSize": "11px",
                        "fontWeight": 600, "textTransform": "uppercase",
                        "letterSpacing": "0.06em", "margin": "0 0 6px 0",
                    }),
                    dmc.Slider(
                        id="citation-max-nodes",
                        min=20, max=150, value=80, step=10,
                        color="cyan",
                        marks=[{"value": 50, "label": "50"}, {"value": 100, "label": "100"}, {"value": 150, "label": "150"}],
                    ),
                ], span=2),
            ], gutter="lg"),
        ], style={**GLASS_CARD, "marginBottom": "20px"}),

        # ── Main layout: graph + info panel ───────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                html.Div([
                    cyto.Cytoscape(
                        id="citation-graph",
                        elements=[],
                        layout={"name": "cose", "animate": True, "animationDuration": 500},
                        style={"width": "100%", "height": "520px", "background": COLORS["bg_base"]},
                        stylesheet=CYTO_STYLESHEET,
                        responsive=True,
                        minZoom=0.2,
                        maxZoom=3,
                    ),
                    # Legend
                    html.Div([
                        html.Div([
                            html.Span(style={"width": "10px", "height": "10px", "borderRadius": "50%", "background": COLORS["accent_amber"], "display": "inline-block", "marginRight": "6px"}),
                            html.Span("Selected paper", style={"fontSize": "11px", "color": COLORS["text_secondary"]}),
                        ], style={"display": "flex", "alignItems": "center", "marginRight": "16px"}),
                        html.Div([
                            html.Span(style={"width": "10px", "height": "10px", "borderRadius": "50%", "background": COLORS["accent_cyan"], "display": "inline-block", "marginRight": "6px"}),
                            html.Span("Paper", style={"fontSize": "11px", "color": COLORS["text_secondary"]}),
                        ], style={"display": "flex", "alignItems": "center", "marginRight": "16px"}),
                        html.Div([
                            html.Span(style={"width": "10px", "height": "10px", "transform": "rotate(45deg)", "background": COLORS["accent_teal"], "display": "inline-block", "marginRight": "6px"}),
                            html.Span("Reference", style={"fontSize": "11px", "color": COLORS["text_secondary"]}),
                        ], style={"display": "flex", "alignItems": "center"}),
                    ], style={"display": "flex", "padding": "10px 16px", "borderTop": f"1px solid {COLORS['border']}"}),
                ], style={**GLASS_CARD, "padding": "0", "overflow": "hidden"}),
            ], span=8),

            dmc.GridCol([
                html.Div(id="citation-info-panel", children=[
                    no_data("Select a paper to explore its citation neighbourhood."),
                ], style={**GLASS_CARD, "minHeight": "560px"}),
            ], span=4),
        ], gutter="lg"),

        # Hidden store for search results
        dcc.Store(id="citation-search-results", data=[]),
    ], style=PAGE_WRAPPER)
