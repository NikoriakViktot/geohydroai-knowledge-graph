"""
empty_states.py  —  Elegant empty/error state components
"""
from dash import html
from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD


def no_data(message: str = "No data matches the current filters.") -> html.Div:
    return html.Div([
        html.Div([
            html.Div("⬡", style={
                "fontSize": "36px", "color": COLORS["text_dim"],
                "marginBottom": "12px", "opacity": "0.6",
            }),
            html.P(message, style={
                "color": COLORS["text_secondary"], "fontSize": "14px",
                "margin": "0", "textAlign": "center", "maxWidth": "280px",
            }),
        ], style={
            **GLASS_CARD,
            "display": "flex", "flexDirection": "column",
            "alignItems": "center", "justifyContent": "center",
            "padding": "40px 24px", "textAlign": "center",
            "border": f"1px dashed {COLORS['border_light']}",
        }),
    ])


def error_state(message: str = "An error occurred while loading data.") -> html.Div:
    return html.Div([
        html.Div([
            html.Div("!", style={
                "fontSize": "28px", "color": COLORS["accent_red"],
                "fontWeight": "800", "marginBottom": "10px",
                "width": "48px", "height": "48px",
                "borderRadius": "50%",
                "border": f"2px solid {COLORS['accent_red']}",
                "display": "flex", "alignItems": "center", "justifyContent": "center",
            }),
            html.P(message, style={
                "color": COLORS["accent_red"], "fontSize": "13px",
                "margin": "0", "textAlign": "center",
                "opacity": "0.85",
            }),
        ], style={
            **GLASS_CARD,
            "display": "flex", "flexDirection": "column",
            "alignItems": "center", "justifyContent": "center",
            "padding": "32px 24px",
            "border": f"1px solid rgba(239,68,68,0.25)",
        }),
    ])


def coming_soon(label: str = "Coming Soon") -> html.Div:
    return html.Div([
        html.Div([
            html.P(label, style={
                "color": COLORS["text_dim"], "fontSize": "13px",
                "fontWeight": 600, "margin": "0", "letterSpacing": "0.08em",
                "textTransform": "uppercase",
            }),
        ], style={
            **GLASS_CARD,
            "display": "flex", "alignItems": "center", "justifyContent": "center",
            "padding": "32px",
            "border": f"1px dashed {COLORS['text_dim']}",
        }),
    ])
