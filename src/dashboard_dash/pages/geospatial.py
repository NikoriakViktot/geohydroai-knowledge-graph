"""
geospatial.py  —  DeckGL geospatial research distribution page

Basemap: Carto Dark Matter (no Mapbox token required)
Layers:  ScatterplotLayer · HexagonLayer · Institution points · Choropleth
"""
from dash import html, dcc
import dash_mantine_components as dmc
import dash_deck
import pydeck as pdk

from src.dashboard_dash.theme import COLORS, MANTINE_THEME
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER
from src.dashboard_dash.components.metric_cards import section_header

# ── View defaults ──────────────────────────────────────────────────────────────

_INITIAL_VIEW = {
    "longitude": 0,
    "latitude":  20,
    "zoom":      1.2,
    "pitch":     15,
    "bearing":   0,
}

# Carto Dark Matter — requires no Mapbox token
_CARTO_DARK = pdk.map_styles.CARTO_DARK  # "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"

# ── Colour ranges for HexagonLayer ────────────────────────────────────────────

_HEX_COLOR_RANGE = [
    [2,   20,  45,  170],
    [0,   50,  90,  185],
    [0,   90,  140, 200],
    [0,   140, 185, 215],
    [0,   185, 230, 235],
    [0,   212, 255, 252],
]

# ── Tooltip HTML ───────────────────────────────────────────────────────────────

_SCATTER_TOOLTIP = {
    "html": (
        "<div style='"
        "font-family:Inter,sans-serif;"
        "font-size:12px;"
        "line-height:1.7;"
        "min-width:160px;"
        "'>"
        "<b style='color:#00d4ff;font-size:13px'>{country}</b><br/>"
        "<span style='color:#94a3b8'>Papers:&nbsp;</span>"
        "<b style='color:#e2e8f0'>{papers}</b><br/>"
        "<span style='color:#94a3b8'>Avg&nbsp;citations:&nbsp;</span>"
        "<b style='color:#e2e8f0'>{avg_citations}</b>"
        "</div>"
    ),
    "style": {
        "backgroundColor": "rgba(7,12,20,0.92)",
        "border":          "1px solid rgba(0,212,255,0.25)",
        "borderRadius":    "8px",
        "padding":         "8px 12px",
        "color":           "#e2e8f0",
        "backdropFilter":  "blur(8px)",
        "boxShadow":       "0 4px 20px rgba(0,212,255,0.12)",
    },
}

_INST_TOOLTIP = {
    "html": (
        "<div style='"
        "font-family:Inter,sans-serif;"
        "font-size:12px;"
        "line-height:1.7;"
        "min-width:160px;"
        "'>"
        "<b style='color:#10b981;font-size:13px'>{name}</b><br/>"
        "<span style='color:#94a3b8'>Papers:&nbsp;</span>"
        "<b style='color:#e2e8f0'>{paper_count}</b>"
        "</div>"
    ),
    "style": {
        "backgroundColor": "rgba(7,12,20,0.92)",
        "border":          "1px solid rgba(16,185,129,0.25)",
        "borderRadius":    "8px",
        "padding":         "8px 12px",
        "color":           "#e2e8f0",
        "backdropFilter":  "blur(8px)",
    },
}


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    return html.Div([
        html.Div([
            html.H1("Geospatial Analysis", style={
                "color": COLORS["text_primary"], "fontWeight": 800,
                "fontSize": "24px", "margin": "0 0 4px 0",
            }),
            html.P(
                "Global research distribution · institution density · country intensity",
                style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
            ),
        ], style={"marginBottom": "24px"}),

        # ── Layer selector ────────────────────────────────────────────────────
        html.Div([
            dmc.SegmentedControl(
                id="geo-layer-select",
                value="scatter",
                data=[
                    {"label": "Country Scatter",   "value": "scatter"},
                    {"label": "Research Hexbins",   "value": "hex"},
                    {"label": "Institutions",       "value": "institutions"},
                    {"label": "Choropleth",         "value": "choropleth"},
                ],
                color="cyan",
                styles={
                    "root":  {"background": COLORS["bg_surface"],
                              "border":     f"1px solid {COLORS['border']}"},
                    "label": {"color": COLORS["text_secondary"], "fontSize": "13px"},
                },
            ),
        ], style={"marginBottom": "16px"}),

        # ── DeckGL map ─────────────────────────────────────────────────────────
        html.Div([
            html.Div(
                id="deck-map-container",
                style={"height": "520px", "width": "100%"},
            ),
        ], style={
            **GLASS_CARD,
            "padding":    "0",
            "overflow":   "hidden",
            "position":   "relative",
            "marginBottom": "20px",
            "borderRadius": "12px",
        }),

        # ── Country stats + regional bar chart ────────────────────────────────
        dmc.Grid([
            dmc.GridCol([
                section_header("Country Research Intensity"),
                html.Div(id="geo-country-table", style={
                    **GLASS_CARD,
                    "padding":    "0",
                    "overflowX":  "auto",
                    "maxHeight":  "360px",
                    "overflowY":  "auto",
                }),
            ], span=5),
            dmc.GridCol([
                section_header("Regional Distribution"),
                html.Div(
                    dcc.Graph(id="chart-country-bars", config={"displayModeBar": False}),
                    style=CHART_BOX,
                ),
            ], span=7),
        ], gutter="lg"),

    ], style=PAGE_WRAPPER)


