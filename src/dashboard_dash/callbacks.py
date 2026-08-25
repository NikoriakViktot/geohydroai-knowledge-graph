"""
callbacks.py  —  All Dash callbacks: routing, filters, and per-page charts

ALL component library imports are at module level — Dash 4.x prohibits
importing dash_cytoscape, dash_deck, dash_mantine_components, etc. inside
callback functions (ImportedInsideCallbackError).
"""
from __future__ import annotations

import pandas as pd

import dash
from dash import Input, Output, State, html, dcc, no_update, ctx, ALL
from dash.exceptions import PreventUpdate
import dash_mantine_components as dmc
import plotly.graph_objects as go

# ── Shared app instance (must be first) ───────────────────────────────────────
from src.dashboard_dash._app_instance import app as _app
callback = _app.callback  # bind all @callback to the single shared Dash instance

# ── Internal imports (all at module level) ────────────────────────────────────
from src.dashboard_dash import data_loader as dl
from src.dashboard_dash.theme import COLORS, CHART_LAYOUT
from src.dashboard_dash.styles import GLASS_CARD, TABLE_STYLE, TABLE_HEADER, TABLE_ROW, badge_style
from src.dashboard_dash.components import charts, empty_states
from src.dashboard_dash.components.metric_cards import kpi_card
from src.dashboard_dash.components.deck_layers import (
    country_scatter_data, country_hex_data, institution_scatter_data,
)
from src.dashboard_dash.duckdb_manager import query as _dbq
from src.dashboard_dash.layout import sidebar

# ── Page modules (all at module level) ────────────────────────────────────────
from src.dashboard_dash.pages import (
    overview, scientometrics, methods, topics,
    citations, geospatial, quality, explorer, analytics, notebooks as _notebooks_page,
)
from src.dashboard_dash import data_neo4j as dn4j
from src.dashboard_dash.pages import research as _research_page
from src.dashboard_dash import research_query_service as rqs
from src.dashboard_dash import ai_gateway
from src.config.settings import GEMINI_API_KEY
from src.dashboard_dash.pages.scientometrics import _paper_table, _author_table
from src.dashboard_dash.pages.quality import _pct_bar
from src.dashboard_dash.pages.explorer import _info_row, paper_detail_panel, search_results_table
from src.dashboard_dash.pages.geospatial import build_deck_map

# ── Flood page ─────────────────────────────────────────────────────────────────
import dash_ag_grid as dag
from src.dashboard_dash.pages import flood as _flood_page
from src.dashboard_dash import data_flood as dfl
from src.dashboard_dash.pages.flood import (
    ACCURACY_TIMELINESS, FLOOD_CASE_STUDIES,
    FLOOD_GRID_COLS, _CAT_COLORS, build_case_study_map,
)


# ─────────────────────────────────────────────────────────────────────────────
# URL routing
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("page-content",      "children"),
    Output("sidebar-container", "children"),
    Input("url", "pathname"),
    State("filter-store", "data"),
)
def route_page(pathname: str, fs: dict):
    _pages = {
        "/":               overview.layout,
        "/scientometrics": scientometrics.layout,
        "/methods":        methods.layout,
        "/topics":         topics.layout,
        "/citations":      citations.layout,
        "/geospatial":     geospatial.layout,
        "/quality":        quality.layout,
        "/explorer":       explorer.layout,
        "/flood":          _flood_page.layout,
        "/analytics":      analytics.layout,
        "/research":       _research_page.layout,
        "/notebooks":      _notebooks_page.layout,
    }
    page_fn = _pages.get(pathname or "/", overview.layout)
    return page_fn(), sidebar(pathname or "/")


# ─────────────────────────────────────────────────────────────────────────────
# Filter options bootstrap
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("filter-options-store", "data"),
    Input("url", "pathname"),
    prevent_initial_call=False,
)
def load_filter_options(_pathname):
    try:
        return dl.get_filter_options()
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Filter store update
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("filter-store",        "data"),
    Input("filter-apply",         "n_clicks"),
    Input("filter-reset",         "n_clicks"),
    State("filter-year",          "value"),
    State("filter-country",       "value"),
    State("filter-study-type",    "value"),
    State("filter-min-citations", "value"),
    State("filter-has-doi",       "checked"),
    State("filter-has-openalex",  "checked"),
    prevent_initial_call=True,
)
def update_filter_store(apply_clicks, reset_clicks, year, countries, study_types,
                        min_cit, has_doi, has_oa):
    if ctx.triggered_id == "filter-reset":
        return {
            "year_min": 1995, "year_max": 2025,
            "countries": [], "study_types": [],
            "min_citations": 0, "has_doi": False, "has_openalex": False,
        }
    return {
        "year_min":     int((year or [1995, 2025])[0]),
        "year_max":     int((year or [1995, 2025])[1]),
        "countries":    countries or [],
        "study_types":  study_types or [],
        "min_citations": int(min_cit or 0),
        "has_doi":      bool(has_doi),
        "has_openalex": bool(has_oa),
    }


@callback(
    Output("filter-year-display", "children"),
    Input("filter-year", "value"),
)
def update_year_display(value):
    if not value:
        return "1995 – 2025"
    return f"{value[0]} – {value[1]}"


# ─────────────────────────────────────────────────────────────────────────────
# Overview page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("kpi-cards-row",        "children"),
    Output("chart-papers-year",    "figure"),
    Output("chart-top-countries",  "figure"),
    Output("chart-journals-treemap","figure"),
    Output("chart-study-type",     "figure"),
    Output("chart-institutions",   "figure"),
    Input("filter-store", "data"),
)
def update_overview(fs: dict):
    fs = fs or {}

    try:
        kpis = dl.get_kpi_counts(fs)
    except Exception:
        kpis = {}

    accent_seq = [
        COLORS["accent_cyan"], COLORS["accent_teal"], COLORS["accent_blue"],
        COLORS["accent_green"], COLORS["accent_amber"], "#8b5cf6",
        COLORS["accent_teal"], COLORS["accent_cyan"],
    ]
    kpi_defs = [
        (f"{kpis.get('papers', 0):,}",      "Total Papers",      "○", 0),
        (f"{kpis.get('authors', 0):,}",     "Authors",           "◈", 1),
        (f"{kpis.get('references', 0):,}",  "References",        "⟳", 2),
        (f"{kpis.get('methods', 0):,}",     "Methods",           "◆", 3),
        (f"{kpis.get('sensors', 0):,}",     "Sensors",           "◉", 4),
        (f"{kpis.get('topics', 0):,}",      "Topics",            "◎", 5),
        (f"{kpis.get('doi_pct', 0):.1f}%",  "DOI Coverage",      "✓", 6),
        (f"{kpis.get('oa_pct', 0):.1f}%",   "OpenAlex Coverage", "⬡", 7),
    ]
    kpi_row = dmc.SimpleGrid(
        cols={"base": 2, "sm": 4, "lg": 8},
        children=[kpi_card(v, l, accent=accent_seq[i], icon=ic) for v, l, ic, i in kpi_defs],
        style={"marginBottom": "4px"},
    )

    try:
        fig_year = charts.area_chart(dl.get_papers_by_year(fs), "year", "papers", "Publications per Year")
    except Exception:
        fig_year = charts.empty_fig("Query failed")

    try:
        fig_ctry = charts.bar_h(dl.get_top_countries(fs, 15), "papers", "country", "Top Countries", 15)
    except Exception:
        fig_ctry = charts.empty_fig("Query failed")

    try:
        fig_jnl = charts.treemap(dl.get_top_journals(fs, 20), "journal", "papers", "Top Journals")
    except Exception:
        fig_jnl = charts.empty_fig("Query failed")

    try:
        df_st = dl.get_study_type_dist(fs)
        if not df_st.empty:
            df_st["study_type"] = df_st["study_type"].str.replace("_", " ").str.title()
        fig_st = charts.donut(df_st, "study_type", "papers", "Study Types")
    except Exception:
        fig_st = charts.empty_fig("Query failed")

    try:
        fig_inst = charts.bar_h(dl.get_top_institutions(fs, 15), "paper_count", "display_name", "Top Institutions", 15)
    except Exception:
        fig_inst = charts.empty_fig("Query failed")

    return kpi_row, fig_year, fig_ctry, fig_jnl, fig_st, fig_inst


# ─────────────────────────────────────────────────────────────────────────────
# Scientometrics page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("table-top-cited",     "children"),
    Output("chart-citation-dist", "figure"),
    Output("table-top-authors",   "children"),
    Output("chart-pub-heatmap",   "figure"),
    Input("filter-store", "data"),
)
def update_scientometrics(fs: dict):
    fs = fs or {}

    try:
        cited_table = _paper_table(dl.get_top_cited_papers(fs, 20))
    except Exception:
        cited_table = empty_states.error_state("Failed to load citation data")

    try:
        df_dist = dl.get_citation_distribution(fs)
        fig_dist = charts.histogram(df_dist["cited_by_count"].dropna(), "Citation Distribution (log scale)", log_x=True)
    except Exception:
        fig_dist = charts.empty_fig("Query failed")

    try:
        auth_table = _author_table(dl.get_top_authors(fs, 20))
    except Exception:
        auth_table = empty_states.error_state("Failed to load author data")

    try:
        df_heat = dl.get_publication_heatmap(fs)
        if not df_heat.empty:
            df_heat["study_type"] = df_heat["study_type"].str.replace("_", " ").str.title()
        fig_heat = charts.heatmap(df_heat, "year", "study_type", "papers", "Publications: Year × Study Type")
    except Exception:
        fig_heat = charts.empty_fig("Query failed")

    return cited_table, fig_dist, auth_table, fig_heat


