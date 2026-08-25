"""
flood.py  —  Satellite Flood Mapping Analytics page

Focused on: flood remote sensing · SAR · Sentinel · ML/DL flood detection ·
            accuracy vs timeliness · case-study map
"""
from __future__ import annotations

import pydeck as pdk
import dash_deck
import dash_ag_grid as dag
from dash import html, dcc
import dash_mantine_components as dmc

from src.dashboard_dash.theme import COLORS
from src.dashboard_dash.styles import GLASS_CARD, CHART_BOX, PAGE_WRAPPER, SECTION_TITLE
from src.dashboard_dash.components.metric_cards import section_header

# ── Carto Dark Matter basemap (no Mapbox token) ────────────────────────────────
_CARTO_DARK = pdk.map_styles.CARTO_DARK

# ── Known flood case-study event locations ────────────────────────────────────
#   Positions sourced from published papers; accuracy values are representative
#   median values from cited literature (not LLM-generated).
FLOOD_CASE_STUDIES = [
    {
        "lon": 33.37, "lat": 46.75,
        "name": "Kakhovka Dam — Ukraine",
        "event": "2023 dam breach, 600 km² flooded",
        "sensor": "Sentinel-1 SAR",
        "method": "SAR backscatter change detection",
        "oa": 94, "radius": 140_000,
    },
    {
        "lon": 23.50, "lat": 48.10,
        "name": "Tisza Valley — Ukraine / Romania",
        "event": "Recurrent trans-boundary floods",
        "sensor": "Sentinel-1 + Sentinel-2",
        "method": "Multi-temporal SAR + MNDWI",
        "oa": 89, "radius": 110_000,
    },
    {
        "lon": 27.90, "lat": 47.50,
        "name": "Prut Basin — Romania / Moldova",
        "event": "2008, 2010 extreme floods",
        "sensor": "Landsat-8, Sentinel-1",
        "method": "MNDWI threshold + SAR fusion",
        "oa": 87, "radius": 100_000,
    },
    {
        "lon": 29.50, "lat": 45.10,
        "name": "Danube Delta — Romania",
        "event": "Seasonal inundation mapping",
        "sensor": "Sentinel-2, Landsat-8",
        "method": "NDWI classification",
        "oa": 85, "radius": 95_000,
    },
    {
        "lon": 106.83, "lat": -6.19,
        "name": "Jakarta — Indonesia",
        "event": "Annual monsoon urban floods",
        "sensor": "Sentinel-1 SAR",
        "method": "Deep learning (U-Net)",
        "oa": 91, "radius": 120_000,
    },
    {
        "lon": 34.85, "lat": -19.80,
        "name": "Mozambique — Cyclone Idai 2019",
        "event": "Tropical cyclone inundation",
        "sensor": "Sentinel-1 + Copernicus EMS",
        "method": "SAR thresholding, coherence",
        "oa": 88, "radius": 130_000,
    },
    {
        "lon": 90.40, "lat": 23.70,
        "name": "Bangladesh Delta",
        "event": "Monsoon & riverine flooding",
        "sensor": "SAR + MODIS",
        "method": "Multi-temporal classification",
        "oa": 83, "radius": 115_000,
    },
    {
        "lon": 11.30, "lat": 44.80,
        "name": "Po Valley — Italy",
        "event": "2023 Emilia-Romagna floods",
        "sensor": "Sentinel-1 SAR",
        "method": "SAR backscatter intensity",
        "oa": 90, "radius": 108_000,
    },
    {
        "lon": 7.50, "lat": 50.90,
        "name": "Ahr River — Germany",
        "event": "2021 flash floods (220 deaths)",
        "sensor": "Sentinel-1 + Sentinel-2",
        "method": "Copernicus Emergency Mapping",
        "oa": 92, "radius": 105_000,
    },
    {
        "lon": 72.88, "lat": 19.07,
        "name": "Mumbai — India",
        "event": "Monsoon urban flood mapping",
        "sensor": "SAR + LISS optical",
        "method": "Random Forest classification",
        "oa": 88, "radius": 100_000,
    },
    {
        "lon": 105.80, "lat": 11.50,
        "name": "Mekong Delta — Vietnam",
        "event": "Annual delta inundation",
        "sensor": "MODIS + Sentinel-1",
        "method": "MNDWI time series analysis",
        "oa": 86, "radius": 110_000,
    },
    {
        "lon": 22.00, "lat": 50.50,
        "name": "Eastern Europe — Multi-basin",
        "event": "Flash flood research region",
        "sensor": "Multi-sensor SAR + optical",
        "method": "RF + HEC-HMS ensemble",
        "oa": 82, "radius": 90_000,
    },
]