# ── DeckGL builder ─────────────────────────────────────────────────────────────

def build_deck_map(
    layer_type:   str,
    scatter_data: list,
    hex_data:     list,
    inst_data:    list,
) -> dash_deck.DeckGL:
    """
    Build a DeckGL component with Carto Dark Matter basemap.
    No Mapbox token required — uses map_provider='carto'.
    """
    layers: list[pdk.Layer] = []

    if layer_type == "scatter":
        # Outer glow ring (rendered first so main circle sits on top)
        if scatter_data:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                id="scatter-glow",
                data=scatter_data,
                get_position="position",
                get_radius="glow_radius",
                get_fill_color="glow_color",
                pickable=False,
                opacity=1.0,
                stroked=False,
                radius_min_pixels=3,
                radius_max_pixels=60,
            ))
        # Main signal dot
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            id="scatter-main",
            data=scatter_data,
            get_position="position",
            get_radius="radius",
            get_fill_color="color",
            get_line_color="line_color",
            pickable=True,
            opacity=0.85,
            stroked=True,
            radius_min_pixels=4,
            radius_max_pixels=80,
            line_width_min_pixels=1,
        ))
        # Country labels for dominant research centres
        label_data = [d for d in scatter_data if d["papers"] >= 25]
        if label_data:
            layers.append(pdk.Layer(
                "TextLayer",
                id="scatter-labels",
                data=label_data,
                get_position="position",
                get_text="country",
                get_color=[210, 235, 255, 210],
                get_size=13,
                get_alignment_baseline="'bottom'",
                get_text_anchor="'middle'",
                billboard=True,
            ))
        tooltip = _SCATTER_TOOLTIP

    elif layer_type == "hex":
        layers.append(pdk.Layer(
            "HexagonLayer",
            id="hex-density",
            data=hex_data,
            get_position=["lon", "lat"],
            radius=200_000,
            auto_highlight=True,
            elevation_scale=80,
            elevation_range=[0, 4_000],
            pickable=True,
            extruded=True,
            coverage=0.85,
            color_range=_HEX_COLOR_RANGE,
            opacity=0.88,
        ))
        tooltip = {"html": "Count: {count}", "style": {"color": "#e2e8f0", "background": "rgba(7,12,20,0.9)"}}

    elif layer_type == "institutions":
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            id="inst-main",
            data=inst_data,
            get_position="position",
            get_radius="radius",
            get_fill_color="color",
            get_line_color="line_color",
            pickable=True,
            opacity=0.82,
            stroked=True,
            radius_min_pixels=3,
            radius_max_pixels=55,
            line_width_min_pixels=1,
        ))
        tooltip = _INST_TOOLTIP

    elif layer_type == "choropleth":
        # Filled country blobs — fallback to scatter with no stroke
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            id="choropleth-fill",
            data=scatter_data,
            get_position="position",
            get_radius="radius",
            get_fill_color="color",
            pickable=True,
            opacity=0.9,
            stroked=False,
            radius_min_pixels=5,
            radius_max_pixels=100,
        ))
        tooltip = _SCATTER_TOOLTIP

    else:
        tooltip = {}

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(**_INITIAL_VIEW),
        map_provider="carto",
        map_style=_CARTO_DARK,
    )

    return dash_deck.DeckGL(
        id="deck-gl-map",
        data=deck.to_json(),
        mapboxKey="",         # not used for Carto tiles; prevents token-check warnings
        enableEvents=["click", "hover"],
        tooltip=tooltip,
        style={"height": "520px", "width": "100%", "borderRadius": "12px"},
    )
