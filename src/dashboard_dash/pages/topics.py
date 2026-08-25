"""
topics.py  —  Topics & Trends page
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header


def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Topics & Trends", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P("OpenAlex topic classification · topic evolution · research trend detection", style={
                "color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0",
            }),
        ], style={"marginBottom": "28px"}),

        # ── Row 1: Top topics bar + topic donut ────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Top Research Topics", "by paper count"),
                html.Div(dcc.Graph(id="chart-top-topics", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=7),
            dmc.GridCol([
                section_header("Topic Score Distribution", "avg relevance score"),
                html.Div(dcc.Graph(id="chart-topic-scores", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=5),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 2: Topic evolution ─────────────────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Topic Evolution by Year", "papers per year per top topic"),
                html.Div(dcc.Graph(id="chart-topic-evolution", config={"displayModeBar": False}),
                         style={**CHART_BOX, "minHeight": "340px"}),
            ], span=12),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 3: Hydrology vs Remote Sensing split ───────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Domain Split", "hydrology vs remote sensing topics"),
                html.Div(id="chart-domain-split", style={**CHART_BOX, "minHeight": "220px"}),
            ], span=5),
            dmc.GridCol([
                section_header("Emerging Topics", "fastest growing in last 3 years"),
                html.Div(id="chart-emerging-topics", style={**CHART_BOX, "minHeight": "220px"}),
            ], span=7),
        ], gutter="lg"),
    ], style=PAGE_WRAPPER)