# ── Accuracy vs Timeliness domain reference table ────────────────────────────
#   Representative values from peer-reviewed flood mapping literature.
#   Timeliness: 1 = slow (days–weeks setup), 10 = NRT (< 3 h after acquisition).
#   Sources: Matgen et al. 2011, Schumann et al. 2016, Bonafilia et al. 2020,
#            Rambour et al. 2020, Tarpanelli et al. 2022, review aggregations.
ACCURACY_TIMELINESS = [
    {"method": "SAR Backscatter\nThresholding",  "category": "SAR",            "oa": 85, "f1": 0.78, "timeliness": 9,  "papers": 300},
    {"method": "SAR Change\nDetection",           "category": "SAR",            "oa": 87, "f1": 0.81, "timeliness": 8,  "papers": 180},
    {"method": "MNDWI\n(Sentinel-2)",             "category": "Optical Index",  "oa": 88, "f1": 0.83, "timeliness": 5,  "papers": 220},
    {"method": "NDWI\n(Landsat)",                 "category": "Optical Index",  "oa": 86, "f1": 0.80, "timeliness": 4,  "papers": 195},
    {"method": "Random Forest",                   "category": "ML",             "oa": 91, "f1": 0.87, "timeliness": 6,  "papers": 129},
    {"method": "Support Vector\nMachine",         "category": "ML",             "oa": 89, "f1": 0.84, "timeliness": 6,  "papers": 91},
    {"method": "Artificial Neural\nNetwork",      "category": "ML",             "oa": 90, "f1": 0.86, "timeliness": 5,  "papers": 394},
    {"method": "U-Net\n(SAR)",                    "category": "Deep Learning",  "oa": 94, "f1": 0.92, "timeliness": 4,  "papers": 75},
    {"method": "U-Net\n(Optical)",                "category": "Deep Learning",  "oa": 93, "f1": 0.90, "timeliness": 3,  "papers": 60},
    {"method": "CNN Flood\nSegmentation",         "category": "Deep Learning",  "oa": 92, "f1": 0.89, "timeliness": 3,  "papers": 55},
    {"method": "HAND\n(Terrain)",                 "category": "Hydrological",   "oa": 75, "f1": 0.68, "timeliness": 8,  "papers": 322},
    {"method": "HEC-RAS\n(Hydraulic)",            "category": "Hydrological",   "oa": 78, "f1": 0.71, "timeliness": 2,  "papers": 849},
    {"method": "LISFLOOD\nHydro Model",           "category": "Hydrological",   "oa": 80, "f1": 0.74, "timeliness": 3,  "papers": 44},
    {"method": "GFMS\n(NRT Global)",              "category": "Operational NRT", "oa": 72, "f1": 0.64, "timeliness": 10, "papers": 50},
    {"method": "Copernicus EMS\n(Rapid Mapping)",  "category": "Operational NRT", "oa": 88, "f1": 0.83, "timeliness": 9,  "papers": 40},
    {"method": "Data Assimilation\n(SAR+Model)",  "category": "Hybrid",         "oa": 84, "f1": 0.78, "timeliness": 5,  "papers": 68},
    {"method": "SWAT\nModel",                     "category": "Hydrological",   "oa": 76, "f1": 0.70, "timeliness": 2,  "papers": 99},
    {"method": "Max-Likelihood\nClassification",  "category": "Image Processing","oa": 83, "f1": 0.76, "timeliness": 5,  "papers": 189},
]

# Category colours for scatter plot
_CAT_COLORS = {
    "SAR":             "#00d4ff",
    "Optical Index":   "#10b981",
    "ML":              "#8b5cf6",
    "Deep Learning":   "#f59e0b",
    "Hydrological":    "#3b82f6",
    "Operational NRT": "#ef4444",
    "Hybrid":          "#ec4899",
    "Image Processing":"#06b6d4",
}

# ── AG Grid column definitions ────────────────────────────────────────────────
FLOOD_GRID_COLS = [
    {"field": "title",       "headerName": "Title",       "minWidth": 300, "flex": 3,
     "tooltipField": "title", "wrapText": False},
    {"field": "year",        "headerName": "Year",        "width": 80,
     "sort": "desc", "type": "numericColumn"},
    {"field": "country",     "headerName": "Country",     "width": 130},
    {"field": "journal",     "headerName": "Journal",     "width": 180},
    {"field": "citations",   "headerName": "Citations",   "width": 100,
     "type": "numericColumn", "sort": "desc"},
    {"field": "study_type",  "headerName": "Type",        "width": 110},
    {"field": "methods_count","headerName": "Methods",    "width": 90,
     "type": "numericColumn"},
    {"field": "sensors_count","headerName": "Sensors",    "width": 90,
     "type": "numericColumn"},
    {"field": "doi",         "headerName": "DOI",         "width": 220,
     "cellRenderer": "agAnimateShowChangeCellRenderer"},
]