# ─────────────────────────────────────────────────────────────────────────────
# Methods & Sensors page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("chart-top-methods",       "figure"),
    Output("chart-top-sensors",       "figure"),
    Output("chart-metrics",           "figure"),
    Output("chart-method-coverage",   "figure"),
    Output("methods-coverage-badges", "children"),
    Input("filter-store", "data"),
)
def update_methods(fs: dict):
    fs = fs or {}

    try:
        fig_m = charts.bar_h(dl.get_top_methods(25), "paper_count", "display_name", "Top Methods (by papers)", 20)
    except Exception:
        fig_m = charts.empty_fig("Query failed")

    try:
        fig_s = charts.bar_h(dl.get_top_sensors(20), "paper_count", "display_name", "Top Sensors (by papers)", 15, COLORS["accent_teal"])
    except Exception:
        fig_s = charts.empty_fig("Query failed")

    try:
        fig_me = charts.bar_v(dl.get_top_metrics(), "display_name", "paper_count", "Performance Metrics")
    except Exception:
        fig_me = charts.empty_fig("Query failed")

    try:
        where = dl._paper_where(fs)
        with_m    = int(_dbq(f"SELECT count(*) FROM papers WHERE {where} AND methods_count > 0").iloc[0, 0])
        without_m = int(_dbq(f"SELECT count(*) FROM papers WHERE {where} AND methods_count = 0").iloc[0, 0])
        df_cov = pd.DataFrame({
            "category": ["With methods", "Without methods"],
            "papers":   [with_m, without_m],
        })
        fig_cov = charts.donut(df_cov, "category", "papers", "Method Coverage")
    except Exception:
        fig_cov = charts.empty_fig("Query failed")

    try:
        n_methods = int(_dbq("SELECT count(*) FROM methods").iloc[0, 0])
        n_sensors = int(_dbq("SELECT count(*) FROM sensors").iloc[0, 0])
        n_metrics = int(_dbq("SELECT count(*) FROM metrics").iloc[0, 0])
    except Exception:
        n_methods = n_sensors = n_metrics = 0

    badges = html.Div([
        html.Span(f"⬡ {n_methods} canonical methods", style={**badge_style("cyan"),  "marginRight": "8px"}),
        html.Span(f"◉ {n_sensors} canonical sensors",  style={**badge_style("teal"), "marginRight": "8px"}),
        html.Span(f"◆ {n_metrics} canonical metrics",  style={**badge_style("green")}),
    ])

    return fig_m, fig_s, fig_me, fig_cov, badges


# ─────────────────────────────────────────────────────────────────────────────
# Topics page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("chart-top-topics",      "figure"),
    Output("chart-topic-scores",    "figure"),
    Output("chart-topic-evolution", "figure"),
    Output("chart-domain-split",    "children"),
    Output("chart-emerging-topics", "children"),
    Input("filter-store", "data"),
)
def update_topics(fs: dict):
    fs = fs or {}

    try:
        fig_t = charts.bar_h(dl.get_top_topics(20), "paper_count", "topic_name", "Top Topics", 15)
    except Exception:
        fig_t = charts.empty_fig("Query failed")

    try:
        df_sc = dl.get_topic_score_distribution()
        fig_sc = charts.scatter(df_sc, "avg_score", "paper_count", size="max_score",
                                text="topic_name", title="Score vs Paper Count")
    except Exception:
        fig_sc = charts.empty_fig("Query failed")

    try:
        df_ev = dl.get_topic_year_evolution(fs, 6)
        fig_ev = (
            charts.line_multi(df_ev, "year", "papers", "topic_name", "Topic Evolution by Year")
            if not df_ev.empty
            else charts.empty_fig("No year data available for topics")
        )
    except Exception:
        fig_ev = charts.empty_fig("Query failed")

    try:
        df_all = dl.get_top_topics(50)
        hydro = df_all[df_all["topic_name"].str.contains("Hydro|Flood|Water|Watershed", case=False, na=False)]["paper_count"].sum()
        rs    = df_all[df_all["topic_name"].str.contains("Remote|Sensor|Satellite|Radar", case=False, na=False)]["paper_count"].sum()
        other = max(0, df_all["paper_count"].sum() - hydro - rs)
        df_split = pd.DataFrame({
            "domain": ["Hydrology & Water", "Remote Sensing", "Other"],
            "papers": [int(hydro), int(rs), int(other)],
        })
        fig_split = dcc.Graph(
            figure=charts.donut(df_split, "domain", "papers", "Domain Split"),
            config={"displayModeBar": False},
        )
    except Exception:
        fig_split = empty_states.error_state("Could not compute domain split")

    try:
        df_ev2 = dl.get_topic_year_evolution({"year_min": 2021, "year_max": 2025}, 10)
        if not df_ev2.empty:
            emerging = df_ev2.groupby("topic_name")["papers"].sum().sort_values(ascending=False).head(6)
            df_em = pd.DataFrame({"topic_name": emerging.index, "papers": emerging.values})
            fig_em = dcc.Graph(
                figure=charts.bar_h(df_em, "papers", "topic_name", "Emerging (2021–2025)", 6),
                config={"displayModeBar": False},
            )
        else:
            fig_em = empty_states.no_data("No recent topic data")
    except Exception:
        fig_em = empty_states.error_state("Query failed")

    return fig_t, fig_sc, fig_ev, fig_split, fig_em


# ─────────────────────────────────────────────────────────────────────────────
# Citation network page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("citation-search-results", "data"),
    Output("citation-paper-select",   "data"),
    Input("citation-search",          "value"),
    prevent_initial_call=True,
)
def update_citation_search(term: str):
    if not term or len(term) < 3:
        return [], []
    try:
        df = dl.search_papers(term, limit=30)
        if df.empty:
            return [], []
        options = [
            {"label": f"{str(r['year'] or '')} — {str(r['title'] or '')[:70]}",
             "value": str(r["paper_id"])}
            for _, r in df.iterrows()
        ]
        return df.to_dict("records"), options
    except Exception:
        return [], []


@callback(
    Output("citation-graph",      "elements"),
    Output("citation-info-panel", "children"),
    Input("citation-paper-select","value"),
    Input("citation-max-nodes",   "value"),
    prevent_initial_call=True,
)
def update_citation_graph(paper_id: str, max_nodes: int):
    if not paper_id:
        return [], empty_states.no_data("Select a paper to explore.")

    max_nodes = min(int(max_nodes or 80), 150)

    try:
        paper   = dl.get_paper_detail(paper_id)
        refs_df = dl.get_paper_references(paper_id, max_refs=max_nodes - 1)
    except Exception as e:
        return [], empty_states.error_state(f"Query failed: {e}")

    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    title_short = (str(paper.get("title", paper_id))[:50] if paper else paper_id)
    nodes.append({"data": {"id": paper_id, "label": title_short, "type": "root", "size": 40}})
    node_ids.add(paper_id)

    for _, ref in refs_df.iterrows():
        rid    = str(ref["reference_id"])
        rlabel = str(ref.get("title") or rid)[:45].strip()
        if rid not in node_ids and len(nodes) < max_nodes:
            nodes.append({"data": {"id": rid, "label": rlabel, "type": "reference", "size": 20}})
            node_ids.add(rid)
        if rid in node_ids:
            edges.append({"data": {"source": paper_id, "target": rid}})

    if paper:
        cited = f"{int(paper['cited_by_count']):,}" if paper.get("cited_by_count") else "—"
        info_panel = html.Div([
            html.H4("Selected Paper", style={
                "color": COLORS["accent_cyan"], "fontSize": "11px", "fontWeight": 700,
                "textTransform": "uppercase", "letterSpacing": "0.06em", "margin": "0 0 12px 0",
            }),
            html.P(str(paper.get("title", "Unknown"))[:100], style={
                "color": COLORS["text_primary"], "fontSize": "13px",
                "lineHeight": "1.5", "margin": "0 0 12px 0",
            }),
            _info_row("Citations", cited),
            _info_row("Year",      str(paper.get("year", "—"))),
            _info_row("Journal",   str(paper.get("journal", "—"))[:40]),
            _info_row("Country",   str(paper.get("primary_country", "—"))),
            html.Hr(style={"borderColor": COLORS["border"], "margin": "14px 0"}),
            html.P(f"Graph: {len(nodes)} nodes · {len(edges)} edges",
                   style={"color": COLORS["text_secondary"], "fontSize": "11px"}),
            html.P(f"References shown: {len(refs_df)}",
                   style={"color": COLORS["text_dim"], "fontSize": "11px", "margin": "4px 0 0 0"}),
        ])
    else:
        info_panel = empty_states.no_data("Paper metadata not found.")

    return nodes + edges, info_panel


# ─────────────────────────────────────────────────────────────────────────────
# Geospatial page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("deck-map-container", "children"),
    Output("geo-country-table",  "children"),
    Output("chart-country-bars", "figure"),
    Input("geo-layer-select",    "value"),
    Input("filter-store",        "data"),
)
def update_geospatial(layer_type: str, fs: dict):
    fs = fs or {}

    try:
        df_ctry      = dl.get_country_paper_counts(fs)
        scatter_data = country_scatter_data(df_ctry)
        hex_data     = country_hex_data(df_ctry)
        fig_bars     = charts.bar_h(df_ctry.head(20), "papers", "country", "Papers by Country", 20)
    except Exception:
        df_ctry = None
        scatter_data = hex_data = []
        fig_bars = charts.empty_fig("Query failed")

    try:
        inst_data = institution_scatter_data(dl.get_institution_geo())
    except Exception:
        inst_data = []

    try:
        deck_component = build_deck_map(layer_type or "scatter", scatter_data, hex_data, inst_data)
    except Exception as e:
        deck_component = html.Div(
            f"Map unavailable: {e}",
            style={"color": COLORS["text_dim"], "padding": "40px", "textAlign": "center"},
        )

    try:
        country_table = _country_table(df_ctry)
    except Exception:
        country_table = empty_states.error_state("Could not load country data")

    return deck_component, country_table, fig_bars


