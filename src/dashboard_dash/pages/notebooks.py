"""notebooks.py — Scientific notebook gallery and viewer."""
from dash import html, dcc, callback, Output, Input
from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD

_NOTEBOOKS = [
    {
        "id":    "01",
        "slug":  "01_corpus_overview",
        "title": "01 — Corpus Overview",
        "desc":  "3,065 papers · publication timeline · country choropleth · topic & sensor distribution",
        "icon":  "◉",
    },
    {
        "id":    "02",
        "slug":  "02_metric_analysis",
        "title": "02 — Metric Analysis",
        "desc":  "713 numeric facts · NSE/KGE/RMSE violin plots · calibration vs validation scatter",
        "icon":  "◈",
    },
    {
        "id":    "03",
        "slug":  "03_temporal_trends",
        "title": "03 — Temporal Trends",
        "desc":  "AI/ML emergence · SAR adoption post-2014 · Mann-Kendall NSE trend test",
        "icon":  "◇",
    },
    {
        "id":    "04",
        "slug":  "04_geographic_patterns",
        "title": "04 — Geographic Patterns",
        "desc":  "World choropleth · top institutions · regional NSE performance · Eastern Europe",
        "icon":  "◎",
    },
    {
        "id":    "05",
        "slug":  "05_method_dominance",
        "title": "05 — Method Dominance",
        "desc":  "175 methods · SWAT vs Random Forest · co-occurrence structure · sensor families",
        "icon":  "◆",
    },
    {
        "id":    "06",
        "slug":  "06_scientometric_analysis",
        "title": "06 — Scientometrics",
        "desc":  "9,466 authors · h-index · Lorenz curve (Gini) · reference age distribution",
        "icon":  "◈",
    },
    {
        "id":    "07",
        "slug":  "07_graph_analysis",
        "title": "07 — Graph Analysis",
        "desc":  "155K citation edges · PageRank · Louvain communities · power-law degree distribution",
        "icon":  "⬡",
    },
    {
        "id":    "08",
        "slug":  "08_semantic_retrieval_validation",
        "title": "08 — Semantic Retrieval",
        "desc":  "986K ChromaDB chunks · retrieval score distributions · DOI provenance coverage",
        "icon":  "◑",
    },
    {
        "id":    "09",
        "slug":  "09_information_loss_analysis",
        "title": "09 — Information Loss ⭐",
        "desc":  "Pipeline Sankey · 77% info loss · retention curve · formula/table extraction quality",
        "icon":  "◬",
    },
    {
        "id":    "10",
        "slug":  "10_case_studies_ukraine",
        "title": "10 — Ukraine Case Studies",
        "desc":  "Eastern Europe focus · Kakhovka dam · Prut/Dnieper · SAR adoption timeline",
        "icon":  "◭",
    },
    {
        "id":    "11",
        "slug":  "11_pipeline_observability",
        "title": "11 — Pipeline Observability ⭐",
        "desc":  "Stale analytics vs extraction failure · 22× coverage gain · freshness heatmap · sync consistency · Sankey flow",
        "icon":  "⬡",
    },
    {
        "id":    "12",
        "slug":  "12_flood_knowledge_graph_analysis",
        "title": "12 — Knowledge Graph Analysis ⭐",
        "desc":  "PPMI/Jaccard/Lift normalisation · 6 paradigm communities · method–sensor bipartite · 10 publication figures · scientific ecosystem map",
        "icon":  "⬢",
    },
]


def _card(nb: dict, active: bool) -> html.Div:
    border = f"1px solid {COLORS['accent_cyan']}" if active else f"1px solid {COLORS['border']}"
    bg     = "rgba(0,212,255,0.06)" if active else COLORS["bg_card"]
    return html.Div(
        id={"type": "nb-card", "index": nb["slug"]},
        children=[
            html.Div([
                html.Span(nb["icon"], style={"fontSize": "18px", "marginRight": "8px",
                                             "color": COLORS["accent_cyan"]}),
                html.Span(nb["title"], style={"fontWeight": 700, "fontSize": "13px",
                                              "color": COLORS["text_primary"]}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "6px"}),
            html.Div(nb["desc"], style={
                "fontSize": "11px", "color": COLORS["text_secondary"],
                "lineHeight": "1.5",
            }),
        ],
        style={
            **GLASS_CARD,
            "border":        border,
            "background":    bg,
            "padding":       "12px 14px",
            "cursor":        "pointer",
            "borderRadius":  "10px",
            "transition":    "border 0.15s, background 0.15s",
            "marginBottom":  "6px",
        },
        n_clicks=0,
    )


def layout() -> html.Div:
    return html.Div([
        # Header
        html.Div([
            html.H2("Scientific Notebooks", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "22px", "margin": "0 0 4px 0",
            }),
            html.Div("Publication-quality analysis · 44 figures · real data only", style={
                "color": COLORS["text_secondary"], "fontSize": "13px",
            }),
        ], style={"padding": "20px 24px 16px", "borderBottom": f"1px solid {COLORS['border']}"}),

        # Body: sidebar cards + iframe viewer
        html.Div([
            # Left: notebook list
            html.Div([
                dcc.Store(id="active-nb-slug", data="01_corpus_overview"),
                html.Div(id="nb-card-list", children=[
                    _card(nb, i == 0) for i, nb in enumerate(_NOTEBOOKS)
                ], style={"padding": "10px 8px"}),
            ], style={
                "width":     "270px",
                "minWidth":  "270px",
                "borderRight": f"1px solid {COLORS['border']}",
                "overflowY": "auto",
                "height":    "calc(100vh - 90px)",
            }),

            # Right: iframe viewer
            html.Div([
                html.Iframe(
                    id="nb-iframe",
                    src="/assets/notebooks/01_corpus_overview.html",
                    style={
                        "width":   "100%",
                        "height":  "calc(100vh - 90px)",
                        "border":  "none",
                        "background": "#fff",
                    },
                ),
            ], style={"flex": "1", "overflow": "hidden"}),

        ], style={"display": "flex", "height": "calc(100vh - 90px)"}),
    ], style={"height": "100vh", "overflow": "hidden"})