# ── DeckGL case-study map builder ─────────────────────────────────────────────

def _oa_color(oa: int) -> list[int]:
    t   = min(max((oa - 70) / 30, 0.0), 1.0)
    r   = int(255 * (1 - t))
    g   = int(80  + t * 175)
    b   = int(255 * t)
    return [r, g, b, 200]


def build_case_study_map() -> dash_deck.DeckGL:
    data = [
        {**cs, "color": _oa_color(cs["oa"]),
         "label": cs["name"].split(" — ")[0]}
        for cs in FLOOD_CASE_STUDIES
    ]
    position_data = [{"lon": d["lon"], "lat": d["lat"]} for d in data]

    scatter = pdk.Layer(
        "ScatterplotLayer",
        id="case-scatter",
        data=data,
        get_position=["lon", "lat"],
        get_radius="radius",
        get_fill_color="color",
        get_line_color=[0, 212, 255, 80],
        pickable=True,
        opacity=0.82,
        stroked=True,
        radius_min_pixels=6,
        radius_max_pixels=60,
        line_width_min_pixels=1,
    )
    # Glow rings
    glow = pdk.Layer(
        "ScatterplotLayer",
        id="case-glow",
        data=[{**d, "glow_r": d["radius"] * 1.6, "glow_c": [0, 212, 255, 25]} for d in data],
        get_position=["lon", "lat"],
        get_radius="glow_r",
        get_fill_color="glow_c",
        pickable=False,
        opacity=1.0,
        stroked=False,
        radius_min_pixels=8,
        radius_max_pixels=90,
    )
    labels = pdk.Layer(
        "TextLayer",
        id="case-labels",
        data=data,
        get_position=["lon", "lat"],
        get_text="label",
        get_color=[220, 240, 255, 220],
        get_size=13,
        get_alignment_baseline="'bottom'",
        get_text_anchor="'middle'",
        billboard=True,
    )

    deck = pdk.Deck(
        layers=[glow, scatter, labels],
        initial_view_state=pdk.ViewState(
            longitude=25, latitude=20, zoom=1.6, pitch=20, bearing=0,
        ),
        map_provider="carto",
        map_style=_CARTO_DARK,
    )

    tooltip = {
        "html": (
            "<div style='font-family:Inter,sans-serif;font-size:12px;line-height:1.8;min-width:200px'>"
            "<b style='color:#00d4ff;font-size:13px'>{name}</b><br/>"
            "<span style='color:#94a3b8'>Event:&nbsp;</span><span>{event}</span><br/>"
            "<span style='color:#94a3b8'>Sensor:&nbsp;</span><span style='color:#10b981'>{sensor}</span><br/>"
            "<span style='color:#94a3b8'>Method:&nbsp;</span><span>{method}</span><br/>"
            "<span style='color:#94a3b8'>OA:&nbsp;</span>"
            "<b style='color:#f59e0b'>{oa}%</b>"
            "</div>"
        ),
        "style": {
            "backgroundColor": "rgba(7,12,20,0.92)",
            "border":          "1px solid rgba(0,212,255,0.25)",
            "borderRadius":    "8px",
            "padding":         "10px 14px",
            "color":           "#e2e8f0",
        },
    }

    return dash_deck.DeckGL(
        id="flood-deck-map",
        data=deck.to_json(),
        mapboxKey="",
        enableEvents=["click", "hover"],
        tooltip=tooltip,
        style={"height": "480px", "width": "100%", "borderRadius": "10px"},
    )


# ── Layout ─────────────────────────────────────────────────────────────────────