def _country_table(df) -> html.Table:
    if df is None or df.empty:
        return empty_states.no_data("No country data.")
    rows = [
        html.Tr([
            html.Td(str(r["country"]),           style={**TABLE_ROW, "padding": "7px 12px", "fontWeight": 600}),
            html.Td(f"{int(r['papers']):,}",      style={**TABLE_ROW, "padding": "7px 12px", "color": COLORS["accent_cyan"], "fontWeight": 700, "textAlign": "right"}),
            html.Td(f"{float(r.get('avg_citations') or 0):.1f}",
                                                  style={**TABLE_ROW, "padding": "7px 12px", "color": COLORS["text_secondary"], "textAlign": "right"}),
        ])
        for _, r in df.head(30).iterrows()
    ]
    return html.Table([
        html.Thead(html.Tr([
            html.Th("Country",      style={**TABLE_HEADER}),
            html.Th("Papers",       style={**TABLE_HEADER, "textAlign": "right"}),
            html.Th("Avg Citations",style={**TABLE_HEADER, "textAlign": "right"}),
        ])),
        html.Tbody(rows),
    ], style=TABLE_STYLE)


# ─────────────────────────────────────────────────────────────────────────────
# Data Quality page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("quality-kpi-row",       "children"),
    Output("quality-coverage-bars", "children"),
    Output("chart-coverage-year",   "figure"),
    Output("chart-method-qa",       "figure"),
    Output("chart-sensor-qa",       "figure"),
    Output("chart-year-anomalies",  "figure"),
    Input("filter-store", "data"),
)
def update_quality(fs: dict):
    fs = fs or {}

    try:
        qa = dl.get_quality_stats(fs)
    except Exception:
        qa = {}

    kpi_items = [
        (f"{qa.get('doi_coverage', 0):.1f}%",      "DOI Coverage",      COLORS["accent_cyan"],  "✓"),
        (f"{qa.get('openalex_coverage', 0):.1f}%", "OpenAlex Coverage", COLORS["accent_teal"],  "⬡"),
        (f"{qa.get('abstract_coverage', 0):.1f}%", "Abstract Coverage", COLORS["accent_green"], "¶"),
        (f"{qa.get('refs_with_doi', 0):.1f}%",     "Refs w/ DOI",       COLORS["accent_amber"], "⟳"),
    ]
    kpi_row = dmc.SimpleGrid(
        cols={"base": 2, "sm": 4},
        children=[kpi_card(v, l, accent=a, icon=ic) for v, l, a, ic in kpi_items],
    )

    coverage_bars = html.Div([
        _pct_bar("DOI Coverage",        qa.get("doi_coverage", 0)),
        _pct_bar("OpenAlex Coverage",   qa.get("openalex_coverage", 0)),
        _pct_bar("Abstract Coverage",   qa.get("abstract_coverage", 0)),
        _pct_bar("Papers with Methods", 100 - qa.get("papers_no_methods", 100)),
        _pct_bar("Papers with Sensors", 100 - qa.get("papers_no_sensors", 100)),
        _pct_bar("Refs with DOI",       qa.get("refs_with_doi", 0)),
    ])

    try:
        df_cov = dl.get_coverage_by_year(fs)
        if not df_cov.empty:
            fig_cov = go.Figure()
            for col, clr in [
                ("with_doi",      COLORS["accent_cyan"]),
                ("with_openalex", COLORS["accent_teal"]),
                ("with_methods",  COLORS["accent_green"]),
                ("with_sensors",  COLORS["accent_amber"]),
            ]:
                if col in df_cov.columns:
                    fig_cov.add_trace(go.Scatter(
                        x=df_cov["year"], y=df_cov[col],
                        name=col.replace("_", " ").title(),
                        mode="lines", line=dict(color=clr, width=2),
                    ))
            fig_cov.update_layout(**{**CHART_LAYOUT, "height": 280,
                                     "title": {"text": "Coverage by Year", "font": {"size": 13}}})
        else:
            fig_cov = charts.empty_fig("No year data")
    except Exception:
        fig_cov = charts.empty_fig("Query failed")

    try:
        where = dl._paper_where(fs)
        wm  = int(_dbq(f"SELECT count(*) FROM papers WHERE {where} AND methods_count > 0").iloc[0, 0])
        wom = int(_dbq(f"SELECT count(*) FROM papers WHERE {where} AND methods_count = 0").iloc[0, 0])
        fig_mqa = charts.donut(
            pd.DataFrame({"cat": ["Has methods", "No methods"], "n": [wm, wom]}),
            "cat", "n", "Method Normalization",
        )
    except Exception:
        fig_mqa = charts.empty_fig("Query failed")

    try:
        ws  = int(_dbq(f"SELECT count(*) FROM papers WHERE {where} AND sensors_count > 0").iloc[0, 0])
        wos = int(_dbq(f"SELECT count(*) FROM papers WHERE {where} AND sensors_count = 0").iloc[0, 0])
        fig_sqa = charts.donut(
            pd.DataFrame({"cat": ["Has sensors", "No sensors"], "n": [ws, wos]}),
            "cat", "n", "Sensor Normalization",
        )
    except Exception:
        fig_sqa = charts.empty_fig("Query failed")

    try:
        df_yr = _dbq(
            "SELECT year, count(*) as n FROM papers "
            "WHERE NOT (year SIMILAR TO '[0-9]{4}' AND CAST(year AS INT) BETWEEN 1990 AND 2025) "
            "GROUP BY year ORDER BY n DESC LIMIT 20"
        )
        fig_anom = (
            charts.bar_v(df_yr, "year", "n", "Anomalous Year Values")
            if not df_yr.empty
            else charts.empty_fig("No year anomalies")
        )
    except Exception:
        fig_anom = charts.empty_fig("Query failed")

    return kpi_row, coverage_bars, fig_cov, fig_mqa, fig_sqa, fig_anom


# ─────────────────────────────────────────────────────────────────────────────
# Explorer page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("explorer-results-table", "children"),
    Input("explorer-search",         "value"),
    Input("explorer-filter-type",    "value"),
    prevent_initial_call=True,
)
def update_explorer_search(term: str, filter_type: str):
    if not term or len(term.strip()) < 2:
        return empty_states.no_data("Enter at least 2 characters to search.")
    try:
        df = dl.search_papers(term.strip(), limit=40)
        return search_results_table(df)
    except Exception as e:
        return empty_states.error_state(f"Search failed: {e}")


