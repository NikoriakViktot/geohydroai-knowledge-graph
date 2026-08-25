"""
filters.py  —  Global sidebar filter panel
"""
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import FILTER_LABEL, GLASS_CARD


DEFAULT_FILTER_STATE = {
    "year_min":    1995,
    "year_max":    2025,
    "countries":   [],
    "study_types": [],
    "min_citations": 0,
    "has_doi":     None,
    "has_openalex": None,
}


def _label(text: str) -> html.P:
    return html.P(text, style={**FILTER_LABEL, "marginBottom": "6px", "marginTop": "14px"})


def filter_panel(options: dict) -> html.Div:
    """Build the full sidebar filter panel. options from data_loader.get_filter_options()."""

    country_opts = options.get("countries", [])
    study_opts   = options.get("study_types", [])

    return html.Div([
        html.Div([
            html.Div("⊡ Filters", style={
                "color": COLORS["accent_cyan"],
                "fontSize": "11px",
                "fontWeight": 700,
                "letterSpacing": "0.1em",
                "textTransform": "uppercase",
                "marginBottom": "16px",
                "paddingBottom": "10px",
                "borderBottom": f"1px solid {COLORS['border']}",
            }),

            # Year range
            _label("Publication Year"),
            dmc.RangeSlider(
                id="filter-year",
                min=1995, max=2025,
                value=[1995, 2025],
                marks=[
                    {"value": 2000, "label": "2000"},
                    {"value": 2010, "label": "2010"},
                    {"value": 2020, "label": "2020"},
                ],
                minRange=1,
                color="cyan",
                size="sm",
                styles={
                    "track":  {"backgroundColor": COLORS["border"]},
                    "label":  {"backgroundColor": COLORS["bg_card"]},
                },
            ),
            html.Div(id="filter-year-display", style={
                "color": COLORS["text_secondary"], "fontSize": "11px",
                "textAlign": "center", "marginTop": "6px",
            }),

            # Country filter
            _label("Country"),
            dmc.MultiSelect(
                id="filter-country",
                data=country_opts,
                placeholder="All countries",
                searchable=True,
                clearable=True,
                maxDropdownHeight=200,
                styles={
                    "input":   {"backgroundColor": COLORS["bg_surface"], "borderColor": COLORS["border"], "color": COLORS["text_primary"], "fontSize": "12px"},
                    "dropdown": {"backgroundColor": COLORS["bg_card"], "borderColor": COLORS["border"]},
                    "option":  {"color": COLORS["text_primary"], "fontSize": "12px"},
                },
            ),

            # Study type
            _label("Study Type"),
            dmc.MultiSelect(
                id="filter-study-type",
                data=study_opts,
                placeholder="All types",
                searchable=True,
                clearable=True,
                styles={
                    "input":   {"backgroundColor": COLORS["bg_surface"], "borderColor": COLORS["border"], "color": COLORS["text_primary"], "fontSize": "12px"},
                    "dropdown": {"backgroundColor": COLORS["bg_card"], "borderColor": COLORS["border"]},
                    "option":  {"color": COLORS["text_primary"], "fontSize": "12px"},
                },
            ),

            # Min citations
            _label("Min Citations"),
            dmc.NumberInput(
                id="filter-min-citations",
                value=0,
                min=0,
                max=10000,
                step=10,
                styles={
                    "input": {"backgroundColor": COLORS["bg_surface"], "borderColor": COLORS["border"], "color": COLORS["text_primary"], "fontSize": "12px"},
                },
            ),

            # Toggles
            _label("Requirements"),
            dmc.Checkbox(
                id="filter-has-doi",
                label="Has DOI",
                color="cyan",
                styles={"label": {"color": COLORS["text_secondary"], "fontSize": "12px"}},
            ),
            dmc.Checkbox(
                id="filter-has-openalex",
                label="Has OpenAlex",
                color="cyan",
                styles={"label": {"color": COLORS["text_secondary"], "fontSize": "12px"}},
                style={"marginTop": "8px"},
            ),

            # Apply button
            html.Div(
                dmc.Button(
                    "Apply Filters",
                    id="filter-apply",
                    color="cyan",
                    variant="light",
                    fullWidth=True,
                    size="sm",
                    style={"marginTop": "20px"},
                ),
            ),

            # Reset
            html.Div(
                dmc.Anchor(
                    "Reset to defaults",
                    id="filter-reset",
                    href="#",
                    style={"fontSize": "11px", "color": COLORS["text_dim"],
                           "textAlign": "center", "display": "block", "marginTop": "8px"},
                ),
            ),
        ], style={
            "padding": "16px",
        }),
    ], style={
        "background": COLORS["bg_sidebar"],
        "height":     "100%",
        "borderRight": f"1px solid {COLORS['border']}",
        "overflowY":  "auto",
    })