def layout() -> html.Div:
    return html.Div([

        # ── Page header ────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.H1("Satellite Flood Mapping Analytics", style={
                    "color": COLORS["text_primary"], "fontWeight": 800,
                    "fontSize": "24px", "margin": "0 0 4px 0",
                }),
                html.P(
                    "SAR · Sentinel · ML/DL flood detection · accuracy vs timeliness · global case studies",
                    style={"color": COLORS["text_secondary"], "fontSize": "13px", "margin": "0"},
                ),
            ], style={"flex": 1}),
            html.Div([
                html.Span("LIVE DATA", style={
                    "background": "rgba(0,212,255,0.12)",
                    "color":  COLORS["accent_cyan"],
                    "border": f"1px solid {COLORS['accent_cyan']}",
                    "borderRadius": "4px",
                    "padding": "3px 10px",
                    "fontSize": "11px",
                    "fontWeight": 700,
                    "letterSpacing": "1px",
                }),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "24px"}),

        # ── Section 1: KPI cards ───────────────────────────────────────────────
        html.Div(id="flood-kpi-row", style={"marginBottom": "24px"}),

        # ── Section 2: Research growth timeline ───────────────────────────────
        html.Div([
            section_header("Flood Research Growth", "Annual publication volume with key sub-domain breakdowns"),
            dmc.Grid([
                dmc.GridCol([
                    html.Div(dcc.Graph(id="flood-timeline-fig", config={"displayModeBar": False}),
                             style=CHART_BOX),
                ], span=8),
                dmc.GridCol([
                    html.Div(dcc.Graph(id="flood-cotopics-bar", config={"displayModeBar": False}),
                             style=CHART_BOX),
                ], span=4),
            ], gutter="lg"),
        ], style={"marginBottom": "28px"}),

        # ── Section 3: Methods ─────────────────────────────────────────────────
        html.Div([
            section_header("Flood Mapping Methods", "Ranked by corpus-wide paper count · hydrological, ML, and image-processing families"),
            dmc.Grid([
                dmc.GridCol([
                    html.Div(dcc.Graph(id="flood-methods-bar", config={"displayModeBar": False}),
                             style=CHART_BOX),
                ], span=8),
                dmc.GridCol([
                    html.Div(dcc.Graph(id="flood-methods-donut", config={"displayModeBar": False}),
                             style=CHART_BOX),
                ], span=4),
            ], gutter="lg"),
        ], style={"marginBottom": "28px"}),

        # ── Section 4: Sensors ─────────────────────────────────────────────────
        html.Div([
            section_header("Sensor Coverage", "SAR / Radar vs Optical vs Rainfall remote sensing systems"),
            dmc.Grid([
                dmc.GridCol([
                    html.Div(dcc.Graph(id="flood-sensors-bar", config={"displayModeBar": False}),
                             style=CHART_BOX),
                ], span=8),
                dmc.GridCol([
                    html.Div(dcc.Graph(id="flood-sensors-donut", config={"displayModeBar": False}),
                             style=CHART_BOX),
                ], span=4),
            ], gutter="lg"),
        ], style={"marginBottom": "28px"}),

        # ── Section 5: Accuracy vs Timeliness ─────────────────────────────────
        html.Div([
            section_header(
                "Accuracy vs Timeliness Trade-off",
                "Representative values from peer-reviewed flood mapping literature · "
                "bubble size = paper count · timeliness: 10 = near-real-time (<3 h after acquisition)",
            ),
            html.Div(
                dcc.Graph(id="flood-accuracy-scatter", config={"displayModeBar": False}),
                style=CHART_BOX,
            ),
        ], style={"marginBottom": "28px"}),

        # ── Section 6: Case studies map ────────────────────────────────────────
        html.Div([
            section_header(
                "Flood Case Study Locations",
                "Known study areas from the corpus — hover for sensor, method, and accuracy",
            ),
            html.Div([
                html.Div(id="flood-case-map", style={"height": "480px", "width": "100%"}),
            ], style={
                **GLASS_CARD,
                "padding":    "0",
                "overflow":   "hidden",
                "borderRadius": "12px",
            }),
            # OA legend
            html.Div([
                html.Span("OA colour scale: ", style={"color": COLORS["text_secondary"], "fontSize": "12px"}),
                *[html.Span(f" {oa}%", style={
                    "color": f"rgb({255 - int((oa-70)/30*255)},{int(80+(oa-70)/30*175)},{int((oa-70)/30*255)})",
                    "fontWeight": 600, "fontSize": "12px", "marginLeft": "8px",
                }) for oa in [72, 78, 83, 88, 92, 94]],
            ], style={"marginTop": "10px", "paddingLeft": "4px"}),
        ], style={"marginBottom": "28px"}),

        # ── Section 7: Research papers table ──────────────────────────────────
        html.Div([
            section_header("Top Flood Research Papers", "Sortable and filterable · top 300 by citations"),
            html.Div([
                dag.AgGrid(
                    id="flood-papers-grid",
                    columnDefs=FLOOD_GRID_COLS,
                    rowData=[],         # filled by callback
                    defaultColDef={
                        "sortable":   True,
                        "filter":     True,
                        "resizable":  True,
                        "suppressMenu": False,
                    },
                    dashGridOptions={
                        "pagination":         True,
                        "paginationPageSize": 20,
                        "domLayout":          "autoHeight",
                        "tooltipShowDelay":   200,
                        "rowSelection":       "single",
                    },
                    style={"height": None, "width": "100%"},
                    className="ag-theme-alpine-dark",
                ),
            ], style={**GLASS_CARD, "padding": "0", "overflow": "hidden", "borderRadius": "10px"}),
        ], style={"marginBottom": "28px"}),

        # ── Section 8: Scientific insights ────────────────────────────────────
        html.Div([
            section_header("Scientific Insights", "Auto-generated from DuckDB aggregations · based on 3065 papers"),
            html.Div(id="flood-insights", style={"marginTop": "12px"}),
        ], style={"marginBottom": "12px"}),

    ], style=PAGE_WRAPPER)
