"""
metric_cards.py  —  KPI and metric card components
"""
from dash import html
from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD


def kpi_card(
    value: str,
    label: str,
    accent: str = COLORS["accent_cyan"],
    sub: str = "",
    icon: str = "",
) -> html.Div:
    return html.Div([
        html.Div([
            html.Div(icon, style={"fontSize": "22px", "marginBottom": "6px", "lineHeight": "1"})
            if icon else None,
            html.Div(value, style={
                "fontSize": "28px", "fontWeight": 800, "color": accent,
                "letterSpacing": "-0.02em", "lineHeight": "1.1",
            }),
            html.Div(label, style={
                "fontSize": "11px", "fontWeight": 600, "color": COLORS["text_secondary"],
                "textTransform": "uppercase", "letterSpacing": "0.06em",
                "marginTop": "4px",
            }),
            html.Div(sub, style={
                "fontSize": "11px", "color": COLORS["text_dim"], "marginTop": "2px",
            }) if sub else None,
        ], style={
            "textAlign": "center",
            "display":   "flex",
            "flexDirection": "column",
            "alignItems": "center",
        }),
    ], style={
        **GLASS_CARD,
        "padding":     "20px 12px",
        "minHeight":   "115px",
        "display":     "flex",
        "alignItems":  "center",
        "justifyContent": "center",
        "border":      f"1px solid {accent}22",
        "transition":  "border-color 0.2s, box-shadow 0.2s",
        "boxShadow":   f"0 0 24px {accent}0a",
    })


def stat_row(label: str, value: str, accent: str = COLORS["accent_cyan"]) -> html.Div:
    return html.Div([
        html.Span(label, style={
            "color": COLORS["text_secondary"], "fontSize": "13px", "flex": "1",
        }),
        html.Span(value, style={
            "color": accent, "fontSize": "14px", "fontWeight": 700,
        }),
    ], style={
        "display":     "flex",
        "justifyContent": "space-between",
        "alignItems":  "center",
        "padding":     "8px 0",
        "borderBottom": f"1px solid {COLORS['border']}",
    })


def section_header(title: str, subtitle: str = "") -> html.Div:
    return html.Div([
        html.H3(title, style={
            "color":       COLORS["text_primary"],
            "fontWeight":  700,
            "fontSize":    "15px",
            "letterSpacing": "0.03em",
            "textTransform": "uppercase",
            "margin":      "0 0 4px 0",
        }),
        html.P(subtitle, style={
            "color":  COLORS["text_secondary"],
            "fontSize": "12px",
            "margin": "0",
        }) if subtitle else None,
    ], style={"marginBottom": "16px"})
