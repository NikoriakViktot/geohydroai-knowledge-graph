"""
overview.py  —  Overview page: KPI cards + publication distributions
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import kpi_card, section_header


def layout() -> html.Div:
    return html.Div([
        # ── Page header ───────────────────────────────────────────────────────
        html.Div([
            html.H1("Research Overview", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P("GeoHydroAI corpus summary · all figures respond to sidebar filters", style={
                "color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0",
            }),
        ], style={"marginBottom": "28px"}),

        # ── KPI row ────────────────────────────────────────────────────────────
        html.Div(id="kpi-cards-row", style={"marginBottom": "28px"}),

        # ── Row 1: Papers by Year + Top Countries ──────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Publications by Year"),
                html.Div(dcc.Graph(id="chart-papers-year", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=7),
            dmc.GridCol([
                section_header("Top Countries"),
                html.Div(dcc.Graph(id="chart-top-countries", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=5),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 2: Journal Treemap + Study Type Donut ──────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Top Journals", "by paper count"),
                html.Div(dcc.Graph(id="chart-journals-treemap", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=7),
            dmc.GridCol([
                section_header("Study Type Distribution"),
                html.Div(dcc.Graph(id="chart-study-type", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=5),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 3: Top Institutions ────────────────────────────────────────────
        html.Div([
            section_header("Top Institutions", "by paper count across corpus"),
            html.Div(dcc.Graph(id="chart-institutions", config={"displayModeBar": False}),
                     style=CHART_BOX),
        ]),
    ], style=PAGE_WRAPPER)
