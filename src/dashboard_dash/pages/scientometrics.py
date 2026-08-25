"""
scientometrics.py  —  Citation analysis and author metrics page
"""
from dash import html, dcc, dash_table
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header


def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Scientometrics", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P("Citation impact analysis · author productivity · publication heatmap", style={
                "color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0",
            }),
        ], style={"marginBottom": "28px"}),

        # ── Row 1: Top cited papers + citation distribution ────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Top Cited Papers"),
                html.Div(id="table-top-cited", style={
                    **GLASS_CARD,
                    "padding": "0",
                    "overflowX": "auto",
                    "minHeight": "280px",
                }),
            ], span=7),
            dmc.GridCol([
                section_header("Citation Distribution"),
                html.Div(dcc.Graph(id="chart-citation-dist", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=5),
        ], gutter="lg", style={"marginBottom": "20px"}),

        # ── Row 2: Top authors table + publication heatmap ────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Most Prolific Authors", "by papers in corpus"),
                html.Div(id="table-top-authors", style={
                    **GLASS_CARD, "padding": "0",
                    "overflowX": "auto", "minHeight": "280px",
                }),
            ], span=5),
            dmc.GridCol([
                section_header("Publication Heatmap", "papers by year × study type"),
                html.Div(dcc.Graph(id="chart-pub-heatmap", config={"displayModeBar": False}),
                         style=CHART_BOX),
            ], span=7),
        ], gutter="lg"),
    ], style=PAGE_WRAPPER)


def _paper_table(df) -> html.Table:
    """Render top-cited papers as a styled HTML table."""
    from src.dashboard_dash.styles import TABLE_STYLE, TABLE_HEADER, TABLE_ROW
    from src.dashboard_dash.theme import COLORS

    if df is None or df.empty:
        from src.dashboard_dash.components.empty_states import no_data
        return no_data("No papers match the current filters.")

    rows = []
    for _, r in df.iterrows():
        title = str(r.get("title", ""))[:80] + ("…" if len(str(r.get("title", ""))) > 80 else "")
        cited = f"{int(r['cited_by_count']):,}" if r.get("cited_by_count") is not None else "—"
        rows.append(html.Tr([
            html.Td(str(r.get("year", "—")), style={**TABLE_ROW, "padding": "8px 12px", "color": COLORS["text_secondary"], "whiteSpace": "nowrap"}),
            html.Td(title, style={**TABLE_ROW, "padding": "8px 12px", "maxWidth": "320px"}),
            html.Td(str(r.get("journal", "—"))[:30], style={**TABLE_ROW, "padding": "8px 12px", "color": COLORS["text_secondary"]}),
            html.Td(cited, style={**TABLE_ROW, "padding": "8px 12px", "color": COLORS["accent_cyan"], "fontWeight": 700, "textAlign": "right"}),
        ]))

    return html.Table([
        html.Thead(html.Tr([
            html.Th("Year",     style={**TABLE_HEADER}),
            html.Th("Title",    style={**TABLE_HEADER}),
            html.Th("Journal",  style={**TABLE_HEADER}),
            html.Th("Citations",style={**TABLE_HEADER, "textAlign": "right"}),
        ])),
        html.Tbody(rows),
    ], style=TABLE_STYLE)


def _author_table(df) -> html.Table:
    from src.dashboard_dash.styles import TABLE_STYLE, TABLE_HEADER, TABLE_ROW
    from src.dashboard_dash.theme import COLORS

    if df is None or df.empty:
        from src.dashboard_dash.components.empty_states import no_data
        return no_data("No author data available.")

    rows = []
    for i, r in df.iterrows():
        name = str(r.get("display_name", ""))
        orcid = "✓" if r.get("orcid") else "—"
        papers = f"{int(r['paper_count']):,}" if r.get("paper_count") is not None else "—"
        rows.append(html.Tr([
            html.Td(name,   style={**TABLE_ROW, "padding": "8px 12px"}),
            html.Td(papers, style={**TABLE_ROW, "padding": "8px 12px", "color": COLORS["accent_cyan"], "fontWeight": 700, "textAlign": "center"}),
            html.Td(orcid,  style={**TABLE_ROW, "padding": "8px 12px", "color": COLORS["accent_green"] if orcid == "✓" else COLORS["text_dim"], "textAlign": "center"}),
        ]))

    return html.Table([
        html.Thead(html.Tr([
            html.Th("Author",    style={**TABLE_HEADER}),
            html.Th("Papers",   style={**TABLE_HEADER, "textAlign": "center"}),
            html.Th("ORCID",    style={**TABLE_HEADER, "textAlign": "center"}),
        ])),
        html.Tbody(rows),
    ], style=TABLE_STYLE)