@callback(
    Output("explorer-selected-paper", "data"),
    Input({"type": "explorer-row", "paper_id": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_explorer_row(n_clicks_list):
    if not any(n_clicks_list):
        return no_update
    triggered = ctx.triggered_id
    if triggered and isinstance(triggered, dict):
        return triggered.get("paper_id")
    return no_update


@callback(
    Output("explorer-detail-panel", "children"),
    Input("explorer-selected-paper","data"),
    prevent_initial_call=True,
)
def update_explorer_detail(paper_id: str):
    if not paper_id:
        return empty_states.no_data("Select a paper from results.")
    try:
        paper      = dl.get_paper_detail(paper_id)
        topics_df  = dl.get_paper_topics(paper_id)
        authors_df = dl.get_paper_authors_detail(paper_id)
        refs_df    = dl.get_paper_references(paper_id, 20)
        return paper_detail_panel(paper, topics_df, authors_df, refs_df)
    except Exception as e:
        return empty_states.error_state(f"Could not load paper: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Flood Mapping Analytics page
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output("flood-kpi-row",        "children"),
    Output("flood-timeline-fig",   "figure"),
    Output("flood-cotopics-bar",   "figure"),
    Output("flood-methods-bar",    "figure"),
    Output("flood-methods-donut",  "figure"),
    Output("flood-sensors-bar",    "figure"),
    Output("flood-sensors-donut",  "figure"),
    Output("flood-accuracy-scatter","figure"),
    Output("flood-case-map",       "children"),
    Output("flood-papers-grid",    "rowData"),
    Output("flood-insights",       "children"),
    Input("filter-store", "data"),
)
def update_flood_page(fs: dict):
    fs = fs or {}

    # ── KPI cards ──────────────────────────────────────────────────────────────
    try:
        kpis = dfl.get_flood_kpis()
    except Exception:
        kpis = {}

    kpi_row = html.Div([
        kpi_card(f"{kpis.get('flood_papers', 0):,}",  "Flood Papers",       COLORS["accent_cyan"],  "Flood Risk topic"),
        kpi_card(f"{kpis.get('sar_papers', 0):,}",    "SAR / Radar Papers", COLORS["accent_teal"],  "Corpus-wide"),
        kpi_card(f"{kpis.get('sentinel', 0):,}",      "Sentinel Papers",    COLORS["accent_blue"],  "Optical SAR combined"),
        kpi_card(f"{kpis.get('ml_papers', 0):,}",     "AI / ML Papers",     COLORS["accent_green"], "Hydro-AI topic"),
        kpi_card(f"{kpis.get('avg_citations', 0):.1f}","Avg Citations",      COLORS["accent_amber"], "Flood topic papers"),
        kpi_card(f"{kpis.get('nse_papers', 0):,}",    "NSE Reports",        "#8b5cf6",              "Nash-Sutcliffe"),
        kpi_card(f"{kpis.get('oa_papers', 0):,}",     "OA Reports",         "#ec4899",              "Overall Accuracy"),
    ], style={
        "display": "grid",
        "gridTemplateColumns": "repeat(7, 1fr)",
        "gap": "12px",
    })

    # ── Timeline ───────────────────────────────────────────────────────────────
    try:
        df_tl = dfl.get_flood_timeline()
        fig_tl = go.Figure()
        fig_tl.add_trace(go.Scatter(
            x=df_tl["year"], y=df_tl["total_flood"],
            name="Total Flood", mode="lines+markers",
            line=dict(color=COLORS["accent_cyan"], width=2.5),
            fill="tozeroy", fillcolor="rgba(0,212,255,0.08)",
            marker=dict(size=4),
        ))
        fig_tl.add_trace(go.Scatter(
            x=df_tl["year"], y=df_tl["ai_ml"],
            name="AI / ML sub-topic", mode="lines+markers",
            line=dict(color=COLORS["accent_green"], width=2, dash="dot"),
            marker=dict(size=4),
        ))
        fig_tl.add_trace(go.Scatter(
            x=df_tl["year"], y=df_tl["sar"],
            name="SAR sub-topic", mode="lines+markers",
            line=dict(color=COLORS["accent_amber"], width=2, dash="dash"),
            marker=dict(size=4),
        ))
        fig_tl.update_layout(
            **CHART_LAYOUT,
            title="Annual Flood Paper Count",
            xaxis_title="Year", yaxis_title="Papers",
            hovermode="x unified",
        )
    except Exception:
        fig_tl = charts.empty_fig("Timeline query failed")

    # ── Co-occurring topics bar ────────────────────────────────────────────────
    try:
        df_co = dfl.get_flood_cotopics()
        df_co["short"] = df_co["topic_name"].str.slice(0, 30)
        fig_co = charts.bar_h(df_co, "n", "short", "Co-occurring Topics", 12)
    except Exception:
        fig_co = charts.empty_fig("Topic query failed")

    # ── Methods bar ───────────────────────────────────────────────────────────
    try:
        df_m = dfl.get_flood_methods()
        df_m["short"] = df_m["display_name"].str.slice(0, 32)
        fig_mbar = charts.bar_h(df_m, "paper_count", "short", "Top Flood Mapping Methods", 15)
    except Exception:
        fig_mbar = charts.empty_fig("Methods query failed")

    # ── Methods donut ──────────────────────────────────────────────────────────
    try:
        df_mc = dfl.get_method_categories()
        fig_mdonut = charts.donut(df_mc, "category", "total", "Method Families")
    except Exception:
        fig_mdonut = charts.empty_fig("Method categories failed")

    # ── Sensors bar ───────────────────────────────────────────────────────────
    try:
        df_s = dfl.get_top_sensors()
        df_s["short"] = df_s["display_name"].str.slice(0, 32)
        fig_sbar = charts.bar_h(df_s, "paper_count", "short", "Top Remote Sensing Systems", 12)
    except Exception:
        fig_sbar = charts.empty_fig("Sensor query failed")

    # ── Sensors donut ─────────────────────────────────────────────────────────
    try:
        df_sf = dfl.get_sensor_family_totals()
        fig_sdonut = charts.donut(df_sf, "family", "paper_count", "Sensor Families")
    except Exception:
        fig_sdonut = charts.empty_fig("Sensor families failed")

    # ── Accuracy vs Timeliness bubble chart ────────────────────────────────────
    try:
        fig_acc = go.Figure()
        categories = {}
        for row in ACCURACY_TIMELINESS:
            cat = row["category"]
            if cat not in categories:
                categories[cat] = {"x": [], "y": [], "size": [], "text": [], "hover": []}
            categories[cat]["x"].append(row["timeliness"])
            categories[cat]["y"].append(row["oa"])
            categories[cat]["size"].append(max(12, min(60, row["papers"] ** 0.5 * 3)))
            categories[cat]["text"].append(row["method"].replace("\n", " "))
            categories[cat]["hover"].append(
                f"<b>{row['method'].replace(chr(10), ' ')}</b><br>"
                f"OA: {row['oa']}%  |  F1: {row['f1']}<br>"
                f"Timeliness: {row['timeliness']}/10<br>"
                f"Papers: ~{row['papers']}"
            )

        for cat, d in categories.items():
            color = _CAT_COLORS.get(cat, "#94a3b8")
            fig_acc.add_trace(go.Scatter(
                x=d["x"], y=d["y"],
                mode="markers+text",
                name=cat,
                marker=dict(
                    size=d["size"],
                    color=color,
                    opacity=0.82,
                    line=dict(color="rgba(255,255,255,0.25)", width=1),
                ),
                text=d["text"],
                textposition="top center",
                textfont=dict(size=9, color="rgba(226,232,240,0.75)"),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=d["hover"],
            ))

        fig_acc.update_layout(
            **CHART_LAYOUT,
            title="Accuracy vs Timeliness Trade-off",
            xaxis=dict(
                **CHART_LAYOUT.get("xaxis", {}),
                title="Timeliness (1 = slow / calibration-heavy · 10 = near-real-time)",
                range=[0.5, 11], dtick=1,
            ),
            yaxis=dict(
                **CHART_LAYOUT.get("yaxis", {}),
                title="Overall Accuracy (%)",
                range=[65, 97],
            ),
            showlegend=True,
            height=500,
            annotations=[
                dict(x=9.5, y=73, text="← NRT / low accuracy",
                     showarrow=False, font=dict(color=COLORS["text_dim"], size=10)),
                dict(x=3.0, y=94, text="High accuracy / slow →",
                     showarrow=False, font=dict(color=COLORS["text_dim"], size=10)),
            ],
        )
    except Exception:
        fig_acc = charts.empty_fig("Accuracy/timeliness chart failed")

    # ── Case studies DeckGL map ────────────────────────────────────────────────
    try:
        case_map = build_case_study_map()
    except Exception as e:
        case_map = html.Div(
            f"Map unavailable: {e}",
            style={"color": COLORS["text_dim"], "padding": "40px", "textAlign": "center"},
        )

    # ── AG Grid row data ───────────────────────────────────────────────────────
    try:
        df_papers = dfl.get_flood_papers_table()
        grid_rows = df_papers.fillna("").to_dict("records")
    except Exception:
        grid_rows = []

    # ── Scientific insights panel ──────────────────────────────────────────────
    try:
        insight_list = dfl.get_flood_insights()
    except Exception:
        insight_list = []

    _icon_chars = {
        "trend": "↗", "pin": "◉", "satellite": "◎",
        "brain": "◆", "model": "◈", "metrics": "◇",
    }
    insights_div = html.Div([
        html.Div([
            html.Div([
                html.Span(_icon_chars.get(ins["icon"], "·"), style={
                    "color": ins["color"], "fontSize": "18px",
                    "minWidth": "24px", "fontWeight": 800,
                }),
                html.P(ins["text"], style={
                    "color": COLORS["text_primary"],
                    "fontSize": "13px",
                    "lineHeight": "1.6",
                    "margin": "0",
                }),
            ], style={"display": "flex", "gap": "12px", "alignItems": "flex-start"}),
        ], style={
            **GLASS_CARD,
            "padding":      "14px 18px",
            "marginBottom": "10px",
            "borderLeft":   f"3px solid {ins['color']}",
        })
        for ins in insight_list
    ]) if insight_list else empty_states.no_data("No insights available.")

    return (
        kpi_row, fig_tl, fig_co,
        fig_mbar, fig_mdonut,
        fig_sbar, fig_sdonut,
        fig_acc,
        case_map,
        grid_rows,
        insights_div,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Analytics page
# ─────────────────────────────────────────────────────────────────────────────

def _empty_fig(msg: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        **{**CHART_LAYOUT,
           "annotations": [dict(text=msg, x=0.5, y=0.5, showarrow=False,
                                font=dict(color=COLORS["text_secondary"], size=13),
                                xref="paper", yref="paper")]},
    )
    return fig


@callback(
    Output("analytics-kpi-row",        "children"),
    Output("chart-domain-nse",         "figure"),
    Output("chart-ai-trend",           "figure"),
    Output("chart-institutions-nse",   "figure"),
    Output("chart-regional",           "figure"),
    Output("chart-metric-domain",      "figure"),
    Input("url", "pathname"),
)
def update_analytics(pathname: str):
    if pathname != "/analytics":
        import dash
        return [dash.no_update] * 6

    C = COLORS

    # ── KPIs ─────────────────────────────────────────────────────────────────
    try:
        kpis = dn4j.get_analytics_kpis()
    except Exception:
        kpis = {}

    kpi_row = html.Div([
        kpi_card(f"{kpis.get('total_facts', 0):,}",  "NSE Facts",       C["accent_cyan"]),
        kpi_card(f"{kpis.get('mean_nse', 0):.3f}",   "Mean NSE",        C["accent_teal"],
                 sub="corpus-wide"),
        kpi_card(f"{kpis.get('pct_excellent', 0):.1f}%", "Excellent (≥0.75)", C["accent_green"]),
        kpi_card(kpis.get("top_institution", "—"),    "Top Institution", C["accent_amber"],
                 sub="by avg NSE"),
    ], style={
        "display": "grid",
        "gridTemplateColumns": "repeat(4, 1fr)",
        "gap": "16px",
    })

    # ── Q2: Domain NSE ────────────────────────────────────────────────────────
    try:
        df_domain = dn4j.get_domain_nse()
        if df_domain.empty:
            fig_domain = _empty_fig("No NSE data by domain")
        else:
            df_domain = df_domain.sort_values("avg_nse")
            colors_bar = [
                C["accent_cyan"] if d == "AI / ML Forecasting" else
                C["accent_blue"] if d == "Hydraulic Modeling" else
                C["accent_teal"] if d == "Remote Sensing" else
                C["accent_green"]
                for d in df_domain["domain"]
            ]
            fig_domain = go.Figure(go.Bar(
                x=df_domain["avg_nse"],
                y=df_domain["domain"],
                orientation="h",
                marker_color=colors_bar,
                error_x=dict(
                    type="data", array=df_domain["std_nse"].tolist(),
                    color=C["border_light"], thickness=1.5, width=4,
                ),
                text=[f"{v:.3f}  (n={n})" for v, n in
                      zip(df_domain["avg_nse"], df_domain["n"])],
                textposition="outside",
                textfont=dict(color=C["text_secondary"], size=11),
                customdata=df_domain[["pct_excellent", "n"]].values,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "avg NSE: %{x:.3f}<br>"
                    "% excellent: %{customdata[0]:.1%}<br>"
                    "papers: %{customdata[1]}<extra></extra>"
                ),
            ))
            fig_domain.update_layout(
                **CHART_LAYOUT,
                xaxis_title="Average NSE",
                xaxis=dict(**CHART_LAYOUT["xaxis"], range=[0, 1.05]),
                showlegend=False,
                height=260,
            )
    except Exception as exc:
        fig_domain = _empty_fig(str(exc))

    # ── Q4: AI trend ──────────────────────────────────────────────────────────
    try:
        df_ai = dn4j.get_ai_nse_trend()
        if df_ai.empty:
            fig_ai = _empty_fig("No AI trend data")
        else:
            fig_ai = go.Figure()
            # ±1 std band
            fig_ai.add_trace(go.Scatter(
                x=list(df_ai["year"]) + list(df_ai["year"])[::-1],
                y=list((df_ai["avg_nse"] + df_ai["std_nse"]).clip(upper=1.0)) +
                  list((df_ai["avg_nse"] - df_ai["std_nse"]).clip(lower=-1.0))[::-1],
                fill="toself",
                fillcolor="rgba(0,212,255,0.08)",
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            ))
            fig_ai.add_trace(go.Scatter(
                x=df_ai["year"], y=df_ai["avg_nse"],
                mode="lines+markers",
                name="avg NSE",
                line=dict(color=C["accent_cyan"], width=2),
                marker=dict(size=7, color=C["accent_cyan"]),
                customdata=df_ai[["papers", "pct_excellent"]].values,
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "avg NSE: %{y:.3f}<br>"
                    "papers: %{customdata[0]}<br>"
                    "% excellent: %{customdata[1]:.1%}<extra></extra>"
                ),
            ))
            fig_ai.add_trace(go.Bar(
                x=df_ai["year"], y=df_ai["pct_excellent"],
                name="% excellent",
                yaxis="y2",
                marker_color="rgba(16,185,129,0.25)",
                hovertemplate="<b>%{x}</b><br>% excellent: %{y:.1%}<extra></extra>",
            ))
            fig_ai.update_layout(
                **CHART_LAYOUT,
                yaxis=dict(**CHART_LAYOUT["yaxis"],
                           title="avg NSE", range=[0, 1.05]),
                yaxis2=dict(
                    overlaying="y", side="right",
                    range=[0, 1.4], tickformat=".0%",
                    gridcolor="transparent",
                    tickfont=dict(color=C["text_secondary"]),
                    title_font=dict(color=C["text_secondary"]),
                    title="% excellent",
                ),
                xaxis_title="Year",
                legend=dict(**CHART_LAYOUT["legend"],
                            orientation="h", y=1.08, x=0),
                height=280,
            )
    except Exception as exc:
        fig_ai = _empty_fig(str(exc))

    # ── Q3: Institutions ──────────────────────────────────────────────────────
    try:
        df_inst = dn4j.get_institution_performance()
        if df_inst.empty:
            fig_inst = _empty_fig("No institution data")
        else:
            df_inst = df_inst.sort_values("avg_nse").tail(15)
            label = [
                f"{r['institution'][:38]}  ({r['country'] or '??'})"
                for _, r in df_inst.iterrows()
            ]
            fig_inst = go.Figure(go.Bar(
                x=df_inst["avg_nse"],
                y=label,
                orientation="h",
                marker=dict(
                    color=df_inst["pct_excellent"],
                    colorscale=[[0, C["accent_teal"]], [0.5, C["accent_cyan"]], [1, C["accent_green"]]],
                    showscale=True,
                    colorbar=dict(
                        title=dict(text="% excellent", font=dict(color=C["text_secondary"], size=11)),
                        tickformat=".0%",
                        tickfont=dict(color=C["text_secondary"], size=10),
                        len=0.7,
                    ),
                ),
                text=[f"{v:.3f}" for v in df_inst["avg_nse"]],
                textposition="outside",
                textfont=dict(color=C["text_secondary"], size=11),
                customdata=df_inst[["papers", "pct_excellent"]].values,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "avg NSE: %{x:.3f}<br>"
                    "papers: %{customdata[0]}<br>"
                    "% excellent: %{customdata[1]:.1%}<extra></extra>"
                ),
            ))
            fig_inst.update_layout(
                **CHART_LAYOUT,
                xaxis=dict(**CHART_LAYOUT["xaxis"],
                           title="Average NSE", range=[0, 1.05]),
                showlegend=False,
                height=400,
                margin=dict(l=12, r=100, t=24, b=12),
            )
    except Exception as exc:
        fig_inst = _empty_fig(str(exc))

    # ── Q1: Regional ──────────────────────────────────────────────────────────
    try:
        df_reg = dn4j.get_regional_dominance()
        if df_reg.empty:
            fig_reg = _empty_fig("No regional data")
        else:
            df_reg = df_reg.sort_values("papers", ascending=True).tail(25)
            # colour by dominant topic category
            def _topic_color(t: str) -> str:
                t = t or ""
                if "Watershed" in t or "Hydrology" in t:  return C["accent_cyan"]
                if "Flood Risk" in t or "Risk" in t:       return C["accent_amber"]
                if "AI" in t or "Neural" in t:             return C["accent_green"]
                if "SAR" in t or "Remote" in t:            return "#8b5cf6"
                return C["accent_teal"]

            bar_colors = [_topic_color(t) for t in df_reg["dominant_topic"]]
            fig_reg = go.Figure(go.Bar(
                x=df_reg["papers"],
                y=df_reg["country"],
                orientation="h",
                marker_color=bar_colors,
                customdata=df_reg[["dominant_topic", "topic_papers"]].values,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Papers: %{x}<br>"
                    "Dominant: %{customdata[0]}<br>"
                    "Topic papers: %{customdata[1]}<extra></extra>"
                ),
                text=df_reg["papers"],
                textposition="outside",
                textfont=dict(color=C["text_secondary"], size=10),
            ))
            # legend entries
            for label, color in [
                ("Watershed / Hydrology", C["accent_cyan"]),
                ("Flood Risk",            C["accent_amber"]),
                ("AI / ML",               C["accent_green"]),
                ("Remote Sensing",        "#8b5cf6"),
                ("Other",                 C["accent_teal"]),
            ]:
                fig_reg.add_trace(go.Bar(
                    x=[None], y=[None], orientation="h",
                    marker_color=color, name=label, showlegend=True,
                ))
            fig_reg.update_layout(
                **CHART_LAYOUT,
                xaxis_title="Papers in corpus",
                barmode="overlay",
                legend=dict(**CHART_LAYOUT["legend"],
                            orientation="v", x=1.0, y=0,
                            font=dict(size=10)),
                height=480,
                margin=dict(l=12, r=160, t=24, b=12),
            )
    except Exception as exc:
        fig_reg = _empty_fig(str(exc))

    # ── Q5: Metric by domain ──────────────────────────────────────────────────
    try:
        df_met = dn4j.get_metric_by_domain()
        if df_met.empty:
            fig_met = _empty_fig("No metric data")
        else:
            # pivot: rows = metric, cols = domain
            pivot = (
                df_met.groupby(["domain", "metric"])["papers"]
                .sum().unstack(level=0, fill_value=0)
            )
            # keep top 8 metrics by total
            top_metrics = pivot.sum(axis=1).nlargest(8).index
            pivot = pivot.loc[top_metrics]

            domain_colors = {
                "AI / ML":            C["accent_cyan"],
                "Hydraulic":          C["accent_blue"],
                "Hydrological":       C["accent_green"],
                "Remote Sensing":     "#8b5cf6",
            }
            fig_met = go.Figure()
            for domain in pivot.columns:
                fig_met.add_trace(go.Bar(
                    name=domain,
                    x=pivot[domain].values,
                    y=[m.replace("metric.", "").upper() for m in pivot.index],
                    orientation="h",
                    marker_color=domain_colors.get(domain, C["accent_teal"]),
                    customdata=[[domain]] * len(pivot),
                    hovertemplate=(
                        "<b>%{y}</b> — " + domain + "<br>"
                        "Papers: %{x}<extra></extra>"
                    ),
                ))
            fig_met.update_layout(
                **CHART_LAYOUT,
                barmode="group",
                xaxis_title="Papers reporting metric",
                legend=dict(**CHART_LAYOUT["legend"],
                            orientation="h", y=1.06, x=0,
                            font=dict(size=10)),
                height=480,
            )
    except Exception as exc:
        fig_met = _empty_fig(str(exc))

    return kpi_row, fig_domain, fig_ai, fig_inst, fig_reg, fig_met


