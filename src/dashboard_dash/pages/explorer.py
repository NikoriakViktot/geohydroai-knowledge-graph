"""
explorer.py  —  Interactive paper explorer: search + metadata panel
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER, badge_style
from src.dashboard_dash.components.metric_cards import section_header
from src.dashboard_dash.components.empty_states import no_data


def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Paper Explorer", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P(
                "Search and inspect individual papers · metadata · enrichment · methods · topics",
                style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
            ),
        ], style={"marginBottom": "24px"}),

        # ── Search bar ────────────────────────────────────────────────────────
        html.Div([
            dmc.Grid([
                dmc.GridCol([
                    dcc.Input(
                        id="explorer-search",
                        type="text",
                        placeholder="Search by title, DOI, journal…",
                        debounce=True,
                        style={
                            "width": "100%", "padding": "12px 16px",
                            "background": COLORS["bg_surface"],
                            "border": f"1px solid {COLORS['accent_cyan']}44",
                            "borderRadius": "8px", "color": COLORS["text_primary"],
                            "fontSize": "14px", "outline": "none",
                            "boxShadow": f"0 0 0 1px {COLORS['accent_cyan']}22",
                        },
                    ),
                ], span=9),
                dmc.GridCol([
                    dmc.Select(
                        id="explorer-filter-type",
                        data=[
                            {"label": "All fields",   "value": "all"},
                            {"label": "Title only",   "value": "title"},
                            {"label": "DOI",          "value": "doi"},
                            {"label": "Journal",      "value": "journal"},
                        ],
                        value="all",
                        styles={
                            "input": {"backgroundColor": COLORS["bg_surface"], "borderColor": COLORS["border"], "color": COLORS["text_primary"], "fontSize": "13px"},
                            "dropdown": {"backgroundColor": COLORS["bg_card"]},
                        },
                    ),
                ], span=3),
            ], gutter="md"),
        ], style={**GLASS_CARD, "marginBottom": "20px"}),

        # ── Results + detail panel ────────────────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Results", "click a row to inspect"),
                html.Div(id="explorer-results-table", style={
                    **GLASS_CARD, "padding": "0",
                    "overflowY": "auto", "maxHeight": "600px",
                    "minHeight": "200px",
                }, children=[no_data("Enter a search term to find papers.")]),
                # Hidden store for selected paper_id
                dcc.Store(id="explorer-selected-paper", data=None),
            ], span=5),

            dmc.GridCol([
                html.Div(id="explorer-detail-panel", children=[
                    no_data("Select a paper from the results to inspect."),
                ], style={**GLASS_CARD, "minHeight": "600px"}),
            ], span=7),
        ], gutter="lg"),
    ], style=PAGE_WRAPPER)


def paper_detail_panel(paper: dict, topics_df, authors_df, refs_df) -> html.Div:
    """Build the full detail panel for a selected paper."""
    if not paper:
        return no_data("Paper not found.")

    doi   = paper.get("doi") or "—"
    year  = paper.get("year") or "—"
    title = paper.get("title") or "Untitled"
    cited = f"{int(paper['cited_by_count']):,}" if paper.get("cited_by_count") is not None else "—"
    oa_id = paper.get("openalex_id") or "—"
    journal = paper.get("journal") or "—"
    study   = paper.get("study_type") or "—"
    country = paper.get("primary_country") or "—"

    sections = [
        # Header
        html.Div([
            html.H3(title, style={
                "color": COLORS["text_primary"], "fontWeight": 700,
                "fontSize": "15px", "margin": "0 0 8px 0", "lineHeight": "1.4",
            }),
            html.Div([
                html.Span(year, style={**badge_style("teal"), "marginRight": "6px"}),
                html.Span(study.replace("_", " ").title(), style={**badge_style("cyan"), "marginRight": "6px"}),
                html.Span(f"⭐ {cited}", style={**badge_style("amber")}),
            ], style={"marginBottom": "12px"}),
        ]),
        html.Hr(style={"borderColor": COLORS["border"], "margin": "12px 0"}),

        # Bibliographic metadata
        _info_row("Journal",    journal),
        _info_row("DOI",        doi),
        _info_row("Country",    country),
        _info_row("OpenAlex ID", oa_id[:40] + "…" if len(oa_id) > 40 else oa_id),
        _info_row("Abstract",   "✓" if paper.get("has_abstract") else "✗"),

        html.Hr(style={"borderColor": COLORS["border"], "margin": "16px 0"}),
    ]

    # Authors
    if authors_df is not None and not authors_df.empty:
        sections.append(html.P("Authors", style={
            "color": COLORS["text_secondary"], "fontSize": "11px",
            "fontWeight": 600, "textTransform": "uppercase",
            "letterSpacing": "0.06em", "margin": "0 0 8px 0",
        }))
        author_items = []
        for _, a in authors_df.iterrows():
            pos_label = f" [{str(a.get('author_position', '')).replace('first','①').replace('middle','◦').replace('last','⊕')}]"
            corr = " ✉" if a.get("is_corresponding") else ""
            author_items.append(html.Span(
                f"{a['display_name']}{pos_label}{corr}",
                style={"marginRight": "8px", "marginBottom": "4px", "display": "inline-block",
                       **badge_style("teal")},
            ))
        sections.append(html.Div(author_items, style={"marginBottom": "12px"}))

    # Topics
    if topics_df is not None and not topics_df.empty:
        sections.append(html.Hr(style={"borderColor": COLORS["border"], "margin": "12px 0"}))
        sections.append(html.P("OpenAlex Topics", style={
            "color": COLORS["text_secondary"], "fontSize": "11px",
            "fontWeight": 600, "textTransform": "uppercase",
            "letterSpacing": "0.06em", "margin": "0 0 8px 0",
        }))
        for _, t in topics_df.iterrows():
            pct = int(t["score"] * 100)
            sections.append(html.Div([
                html.Span(t["topic_name"][:50], style={"color": COLORS["text_primary"], "fontSize": "12px", "flex": "1"}),
                html.Span(f"{pct}%", style={"color": COLORS["accent_cyan"], "fontSize": "11px", "fontWeight": 700}),
            ], style={
                "display": "flex", "justifyContent": "space-between",
                "padding": "5px 0", "borderBottom": f"1px solid {COLORS['border']}",
            }))

    # Quick stats
    sections.append(html.Hr(style={"borderColor": COLORS["border"], "margin": "16px 0"}))
    sections.append(html.Div([
        html.Div([
            html.Div(str(paper.get("methods_count", 0)), style={"color": COLORS["accent_cyan"], "fontSize": "22px", "fontWeight": 800}),
            html.Div("Methods", style={"color": COLORS["text_secondary"], "fontSize": "10px", "textTransform": "uppercase"}),
        ], style={"textAlign": "center", "flex": "1"}),
        html.Div([
            html.Div(str(paper.get("sensors_count", 0)), style={"color": COLORS["accent_teal"], "fontSize": "22px", "fontWeight": 800}),
            html.Div("Sensors", style={"color": COLORS["text_secondary"], "fontSize": "10px", "textTransform": "uppercase"}),
        ], style={"textAlign": "center", "flex": "1"}),
        html.Div([
            html.Div(str(paper.get("metrics_count", 0)), style={"color": COLORS["accent_green"], "fontSize": "22px", "fontWeight": 800}),
            html.Div("Metrics", style={"color": COLORS["text_secondary"], "fontSize": "10px", "textTransform": "uppercase"}),
        ], style={"textAlign": "center", "flex": "1"}),
        html.Div([
            html.Div(str(paper.get("references_count", 0)), style={"color": COLORS["accent_amber"], "fontSize": "22px", "fontWeight": 800}),
            html.Div("References", style={"color": COLORS["text_secondary"], "fontSize": "10px", "textTransform": "uppercase"}),
        ], style={"textAlign": "center", "flex": "1"}),
    ], style={"display": "flex", "justifyContent": "space-around"}))

    return html.Div(sections)


def _info_row(label: str, value: str) -> html.Div:
    return html.Div([
        html.Span(label, style={
            "color": COLORS["text_secondary"], "fontSize": "11px",
            "fontWeight": 600, "textTransform": "uppercase",
            "letterSpacing": "0.05em", "minWidth": "90px",
        }),
        html.Span(value, style={
            "color": COLORS["text_primary"], "fontSize": "12px",
            "wordBreak": "break-all",
        }),
    ], style={
        "display": "flex", "gap": "12px",
        "padding": "5px 0", "borderBottom": f"1px solid {COLORS['border']}22",
    })


def search_results_table(df) -> html.Div:
    """Render search results as a clickable table."""
    from src.dashboard_dash.styles import TABLE_STYLE, TABLE_HEADER

    if df is None or df.empty:
        return no_data("No papers found. Try a different search term.")

    rows = []
    for _, r in df.iterrows():
        title = str(r.get("title", ""))[:60] + ("…" if len(str(r.get("title", ""))) > 60 else "")
        cited = f"{int(r['cited_by_count']):,}" if r.get("cited_by_count") is not None else "—"
        oa = "✓" if r.get("has_openalex") else "—"
        rows.append(html.Tr(
            [
                html.Td(str(r.get("year", "—")), style={"padding": "8px 10px", "color": COLORS["text_secondary"], "fontSize": "12px", "whiteSpace": "nowrap"}),
                html.Td(title, style={"padding": "8px 10px", "fontSize": "12px", "maxWidth": "280px"}),
                html.Td(cited, style={"padding": "8px 10px", "color": COLORS["accent_cyan"], "fontWeight": 700, "textAlign": "right", "fontSize": "12px"}),
                html.Td(oa,    style={"padding": "8px 10px", "textAlign": "center", "color": COLORS["accent_green"] if oa == "✓" else COLORS["text_dim"], "fontSize": "12px"}),
            ],
            id={"type": "explorer-row", "paper_id": str(r["paper_id"])},
            style={
                "cursor": "pointer",
                "borderBottom": f"1px solid {COLORS['border']}",
                "transition": "background 0.15s",
            },
            n_clicks=0,
        ))

    return html.Table([
        html.Thead(html.Tr([
            html.Th("Year",      style={**TABLE_HEADER}),
            html.Th("Title",     style={**TABLE_HEADER}),
            html.Th("Citations", style={**TABLE_HEADER, "textAlign": "right"}),
            html.Th("OA",        style={**TABLE_HEADER, "textAlign": "center"}),
        ])),
        html.Tbody(rows),
    ], style=TABLE_STYLE)
