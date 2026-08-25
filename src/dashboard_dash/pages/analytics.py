"""
analytics.py  —  GeoHydroAI Benchmark Analytics page

5 panels:
  1. Regional dominance — dominant modeling approach by country
  2. Domain vs NSE      — hydraulic / AI / hydro / RS performance comparison
  3. Institutions       — top performers by avg NSE
  4. AI trend           — forecasting quality evolution 2015–2024
  5. Metric dominance   — which metrics dominate each domain
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header, kpi_card


def layout() -> html.Div:
    return html.Div([

        # ── Header ────────────────────────────────────────────────────────────
        html.Div([
            html.H1("Benchmark Analytics", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P(
                "Model performance · regional dominance · AI evolution · "
                "institution ranking · metric landscape",
                style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
            ),
        ], style={"marginBottom": "28px"}),

        # ── KPI row ───────────────────────────────────────────────────────────
        html.Div(id="analytics-kpi-row", style={"marginBottom": "24px"}),

        # ── Row 1: Domain NSE comparison + AI trend ───────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("NSE by Modeling Domain",
                               "avg Nash-Sutcliffe across hydraulic / AI / RS / hydro"),
                html.Div(dcc.Graph(id="chart-domain-nse",
                                   config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=5),
            dmc.GridCol([
                section_header("AI Forecasting Quality Trend",
                               "avg NSE + % excellent by publication year"),
                html.Div(dcc.Graph(id="chart-ai-trend",
                                   config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=7),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 2: Institution ranking (full width) ───────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Top Institutions by Model Performance",
                               "avg NSE · min 3 papers · colour = % excellent"),
                html.Div(dcc.Graph(id="chart-institutions-nse",
                                   config={"displayModeBar": False}),
                         style={**CHART_BOX, "minHeight": "380px"}),
            ], span=12),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 3: Regional + metric dominance ───────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Regional Analysis",
                               "dominant topic proxy per country · papers ≥ 5"),
                html.Div(dcc.Graph(id="chart-regional",
                                   config={"displayModeBar": False}),
                         style={**CHART_BOX, "minHeight": "420px"}),
            ], span=7),
            dmc.GridCol([
                section_header("Metric Dominance by Domain",
                               "top metrics used per modeling paradigm"),
                html.Div(dcc.Graph(id="chart-metric-domain",
                                   config={"displayModeBar": False}),
                         style={**CHART_BOX, "minHeight": "420px"}),
            ], span=5),
        ], gutter="lg"),

    ], style=PAGE_WRAPPER)
