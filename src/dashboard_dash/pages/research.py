"""
research.py  —  Research Intelligence page for GeoHydroAI dashboard.

Unified filter panel + 7 evidence sections + AI synthesis (Gemini).
All data is retrieved via research_query_service.py before Gemini is invoked.
"""
from __future__ import annotations

from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD

# ── Colour aliases ────────────────────────────────────────────────────────────
C = COLORS


def _section_header(title: str, subtitle: str = "") -> html.Div:
    return html.Div([
        html.Span(title, style={
            "color": C["text_primary"], "fontWeight": 700,
            "fontSize": "13px", "letterSpacing": "0.02em",
        }),
        html.Span(f"  {subtitle}", style={
            "color": C["text_dim"], "fontSize": "11px",
        }) if subtitle else None,
    ], style={"marginBottom": "10px"})


def _filter_label(text: str) -> html.Div:
    return html.Div(text, style={
        "color": C["text_secondary"], "fontSize": "11px",
        "marginBottom": "4px", "letterSpacing": "0.04em",
    })


def _btn(label: str, btn_id: str, color: str = C["accent_cyan"]) -> html.Button:
    return html.Button(label, id=btn_id, n_clicks=0, style={
        "background": color,
        "color":      "#000" if color == C["accent_cyan"] else C["text_primary"],
        "border":     "none",
        "borderRadius": "8px",
        "padding":    "9px 22px",
        "fontWeight": 700,
        "fontSize":   "13px",
        "cursor":     "pointer",
        "letterSpacing": "0.03em",
    })


def _table_header_row(cols: list[str]) -> html.Tr:
    return html.Tr([
        html.Th(c, style={
            "padding": "8px 10px", "color": C["text_secondary"],
            "fontSize": "11px", "fontWeight": 600,
            "borderBottom": f"1px solid {C['border']}",
            "textAlign": "left", "whiteSpace": "nowrap",
        }) for c in cols
    ])


def _empty_placeholder(msg: str = "Run a search to see results") -> html.Div:
    return html.Div(msg, style={
        "color": C["text_dim"], "fontSize": "12px",
        "padding": "24px", "textAlign": "center",
    })


# ── Metric / canonical_id labels ──────────────────────────────────────────────
_METRIC_OPTIONS = [
    {"label": "NSE",      "value": "metric.nse"},
    {"label": "RMSE",     "value": "metric.rmse"},
    {"label": "KGE",      "value": "metric.kge"},
    {"label": "MAE",      "value": "metric.mae"},
    {"label": "MAPE",     "value": "metric.mape"},
    {"label": "PBIAS",    "value": "metric.pbias"},
    {"label": "MSE",      "value": "metric.mse"},
    {"label": "RSR",      "value": "metric.rsr"},
    {"label": "SCS-CN",   "value": "method.scs_cn"},
]

_DOMAIN_OPTIONS = [
    {"label": "AI / ML Forecasting",  "value": "AI / ML Forecasting"},
    {"label": "Hydraulic Modeling",   "value": "Hydraulic Modeling"},
    {"label": "Hydrological Modeling","value": "Hydrological Modeling"},
    {"label": "Remote Sensing",       "value": "Remote Sensing"},
]


