"""
theme.py  —  GeoHydroAI dashboard design tokens
"""

# ── Palette ───────────────────────────────────────────────────────────────────
COLORS = {
    "bg_base":       "#070c14",
    "bg_surface":    "#0d1520",
    "bg_card":       "#111c2d",
    "bg_card_hover": "#16243a",
    "bg_sidebar":    "#090e1a",
    "accent_cyan":   "#00d4ff",
    "accent_teal":   "#0891b2",
    "accent_blue":   "#3b82f6",
    "accent_green":  "#10b981",
    "accent_amber":  "#f59e0b",
    "accent_red":    "#ef4444",
    "text_primary":  "#e2e8f0",
    "text_secondary":"#64748b",
    "text_dim":      "#334155",
    "border":        "#1e293b",
    "border_light":  "#253548",
    "glass_bg":      "rgba(13,21,32,0.85)",
    "glass_border":  "rgba(0,212,255,0.12)",
}

# ── Plotly chart template ──────────────────────────────────────────────────────
CHART_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, system-ui, sans-serif", color=COLORS["text_primary"], size=12),
    margin=dict(l=12, r=12, t=36, b=12),
    colorway=[
        COLORS["accent_cyan"], COLORS["accent_teal"], COLORS["accent_blue"],
        COLORS["accent_green"], COLORS["accent_amber"], "#8b5cf6", "#ec4899",
        "#06b6d4", "#14b8a6", "#6366f1",
    ],
    xaxis=dict(
        gridcolor=COLORS["border"],
        linecolor=COLORS["border"],
        tickfont=dict(color=COLORS["text_secondary"]),
        title_font=dict(color=COLORS["text_secondary"]),
        zeroline=False,
    ),
    yaxis=dict(
        gridcolor=COLORS["border"],
        linecolor=COLORS["border"],
        tickfont=dict(color=COLORS["text_secondary"]),
        title_font=dict(color=COLORS["text_secondary"]),
        zeroline=False,
    ),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        bordercolor=COLORS["border"],
        font=dict(color=COLORS["text_secondary"]),
    ),
    hoverlabel=dict(
        bgcolor=COLORS["bg_card"],
        bordercolor=COLORS["accent_cyan"],
        font=dict(color=COLORS["text_primary"]),
    ),
)

# ── DMC Mantine theme ─────────────────────────────────────────────────────────
MANTINE_THEME = {
    "primaryColor": "cyan",
    "defaultRadius": "md",
    "fontFamily": "Inter, system-ui, sans-serif",
    "headings": {"fontFamily": "Inter, system-ui, sans-serif"},
    "colors": {
        "dark": [
            "#C1C2C5", "#A6A7AB", "#909296", "#5c5f66",
            "#373A40", "#2C2E33", "#25262b", "#1A1B1E",
            "#141517", "#101113",
        ],
    },
    "components": {
        "Paper": {"defaultProps": {"radius": "md"}},
        "Card":  {"defaultProps": {"radius": "md"}},
    },
}

# ── DeckGL map style ───────────────────────────────────────────────────────────
MAPBOX_STYLE = "mapbox://styles/mapbox/dark-v11"
MAP_STYLE    = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"

# ── Country centroids (ISO-alpha name → [lon, lat]) ────────────────────────────
COUNTRY_CENTROIDS: dict[str, list[float]] = {
    "USA": [-95.7, 37.1], "India": [78.9, 20.6], "China": [104.2, 35.9],
    "Ukraine": [31.2, 48.4], "Germany": [10.5, 51.2], "Italy": [12.6, 41.9],
    "UK": [-3.4, 55.4], "France": [2.2, 46.2], "Indonesia": [113.9, -0.8],
    "Iran": [53.7, 32.4], "Australia": [133.8, -25.3], "Brazil": [-51.9, -14.2],
    "Canada": [-96.8, 56.1], "Spain": [-3.7, 40.2], "Pakistan": [69.3, 30.4],
    "Netherlands": [5.3, 52.1], "Bangladesh": [90.4, 23.7], "Turkey": [35.2, 39.0],
    "Japan": [138.3, 36.2], "Ethiopia": [40.5, 9.1], "Nigeria": [8.7, 9.1],
    "Mexico": [-102.6, 23.6], "South Korea": [127.8, 36.5], "Sweden": [17.0, 62.0],
    "Switzerland": [8.2, 46.8], "Poland": [19.1, 51.9], "Belgium": [4.5, 50.5],
    "Portugal": [-8.2, 39.4], "Denmark": [9.5, 56.3], "Norway": [10.2, 60.5],
    "Finland": [25.7, 61.9], "Austria": [14.6, 47.5], "Czech Republic": [15.5, 49.8],
    "Romania": [24.9, 45.9], "Greece": [21.8, 39.1], "Hungary": [19.5, 47.2],
    "Slovakia": [19.7, 48.7], "Serbia": [21.0, 44.0], "Croatia": [15.2, 45.1],
    "Argentina": [-63.6, -38.4], "Colombia": [-74.3, 4.6], "Peru": [-75.0, -9.2],
    "Chile": [-71.5, -35.7], "Venezuela": [-66.6, 6.4], "Bolivia": [-64.9, -16.3],
    "Ecuador": [-78.1, -1.8], "Thailand": [100.9, 15.9], "Vietnam": [108.3, 14.1],
    "Malaysia": [109.7, 4.2], "Philippines": [121.8, 12.9], "Myanmar": [95.9, 17.1],
    "Sri Lanka": [80.7, 7.9], "Nepal": [84.1, 28.4], "Morocco": [-7.1, 31.8],
    "Egypt": [30.8, 26.8], "South Africa": [25.1, -29.0], "Kenya": [37.9, -0.0],
    "Tanzania": [34.9, -6.4], "Uganda": [32.3, 1.4], "Ghana": [-1.0, 7.9],
    "Algeria": [1.7, 28.0], "Tunisia": [9.6, 34.0], "Cameroon": [12.4, 5.7],
    "Sudan": [30.2, 12.9], "Saudi Arabia": [44.7, 23.9], "Iraq": [43.7, 33.2],
    "Jordan": [37.0, 30.6], "Israel": [34.9, 31.5], "Lebanon": [35.9, 33.9],
    "Afghanistan": [67.7, 33.9], "Kazakhstan": [66.9, 48.0], "Uzbekistan": [63.9, 41.4],
    "New Zealand": [174.9, -40.9], "Russia": [105.3, 61.5],
    "Serbia": [21.0, 44.0], "Bosnia": [17.7, 44.2], "Slovenia": [14.8, 46.1],
    "Latvia": [24.6, 56.9], "Lithuania": [23.9, 55.2], "Estonia": [25.0, 58.6],
    "Belarus": [27.9, 53.7], "Moldova": [28.4, 47.4], "Georgia": [43.4, 42.3],
    "Armenia": [45.0, 40.1], "Azerbaijan": [47.6, 40.1],
}

# ── Colour scales for heatmaps ─────────────────────────────────────────────────
CYAN_SCALE   = [[0, "#0a0e17"], [0.5, "#0891b2"], [1, "#00d4ff"]]
GREEN_SCALE  = [[0, "#0a0e17"], [0.5, "#059669"], [1, "#10b981"]]
AMBER_SCALE  = [[0, "#0a0e17"], [0.5, "#d97706"], [1, "#f59e0b"]]