# ═════════════════════════════════════════════════════════════════════════════
# Research page callbacks
# ═════════════════════════════════════════════════════════════════════════════

# ── Populate country and topic dropdowns once per session ─────────────────────

@callback(
    Output("research-country-dd", "options"),
    Output("research-topic-dd",   "options"),
    Input("url", "pathname"),
)
def _research_populate_dropdowns(pathname: str):
    if pathname != "/research":
        return no_update, no_update
    try:
        countries = _dbq(
            f"SELECT DISTINCT primary_country AS v FROM 'data/analytics/papers.parquet' "
            f"WHERE primary_country IS NOT NULL ORDER BY primary_country"
        )
        country_opts = [{"label": r, "value": r} for r in countries["v"].tolist()]
    except Exception:
        country_opts = []

    try:
        topics_df = _dbq(
            f"SELECT topic_name FROM 'data/analytics/topics.parquet' ORDER BY topic_name"
        )
        topic_opts = [{"label": r, "value": r} for r in topics_df["topic_name"].tolist()]
    except Exception:
        topic_opts = []

    return country_opts, topic_opts


# ── Callback 1: run search → populate all evidence tables ────────────────────

def _kpi_card_simple(label: str, value: str) -> html.Div:
    return html.Div([
        html.Div(value, style={
            "fontSize": "24px", "fontWeight": 800,
            "color": COLORS["accent_cyan"], "lineHeight": 1,
        }),
        html.Div(label, style={
            "fontSize": "11px", "color": COLORS["text_dim"],
            "marginTop": "4px", "letterSpacing": "0.04em",
        }),
    ], style={
        "background":   COLORS["bg_card"],
        "border":       f"1px solid {COLORS['border']}",
        "borderRadius": "10px",
        "padding":      "14px 18px",
        "minWidth":     "110px",
    })


