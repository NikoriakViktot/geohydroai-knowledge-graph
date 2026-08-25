"""
_app_instance.py  —  Shared Dash app instance (avoids circular imports)

Both app.py and callbacks.py import from here.
"""
import dash
from pathlib import Path
from src.dashboard_dash.layout import build_layout

_ROOT = Path(__file__).parents[2]  # project root

app = dash.Dash(
    __name__,
    title="GeoHydroAI · Research Analytics",
    update_title=None,
    suppress_callback_exceptions=True,
    assets_folder=str(_ROOT / "assets"),
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
        {"name": "theme-color", "content": "#070c14"},
    ],
)

server = app.server
app.layout = build_layout()