# ─────────────────────────────────────────────────────────────────────────────
# Page layout function
# ─────────────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    return html.Div([
        # ── Stores ────────────────────────────────────────────────────────────
        dcc.Store(id="research-filter-store", data={
            "year_min": 1995, "year_max": 2025,
            "countries": [], "topics": [], "domains": [],
            "metrics": [], "metric_min": None, "metric_max": None,
            "institutions": [], "authors": [], "min_citations": 0,
            "semantic_query": "",
        }),
        dcc.Store(id="research-evidence-store", data={}),
        dcc.Store(id="research-selected-paper-id", data=None),

        # ── Header ────────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Div("Research Intelligence", style={
                    "fontSize": "22px", "fontWeight": 800,
                    "color": C["text_primary"], "letterSpacing": "-0.01em",
                }),
                html.Div(
                    "Multi-backend query layer · DuckDB · Neo4j · ChromaDB · Gemini synthesis",
                    style={"color": C["text_dim"], "fontSize": "12px", "marginTop": "4px"},
                ),
            ]),
        ], style={"padding": "24px 28px 0"}),

        # ── Filter panel ──────────────────────────────────────────────────────
        html.Div([
            html.Div([
                # Semantic query
                html.Div([
                    _filter_label("SEMANTIC QUERY"),
                    dcc.Input(
                        id="research-semantic-input",
                        type="text",
                        placeholder="e.g. SWAT model calibration NSE performance",
                        debounce=False,
                        style={
                            "width": "100%",
                            "background": C["bg_card"],
                            "color": C["text_primary"],
                            "border": f"1px solid {C['border']}",
                            "borderRadius": "8px",
                            "padding": "9px 12px",
                            "fontSize": "13px",
                            "boxSizing": "border-box",
                        },
                    ),
                ], style={"marginBottom": "14px"}),

                # Row 1: Year + Countries + Topics + Domains
                html.Div([
                    # Year range
                    html.Div([
                        _filter_label("YEAR RANGE"),
                        dcc.RangeSlider(
                            id="research-year-slider",
                            min=1990, max=2025, step=1,
                            value=[1995, 2025],
                            marks={y: {"label": str(y),
                                       "style": {"color": C["text_dim"], "fontSize": "10px"}}
                                   for y in range(1990, 2026, 5)},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ], style={"flex": "2", "minWidth": "200px"}),

                    # Countries
                    html.Div([
                        _filter_label("COUNTRIES"),
                        dcc.Dropdown(
                            id="research-country-dd",
                            options=[], multi=True, placeholder="All",
                            style={"fontSize": "12px"},
                            className="dark-dropdown",
                        ),
                    ], style={"flex": "1", "minWidth": "140px"}),

                    # Topics
                    html.Div([
                        _filter_label("TOPICS"),
                        dcc.Dropdown(
                            id="research-topic-dd",
                            options=[], multi=True, placeholder="All",
                            style={"fontSize": "12px"},
                            className="dark-dropdown",
                        ),
                    ], style={"flex": "1", "minWidth": "140px"}),

                    # Domains
                    html.Div([
                        _filter_label("DOMAINS"),
                        dcc.Dropdown(
                            id="research-domain-dd",
                            options=_DOMAIN_OPTIONS,
                            multi=True, placeholder="All",
                            style={"fontSize": "12px"},
                            className="dark-dropdown",
                        ),
                    ], style={"flex": "1", "minWidth": "140px"}),
                ], style={
                    "display": "flex", "gap": "16px",
                    "flexWrap": "wrap", "marginBottom": "14px",
                }),

                # Row 2: Metrics + Metric range + Min citations + Buttons
                html.Div([
                    # Metrics
                    html.Div([
                        _filter_label("METRICS"),
                        dcc.Dropdown(
                            id="research-metric-dd",
                            options=_METRIC_OPTIONS,
                            multi=True, placeholder="All",
                            style={"fontSize": "12px"},
                            className="dark-dropdown",
                        ),
                    ], style={"flex": "1", "minWidth": "140px"}),

                    # Metric value range
                    html.Div([
                        _filter_label("METRIC VALUE RANGE"),
                        html.Div([
                            dcc.Input(
                                id="research-metric-min",
                                type="number", placeholder="min",
                                style={
                                    "width": "80px",
                                    "background": C["bg_card"],
                                    "color": C["text_primary"],
                                    "border": f"1px solid {C['border']}",
                                    "borderRadius": "6px",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                },
                            ),
                            html.Span("–", style={"color": C["text_dim"], "padding": "0 6px"}),
                            dcc.Input(
                                id="research-metric-max",
                                type="number", placeholder="max",
                                style={
                                    "width": "80px",
                                    "background": C["bg_card"],
                                    "color": C["text_primary"],
                                    "border": f"1px solid {C['border']}",
                                    "borderRadius": "6px",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                },
                            ),
                        ], style={"display": "flex", "alignItems": "center"}),
                    ], style={"flex": "0 0 auto"}),

                    # Min citations
                    html.Div([
                        _filter_label("MIN CITATIONS"),
                        dcc.Input(
                            id="research-min-citations",
                            type="number", placeholder="0", min=0, step=1,
                            value=0,
                            style={
                                "width": "80px",
                                "background": C["bg_card"],
                                "color": C["text_primary"],
                                "border": f"1px solid {C['border']}",
                                "borderRadius": "6px",
                                "padding": "6px 8px",
                                "fontSize": "12px",
                            },
                        ),
                    ], style={"flex": "0 0 auto"}),

                    # Spacer + buttons
                    html.Div(style={"flex": "1"}),
                    html.Div([
                        _btn("Search", "btn-research-search"),
                        _btn("Generate AI Synthesis", "btn-research-ai",
                             color=C["accent_green"]),
                    ], style={"display": "flex", "gap": "10px", "alignItems": "flex-end"}),
                ], style={
                    "display": "flex", "gap": "16px",
                    "flexWrap": "wrap", "alignItems": "flex-end",
                }),

            ], style={**GLASS_CARD, "padding": "20px 22px", "margin": "18px 28px 0"}),
        ]),

        # ── Loading spinner ───────────────────────────────────────────────────
        dcc.Loading(
            id="research-loading",
            type="circle",
            color=C["accent_cyan"],
            children=html.Div(id="research-results-section", style={"display": "none"}, children=[

                # ── KPI row ───────────────────────────────────────────────────
                html.Div(id="research-kpi-row", style={
                    "display": "flex", "gap": "14px",
                    "padding": "18px 28px 0", "flexWrap": "wrap",
                }),

                # ── Row 2: Papers table + Metric distribution ─────────────────
                html.Div([
                    # Papers table (span 7)
                    html.Div([
                        html.Div([
                            _section_header("Top Papers", "by citation count in filter"),
                            html.Div(id="table-research-papers",
                                     children=_empty_placeholder()),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ], style={"flex": "7", "minWidth": "300px"}),

                    # Metric distribution (span 5)
                    html.Div([
                        html.Div([
                            _section_header("Metric Distribution",
                                            "NumericFacts / DuckDB"),
                            html.Div(id="table-research-metrics",
                                     children=_empty_placeholder()),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ], style={"flex": "5", "minWidth": "260px"}),
                ], style={
                    "display": "flex", "gap": "14px",
                    "padding": "14px 28px 0", "flexWrap": "wrap",
                }),

                # ── Row 3: Authors + Domain performance ───────────────────────
                html.Div([
                    # Authors (span 5)
                    html.Div([
                        html.Div([
                            _section_header("Author Influence",
                                            "DuckDB + OpenAlex"),
                            html.Div(id="table-research-authors",
                                     children=_empty_placeholder()),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ], style={"flex": "5", "minWidth": "260px"}),

                    # Domain performance (span 7)
                    html.Div([
                        html.Div([
                            _section_header("Domain Performance",
                                            "NSE distribution by domain · Neo4j"),
                            html.Div(id="chart-research-domains",
                                     children=_empty_placeholder()),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ], style={"flex": "7", "minWidth": "300px"}),
                ], style={
                    "display": "flex", "gap": "14px",
                    "padding": "14px 28px 0", "flexWrap": "wrap",
                }),

                # ── Row 4: Graph evidence + Semantic passages ─────────────────
                html.Div([
                    # Graph evidence (span 6)
                    html.Div([
                        html.Div([
                            _section_header("Graph Evidence",
                                            "Top NSE papers · doi · Neo4j"),
                            html.Div(id="table-research-graph",
                                     children=_empty_placeholder()),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ], style={"flex": "6", "minWidth": "280px"}),

                    # Semantic passages (span 6)
                    html.Div([
                        html.Div([
                            _section_header("Semantic Passages",
                                            "doi · title · ChromaDB / SPECTER2"),
                            html.Div(id="table-research-semantic",
                                     children=_empty_placeholder()),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ], style={"flex": "6", "minWidth": "280px"}),
                ], style={
                    "display": "flex", "gap": "14px",
                    "padding": "14px 28px 0", "flexWrap": "wrap",
                }),

                # ── Row 4b: Numeric Fact Evidence (individual measurements) ────
                html.Div([
                    html.Div([
                        _section_header(
                            "Numeric Fact Evidence",
                            "individual measurements · doi · confidence · DuckDB / Parquet",
                        ),
                        html.Div(id="research-numeric-facts",
                                 children=_empty_placeholder()),
                    ], style={**GLASS_CARD, "padding": "16px 18px"}),
                ], style={"padding": "14px 28px 0"}),

                # ── Row 5: Paper Detail Panel ─────────────────────────────────
                html.Div(
                    id="research-paper-detail-panel",
                    style={"display": "none"},
                    children=[
                        html.Div([
                            _section_header(
                                "Paper Detail",
                                "title · doi · abstract · authors · metrics · semantic passages",
                            ),
                            html.Div(id="research-paper-detail-content"),
                        ], style={**GLASS_CARD, "padding": "20px 22px"}),
                    ],
                    className="",
                ),

                # ── Row 6: AI Synthesis ───────────────────────────────────────
                html.Div([
                    html.Div([
                        html.Div([
                            _section_header("AI Synthesis",
                                            "Gemini · evidence-grounded · DOI-cited"),
                            html.Div(id="research-ai-meta", style={
                                "color": C["text_dim"], "fontSize": "11px",
                                "marginBottom": "10px",
                            }),
                            dcc.Loading(
                                type="dot", color=C["accent_green"],
                                children=html.Div(id="research-ai-output",
                                                  children=_empty_placeholder(
                                                      "Click 'Generate AI Synthesis' after searching."
                                                  )),
                            ),
                        ], style={**GLASS_CARD, "padding": "16px 18px"}),
                    ]),
                ], style={"padding": "14px 28px 28px"}),

            ]),  # end research-results-section
        ),  # end Loading

    ], style={"minHeight": "100vh", "background": C["bg_base"]})