def _doi_cell(doi) -> html.Td:
    C = COLORS
    if doi and str(doi).startswith("10."):
        inner = html.A(
            str(doi)[:30],
            href=f"https://doi.org/{doi}",
            target="_blank",
            style={"color": C["accent_cyan"], "textDecoration": "none", "fontSize": "11px"},
        )
    else:
        inner = html.Span("—", style={"color": C["text_dim"], "fontSize": "11px"})
    return html.Td(inner, style={
        "padding": "5px 8px",
        "borderBottom": f"1px solid {C['border']}",
        "whiteSpace": "nowrap",
    })


def _source_badge(layer: str) -> html.Span:
    C = COLORS
    colors = {
        "parquet":      C["accent_cyan"],
        "neo4j":        C["accent_teal"],
        "chromadb":     C["accent_amber"],
        "numeric_fact": C["accent_green"],
        "openalex":     "#8b5cf6",
    }
    col = colors.get(layer, C["text_dim"])
    return html.Span(layer, style={
        "background": "rgba(0,0,0,0.3)",
        "color": col,
        "borderRadius": "4px",
        "padding": "1px 6px",
        "fontSize": "10px",
        "fontWeight": 600,
        "border": f"1px solid {col}",
    })


def _th(label: str) -> html.Th:
    return html.Th(label, style={
        "padding": "6px 8px", "color": COLORS["text_dim"],
        "fontSize": "11px", "fontWeight": 600,
        "borderBottom": f"1px solid {COLORS['border']}",
        "textAlign": "left", "whiteSpace": "nowrap",
    })


def _td(content, color=None) -> html.Td:
    return html.Td(content, style={
        "padding": "5px 8px", "fontSize": "11px",
        "color": color or COLORS["text_secondary"],
        "borderBottom": f"1px solid {COLORS['border']}",
    })


def _papers_table(df: pd.DataFrame) -> html.Table:
    C = COLORS
    if df is None or df.empty:
        return html.Div("No papers found.", style={"color": C["text_dim"], "fontSize": "12px"})
    rows = []
    for _, r in df.head(15).iterrows():
        doi = r.get("doi")
        rows.append(html.Tr([
            html.Td(str(r.get("title", ""))[:60], style={
                "padding": "6px 8px", "fontSize": "11px", "color": C["text_primary"],
                "borderBottom": f"1px solid {C['border']}",
                "maxWidth": "240px", "overflow": "hidden",
                "textOverflow": "ellipsis", "whiteSpace": "nowrap",
            }),
            _td(str(r.get("year", "")), C["text_dim"]),
            _td(str(r.get("primary_country", ""))),
            _td(str(r.get("cited_by_count", "")), C["accent_cyan"]),
            _doi_cell(doi),
            html.Td(_source_badge("parquet"), style={
                "padding": "5px 8px",
                "borderBottom": f"1px solid {C['border']}",
            }),
        ]))
    return html.Table([
        html.Thead(html.Tr([_th(l) for l in ["Title", "Year", "Country", "Citations", "DOI", "Source"]])),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})


def _metrics_table(df: pd.DataFrame) -> html.Table:
    if df is None or df.empty:
        return html.Div("No metric data.", style={"color": COLORS["text_dim"], "fontSize": "12px"})
    rows = []
    for _, r in df.iterrows():
        pct = f"{float(r.get('pct_excellent', 0))*100:.1f}%"
        rows.append(html.Tr([
            html.Td(str(r.get("metric", "")).replace("metric.", "").upper(), style={
                "padding": "5px 8px", "fontSize": "12px",
                "color": COLORS["accent_cyan"],
                "borderBottom": f"1px solid {COLORS['border']}",
                "fontWeight": 600,
            }),
            html.Td(str(r.get("n", "")), style={
                "padding": "5px 8px", "fontSize": "12px",
                "color": COLORS["text_secondary"],
                "borderBottom": f"1px solid {COLORS['border']}",
            }),
            html.Td(f"{float(r.get('mean', 0)):.3f}", style={
                "padding": "5px 8px", "fontSize": "12px",
                "color": COLORS["text_secondary"],
                "borderBottom": f"1px solid {COLORS['border']}",
            }),
            html.Td(pct, style={
                "padding": "5px 8px", "fontSize": "12px",
                "color": COLORS["accent_green"],
                "borderBottom": f"1px solid {COLORS['border']}",
            }),
        ]))
    return html.Table([
        html.Thead(html.Tr([
            html.Th(lbl, style={
                "padding": "5px 8px", "color": COLORS["text_dim"],
                "fontSize": "11px", "fontWeight": 600,
                "borderBottom": f"1px solid {COLORS['border']}",
            }) for lbl in ["Metric", "N", "Mean", "% Excellent"]
        ])),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})


def _authors_table(df: pd.DataFrame) -> html.Table:
    if df is None or df.empty:
        return html.Div("No author data.", style={"color": COLORS["text_dim"], "fontSize": "12px"})
    rows = []
    for _, r in df.head(10).iterrows():
        h = r.get("h_index")
        cit = r.get("cited_by_count")
        rows.append(html.Tr([
            html.Td(str(r.get("display_name", ""))[:35], style={
                "padding": "5px 8px", "fontSize": "11px",
                "color": COLORS["text_primary"],
                "borderBottom": f"1px solid {COLORS['border']}",
            }),
            html.Td(str(r.get("paper_count", "")), style={
                "padding": "5px 8px", "fontSize": "11px",
                "color": COLORS["text_secondary"],
                "borderBottom": f"1px solid {COLORS['border']}",
                "textAlign": "right",
            }),
            html.Td(str(h) if h is not None else "—", style={
                "padding": "5px 8px", "fontSize": "11px",
                "color": COLORS["accent_cyan"],
                "borderBottom": f"1px solid {COLORS['border']}",
                "textAlign": "right",
            }),
            html.Td(str(cit) if cit is not None else "—", style={
                "padding": "5px 8px", "fontSize": "11px",
                "color": COLORS["text_secondary"],
                "borderBottom": f"1px solid {COLORS['border']}",
                "textAlign": "right",
            }),
        ]))
    return html.Table([
        html.Thead(html.Tr([
            html.Th(lbl, style={
                "padding": "5px 8px", "color": COLORS["text_dim"],
                "fontSize": "11px", "fontWeight": 600,
                "borderBottom": f"1px solid {COLORS['border']}",
            }) for lbl in ["Author", "Papers", "h-index", "Citations"]
        ])),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})


