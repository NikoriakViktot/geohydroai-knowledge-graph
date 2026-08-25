"""
methods.py  —  Methods & Sensors page
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header


def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Methods & Sensors", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P(
                "Ontology-normalized method and sensor entity analytics · "
                "sourced exclusively from normalized_entities (canonical IDs only)",
                style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
            ),
        ], style={"marginBottom": "28px"}),

        # ── Coverage badges ────────────────────────────────────────────────────
        html.Div(id="methods-coverage-badges", style={"marginBottom": "20px"}),

        # ── Row 1: Top Methods + Top Sensors ──────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Top Methods", "by paper count"),
                html.Div(dcc.Graph(id="chart-top-methods", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=6),
            dmc.GridCol([
                section_header("Top Sensors / Satellites", "by paper count"),
                html.Div(dcc.Graph(id="chart-top-sensors", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=6),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 2: Metrics + Method type groups ───────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Performance Metrics", "canonical metric entities"),
                html.Div(dcc.Graph(id="chart-metrics", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=4),
            dmc.GridCol([
                section_header("Method Coverage Breakdown", "papers with vs without methods"),
                html.Div(dcc.Graph(id="chart-method-coverage", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=8),
        ], gutter="lg"),
    ], style=PAGE_WRAPPER)
