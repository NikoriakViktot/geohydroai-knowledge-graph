"""
quality.py  —  Data Quality page: coverage metrics, QA analysis
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header, kpi_card


def _pct_bar(label: str, pct: float, accent: str = COLORS["accent_cyan"]) -> html.Div:
    """Horizontal coverage bar."""
    color = (
        COLORS["accent_green"] if pct >= 80 else
        COLORS["accent_amber"] if pct >= 50 else
        COLORS["accent_red"]
    )
    return html.Div([
        html.Div([
            html.Span(label, style={"color": COLORS["text_secondary"], "fontSize": "12px", "flex": "1"}),
            html.Span(f"{pct:.1f}%", style={"color": color, "fontSize": "13px", "fontWeight": 700}),
        ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "5px"}),
        html.Div([
            html.Div(style={
                "width": f"{min(pct, 100):.1f}%",
                "height": "6px",
                "background": f"linear-gradient(90deg, {color}, {color}88)",
                "borderRadius": "3px",
                "transition": "width 0.4s ease",
            }),
        ], style={
            "background": COLORS["border"],
            "borderRadius": "3px",
            "height": "6px",
            "marginBottom": "12px",
        }),
    ])


def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Data Quality", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P(
                "Coverage analysis · normalization quality · enrichment completeness",
                style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
            ),
        ], style={"marginBottom": "28px"}),

        # ── Coverage KPIs ─────────────────────────────────────────────────────
        html.Div(id="quality-kpi-row", style={"marginBottom": "24px"}),

        # ── Coverage bars ─────────────────────────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Coverage Metrics"),
                html.Div(id="quality-coverage-bars", style={**GLASS_CARD, "minHeight": "280px"}),
            ], span=4),
            dmc.GridCol([
                section_header("Coverage by Year"),
                html.Div(dcc.Graph(id="chart-coverage-year", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=8),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Normalization stats ───────────────────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Method Coverage", "papers with ≥1 normalized method"),
                html.Div(dcc.Graph(id="chart-method-qa", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=4),
            dmc.GridCol([
                section_header("Sensor Coverage", "papers with ≥1 normalized sensor"),
                html.Div(dcc.Graph(id="chart-sensor-qa", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=4),
            dmc.GridCol([
                section_header("Year Anomalies", "records with invalid year field"),
                html.Div(dcc.Graph(id="chart-year-anomalies", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=4),
        ], gutter="lg"),
    ], style=PAGE_WRAPPER)