def _domain_chart(df: pd.DataFrame):
    import plotly.graph_objects as go
    if df is None or df.empty:
        return _empty_fig("No domain data")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["avg_nse"].tolist(),
        y=df["domain"].tolist(),
        orientation="h",
        marker=dict(
            color=df["avg_nse"].tolist(),
            colorscale=[[0, COLORS["accent_teal"]], [0.5, COLORS["accent_cyan"]], [1, COLORS["accent_green"]]],
            cmin=0, cmax=1,
        ),
        text=[f"{v:.3f}" for v in df["avg_nse"]],
        textposition="outside",
        textfont=dict(color=COLORS["text_secondary"], size=11),
        customdata=df[["n", "pct_excellent"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "avg NSE: %{x:.3f}<br>"
            "papers: %{customdata[0]}<br>"
            "% excellent: %{customdata[1]:.1%}<extra></extra>"
        ),
    ))
    fig.update_layout(
        **CHART_LAYOUT,
        xaxis=dict(**CHART_LAYOUT["xaxis"], title="Average NSE", range=[0, 1.1]),
        showlegend=False,
        height=220,
        margin=dict(l=12, r=80, t=12, b=12),
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def _graph_table(evidence: list) -> html.Div:
    C = COLORS
    if not evidence:
        return html.Div("No graph evidence.", style={"color": C["text_dim"], "fontSize": "12px"})
    items = []
    for e in evidence[:12]:
        if e.get("type") == "top_nse_paper":
            doi = e.get("doi")
            items.append(html.Div([
                html.Span(f"NSE {e.get('avg_nse', 0):.3f}", style={
                    "background": C["bg_input"], "color": C["accent_green"],
                    "borderRadius": "4px", "padding": "2px 6px", "fontSize": "11px",
                    "fontWeight": 700, "marginRight": "8px",
                }),
                html.Span(f"{str(e.get('title', ''))[:60]}  ({e.get('year', '')})", style={
                    "color": C["text_secondary"], "fontSize": "12px", "marginRight": "8px",
                }),
                _doi_cell(doi) if doi else html.Span("—", style={"color": C["text_dim"], "fontSize": "11px"}),
                html.Span("  ", style={}),
                _source_badge("neo4j"),
            ], style={"padding": "5px 0", "borderBottom": f"1px solid {C['border']}",
                      "display": "flex", "alignItems": "center", "gap": "4px"}))
        elif e.get("type") == "metric_count":
            items.append(html.Div([
                html.Span(str(e.get("metric", "")).replace("metric.", "").upper(), style={
                    "background": C["bg_input"], "color": C["accent_cyan"],
                    "borderRadius": "4px", "padding": "2px 6px", "fontSize": "11px",
                    "fontWeight": 700, "marginRight": "8px",
                }),
                html.Span(f"n={e.get('n', 0)}  mean={e.get('mean', 0)}", style={
                    "color": C["text_secondary"], "fontSize": "12px", "marginRight": "8px",
                }),
                _source_badge("neo4j"),
            ], style={"padding": "5px 0", "borderBottom": f"1px solid {C['border']}",
                      "display": "flex", "alignItems": "center", "gap": "4px"}))
    return html.Div(items)


def _semantic_table(hits: list) -> html.Div:
    C = COLORS
    if not hits:
        return html.Div("No semantic hits (enter a query).", style={
            "color": C["text_dim"], "fontSize": "12px",
        })
    items = []
    for h in hits[:8]:
        doi   = h.get("doi")
        title = (h.get("title") or "")[:50]
        text  = (h.get("chunk_text") or h.get("text") or "")[:280]
        items.append(html.Div([
            html.Div([
                html.Span(f"score {h.get('score', 0):.3f}", style={
                    "background": C["bg_input"], "color": C["accent_amber"],
                    "borderRadius": "4px", "padding": "2px 6px", "fontSize": "11px",
                    "fontWeight": 700, "marginRight": "8px",
                }),
                html.Span(f"{h.get('section_title', '')}  ·  ", style={
                    "color": C["text_dim"], "fontSize": "11px",
                }),
                (_doi_cell(doi) if doi else html.Span(
                    h.get("paper_id", "")[:24],
                    style={"color": C["text_dim"], "fontSize": "11px"},
                )),
                html.Span("  ", style={}),
                _source_badge("chromadb"),
            ], style={"display": "flex", "alignItems": "center", "gap": "4px", "flexWrap": "wrap"}),
            html.Div(title, style={
                "color": C["text_primary"], "fontSize": "12px",
                "marginTop": "3px", "fontWeight": 600,
            }) if title else None,
            html.Div(
                f'"{text}"',
                style={"color": C["text_secondary"], "fontSize": "12px",
                       "marginTop": "4px", "fontStyle": "italic"},
            ),
        ], style={
            "padding": "8px 0",
            "borderBottom": f"1px solid {C['border']}",
        }))
    return html.Div(items)


def _numeric_facts_table(facts: list) -> html.Div:
    C = COLORS
    if not facts:
        return html.Div("No individual fact data.", style={"color": C["text_dim"], "fontSize": "12px"})
    rows = []
    for f in facts[:20]:
        val  = f.get("value")
        conf = f.get("confidence")
        doi  = f.get("doi")
        val_s  = f"{float(val):.3f}" if val is not None else "—"
        conf_color = C["accent_green"] if (conf or 0) >= 0.75 else (
            C["accent_amber"] if (conf or 0) >= 0.5 else C["text_dim"]
        )
        rows.append(html.Tr([
            html.Td(str(f.get("metric", "")).replace("metric.", "").upper(), style={
                "padding": "5px 8px", "fontSize": "12px",
                "color": C["accent_cyan"], "fontWeight": 700,
                "borderBottom": f"1px solid {C['border']}",
            }),
            _td(val_s, C["text_primary"]),
            html.Td(f"{float(conf):.2f}" if conf is not None else "—", style={
                "padding": "5px 8px", "fontSize": "11px", "color": conf_color,
                "borderBottom": f"1px solid {C['border']}",
            }),
            _doi_cell(doi),
            html.Td(str(f.get("title", ""))[:40], style={
                "padding": "5px 8px", "fontSize": "11px", "color": C["text_secondary"],
                "borderBottom": f"1px solid {C['border']}",
                "maxWidth": "200px", "overflow": "hidden",
                "textOverflow": "ellipsis", "whiteSpace": "nowrap",
            }),
            html.Td(str(f.get("year", "")), style={
                "padding": "5px 8px", "fontSize": "11px", "color": C["text_dim"],
                "borderBottom": f"1px solid {C['border']}",
            }),
            html.Td(_source_badge("numeric_fact"), style={
                "padding": "5px 8px",
                "borderBottom": f"1px solid {C['border']}",
            }),
        ]))
    return html.Table([
        html.Thead(html.Tr([_th(l) for l in ["Metric", "Value", "Conf", "DOI", "Title", "Year", "Source"]])),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})


@callback(
    Output("research-kpi-row",          "children"),
    Output("table-research-papers",     "children"),
    Output("table-research-metrics",    "children"),
    Output("table-research-authors",    "children"),
    Output("chart-research-domains",    "children"),
    Output("table-research-graph",      "children"),
    Output("table-research-semantic",   "children"),
    Output("research-numeric-facts",    "children"),
    Output("research-results-section",  "style"),
    Output("research-evidence-store",   "data"),
    Input("btn-research-search",        "n_clicks"),
    State("research-semantic-input",    "value"),
    State("research-year-slider",       "value"),
    State("research-country-dd",        "value"),
    State("research-topic-dd",          "value"),
    State("research-domain-dd",         "value"),
    State("research-metric-dd",         "value"),
    State("research-metric-min",        "value"),
    State("research-metric-max",        "value"),
    State("research-min-citations",     "value"),
    prevent_initial_call=True,
)
def run_research_query(
    n_clicks,
    semantic_q, year_range, countries, topics, domains,
    metrics, metric_min, metric_max, min_cit,
):
    fs = {
        "year_min":     (year_range or [1995, 2025])[0],
        "year_max":     (year_range or [1995, 2025])[1],
        "countries":    countries or [],
        "topics":       topics or [],
        "domains":      domains or [],
        "metrics":      metrics or [],
        "metric_min":   metric_min,
        "metric_max":   metric_max,
        "min_citations": int(min_cit or 0),
        "semantic_query": (semantic_q or "").strip(),
    }

    pack = rqs.build_evidence_pack(fs.get("semantic_query", ""), fs)

    # ── KPI cards ────────────────────────────────────────────────────────────
    corpus = pack.get("corpus_summary") or pack.get("corpus") or {}
    kpis = [
        _kpi_card_simple("Papers",     f"{corpus.get('papers', 0):,}"),
        _kpi_card_simple("Authors",    f"{corpus.get('authors', 0):,}"),
        _kpi_card_simple("Topics",     f"{corpus.get('topics', 0):,}"),
        _kpi_card_simple("Year Range", f"{fs['year_min']}–{fs['year_max']}"),
    ]

    # ── Papers table — includes doi column ───────────────────────────────────
    try:
        papers_df = _dbq(
            f"SELECT title, year, primary_country, cited_by_count, doi "
            f"FROM 'data/analytics/papers.parquet' "
            f"WHERE year SIMILAR TO '[0-9]{{4}}' "
            f"  AND CAST(year AS INT) BETWEEN {fs['year_min']} AND {fs['year_max']} "
            + (f"  AND primary_country IN ({','.join(repr(c) for c in fs['countries'])}) " if fs.get("countries") else "")
            + (f"  AND cited_by_count >= {fs['min_citations']} " if fs['min_citations'] else "")
            + f"ORDER BY cited_by_count DESC NULLS LAST LIMIT 20"
        )
        papers_tbl = _papers_table(papers_df)
    except Exception as exc:
        papers_tbl = html.Div(f"Error: {exc}", style={"color": COLORS["text_dim"], "fontSize": "12px"})

    metrics_tbl   = _metrics_table(pack.get("metrics"))
    authors_tbl   = _authors_table(pack.get("authors"))
    domains_chart = _domain_chart(pack.get("domains"))
    graph_tbl     = _graph_table(pack.get("graph_evidence") or pack.get("graph") or [])
    semantic_tbl  = _semantic_table(pack.get("semantic_evidence") or pack.get("semantic") or [])
    nf_tbl        = _numeric_facts_table(pack.get("numeric_fact_evidence") or [])

    results_style = {"display": "block"}

    # Store filter state for AI synthesis
    evidence_json = {"query": pack.get("query"), "filters": fs}

    return (kpis, papers_tbl, metrics_tbl, authors_tbl,
            domains_chart, graph_tbl, semantic_tbl,
            nf_tbl, results_style, evidence_json)


# ── Callback 2: AI synthesis ──────────────────────────────────────────────────

@callback(
    Output("research-ai-output", "children"),
    Output("research-ai-meta",   "children"),
    Input("btn-research-ai",     "n_clicks"),
    State("research-evidence-store", "data"),
    State("research-semantic-input", "value"),
    State("research-year-slider",    "value"),
    State("research-country-dd",     "value"),
    State("research-topic-dd",       "value"),
    State("research-domain-dd",      "value"),
    State("research-metric-dd",      "value"),
    State("research-metric-min",     "value"),
    State("research-metric-max",     "value"),
    State("research-min-citations",  "value"),
    prevent_initial_call=True,
)
def generate_research_synthesis(
    n_clicks, evidence_json,
    semantic_q, year_range, countries, topics, domains,
    metrics, metric_min, metric_max, min_cit,
):
    fs = {
        "year_min":     (year_range or [1995, 2025])[0],
        "year_max":     (year_range or [1995, 2025])[1],
        "countries":    countries or [],
        "topics":       topics or [],
        "domains":      domains or [],
        "metrics":      metrics or [],
        "metric_min":   metric_min,
        "metric_max":   metric_max,
        "min_citations": int(min_cit or 0),
        "semantic_query": (semantic_q or "").strip(),
    }

    pack = rqs.build_evidence_pack(fs.get("semantic_query", ""), fs)
    prompt = ai_gateway.build_research_prompt(pack)

    result = ai_gateway.call_ai_with_gateway(
        prompt=prompt,
        config_kwargs={"max_output_tokens": 8192, "temperature": 0.2},
        api_key=GEMINI_API_KEY,
    )

    if not result.get("ok"):
        reason = result.get("fallback_reason", "unknown")
        attempts = result.get("attempts") or []
        meta = html.Span(
            f"AI unavailable — reason: {reason}",
            style={"color": COLORS["accent_amber"]},
        )
        output = html.Div(
            f"Synthesis not available: {reason}. "
            f"Attempted {len(attempts)} model(s). "
            "Check GEMINI_API_KEY or model availability.",
            style={"color": COLORS["text_dim"], "fontSize": "13px"},
        )
        return output, meta

    model_used  = result.get("model_used", "?")
    attempts    = result.get("attempts") or []
    latency_ms  = sum(a.get("latency_ms", 0) for a in attempts)
    meta = html.Span(
        f"Model: {model_used}  ·  {latency_ms:,} ms  ·  {len(attempts)} attempt(s)",
        style={"color": COLORS["text_dim"]},
    )

    text = result.get("text", "")
    paragraphs = [
        html.P(p.strip(), style={
            "color": COLORS["text_secondary"],
            "fontSize": "13px",
            "lineHeight": "1.65",
            "marginBottom": "10px",
        })
        for p in text.split("\n\n") if p.strip()
    ]
    return html.Div(paragraphs), meta


# ── Paper Detail panel ────────────────────────────────────────────────────────

def _paper_detail_panel(detail: dict) -> html.Div:
    C = COLORS
    doi   = detail.get("doi")
    title = detail.get("title") or "Unknown title"
    year  = detail.get("year") or ""
    journ = detail.get("journal") or ""
    auth  = detail.get("authors") or []
    ab    = detail.get("abstract") or ""
    mets  = detail.get("metrics") or []
    chunks= detail.get("semantic_chunks") or []

    def _row(label, value, color=None):
        return html.Div([
            html.Span(label, style={
                "color": C["text_dim"], "fontSize": "11px",
                "fontWeight": 600, "minWidth": "90px", "display": "inline-block",
            }),
            html.Span(str(value), style={"color": color or C["text_primary"], "fontSize": "12px"}),
        ], style={"marginBottom": "6px"})

    doi_el = (
        html.A(doi, href=f"https://doi.org/{doi}", target="_blank",
               style={"color": C["accent_cyan"], "fontSize": "12px"})
        if doi else html.Span("—", style={"color": C["text_dim"], "fontSize": "12px"})
    )

    # Metrics mini-table
    met_rows = []
    for m in mets[:10]:
        val = m.get("value")
        conf = m.get("confidence")
        met_rows.append(html.Tr([
            html.Td(str(m.get("metric", "")).replace("metric.", "").upper(), style={
                "padding": "3px 6px", "fontSize": "11px",
                "color": C["accent_cyan"], "fontWeight": 700,
            }),
            html.Td(f"{float(val):.3f}" if val is not None else "—", style={
                "padding": "3px 6px", "fontSize": "11px", "color": C["text_primary"],
            }),
            html.Td(f"{float(conf):.2f}" if conf is not None else "—", style={
                "padding": "3px 6px", "fontSize": "11px", "color": C["text_dim"],
            }),
            html.Td(m.get("period_type") or "—", style={
                "padding": "3px 6px", "fontSize": "11px", "color": C["text_dim"],
            }),
        ]))
    met_table = html.Table([
        html.Thead(html.Tr([
            html.Th(l, style={"padding": "3px 6px", "color": C["text_dim"],
                              "fontSize": "10px", "fontWeight": 600})
            for l in ["Metric", "Value", "Conf", "Period"]
        ])),
        html.Tbody(met_rows),
    ], style={"borderCollapse": "collapse", "marginTop": "8px"}) if met_rows else html.Div(
        "No numeric facts.", style={"color": C["text_dim"], "fontSize": "11px"}
    )

    # Semantic chunks
    chunk_items = []
    for ch in chunks:
        chunk_items.append(html.Div([
            html.Span(ch.get("section_title") or "", style={
                "color": C["text_dim"], "fontSize": "10px", "fontWeight": 600,
                "marginRight": "8px",
            }),
            html.Div(
                f'"{(ch.get("text") or "")[:300]}"',
                style={"color": C["text_secondary"], "fontSize": "12px",
                       "fontStyle": "italic", "marginTop": "4px"},
            ),
        ], style={"marginBottom": "8px", "borderLeft": f"2px solid {C['border']}",
                  "paddingLeft": "8px"}))

    return html.Div([
        # Title + DOI header
        html.Div([
            html.Div(title, style={
                "fontSize": "15px", "fontWeight": 700,
                "color": C["text_primary"], "marginBottom": "10px",
            }),
            html.Div([
                _row("DOI", doi_el),
                _row("Year", year, C["text_dim"]),
                _row("Journal", journ or "—"),
                _row("Authors", ", ".join(auth[:5]) or "—"),
            ]),
        ], style={"marginBottom": "16px"}),

        # Abstract
        html.Div([
            html.Div("ABSTRACT", style={
                "color": C["text_dim"], "fontSize": "10px",
                "fontWeight": 700, "letterSpacing": "0.08em", "marginBottom": "6px",
            }),
            html.Div(ab or "Abstract not available.", style={
                "color": C["text_secondary"], "fontSize": "12px",
                "lineHeight": "1.6", "fontStyle": "italic" if not ab else "normal",
            }),
        ], style={"marginBottom": "16px"}),

        # Metrics
        html.Div([
            html.Div("NUMERIC FACTS", style={
                "color": C["text_dim"], "fontSize": "10px",
                "fontWeight": 700, "letterSpacing": "0.08em", "marginBottom": "4px",
            }),
            met_table,
        ], style={"marginBottom": "16px"}),

        # Semantic chunks
        html.Div([
            html.Div("EVIDENCE PASSAGES", style={
                "color": C["text_dim"], "fontSize": "10px",
                "fontWeight": 700, "letterSpacing": "0.08em", "marginBottom": "8px",
            }),
            html.Div(chunk_items) if chunk_items else html.Div(
                "No passages indexed.", style={"color": C["text_dim"], "fontSize": "11px"}
            ),
        ]),
    ])


@callback(
    Output("research-paper-detail-content", "children"),
    Output("research-paper-detail-panel",   "style"),
    Input("research-selected-paper-id",     "data"),
    prevent_initial_call=True,
)
def show_paper_detail(paper_id: str):
    if not paper_id:
        return no_update, {"display": "none"}
    try:
        detail  = rqs.get_paper_detail(paper_id)
        panel   = _paper_detail_panel(detail)
        return panel, {"display": "block", "padding": "14px 28px 0"}
    except Exception as exc:
        err = html.Div(f"Error loading paper detail: {exc}",
                       style={"color": COLORS["text_dim"], "fontSize": "12px"})
        return err, {"display": "block", "padding": "14px 28px 0"}


# ─────────────────────────────────────────────────────────────────────────────
# Notebooks page — card click → iframe + active highlight
# ─────────────────────────────────────────────────────────────────────────────

from src.dashboard_dash.pages.notebooks import _NOTEBOOKS, _card as _nb_card  # noqa: E402

@callback(
    Output("nb-iframe",    "src"),
    Output("nb-card-list", "children"),
    Input({"type": "nb-card", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nb_switch(n_clicks_list):
    from dash import ctx
    if not ctx.triggered_id:
        raise PreventUpdate
    slug = ctx.triggered_id["index"]
    cards = [
        _nb_card(nb, nb["slug"] == slug)
        for nb in _NOTEBOOKS
    ]
    return f"/assets/notebooks/{slug}.html", cards
