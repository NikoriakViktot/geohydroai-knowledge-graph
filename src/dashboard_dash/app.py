"""
app.py  —  GeoHydroAI Dashboard entry point

Usage:
    cd /home/niko/projects/knoweledg_graf
    source .venv/bin/activate
    python -m src.dashboard_dash.app

Then open: http://localhost:8050
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# Import shared app instance FIRST (creates Dash app + layout)
from src.dashboard_dash._app_instance import app, server  # noqa: F401

# Register all callbacks (binds to app via app.callback alias in callbacks.py)
import src.dashboard_dash.callbacks  # noqa: F401

# Flask-Caching
from flask_caching import Cache
cache = Cache(server, config={
    "CACHE_TYPE":            "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_THRESHOLD":       500,
})

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("geohydro.dashboard")

# ── CSS injection ──────────────────────────────────────────────────────────────
app.index_string = """
<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
        *, *::before, *::after { box-sizing: border-box; }
        html, body {
            margin: 0; padding: 0;
            background: #070c14; color: #e2e8f0;
            font-family: 'Inter', system-ui, sans-serif;
            -webkit-font-smoothing: antialiased;
        }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
        a { color: inherit; }
        .modebar { display: none !important; }
        input:focus { outline: none; }
        input[type="text"] { box-sizing: border-box; }
    </style>
</head>
<body>
    {%app_entry%}
    <footer>
        {%config%}
        {%scripts%}
        {%renderer%}
    </footer>
</body>
</html>
"""


def _verify_data() -> bool:
    from src.dashboard_dash.duckdb_manager import verify_views, scalar
    status = verify_views()
    ok = all(status.values())
    for view, accessible in status.items():
        state = "✓" if accessible else "✗ MISSING"
        log.info("  View %-26s %s", view, state)
    if ok:
        n = scalar("SELECT count(*) FROM papers")
        log.info("  Papers in corpus: %s", n)
    return ok


if __name__ == "__main__":
    log.info("=" * 56)
    log.info("GeoHydroAI Research Analytics Dashboard")
    log.info("=" * 56)
    log.info("Verifying DuckDB views…")

    if not _verify_data():
        log.error("Some parquet files are missing. Run:")
        log.error("  python -m src.analytics.parquet_builder")
        sys.exit(1)

    log.info("Starting dashboard on http://localhost:8050")
    app.run(debug=True, host="0.0.0.0", port=8050, dev_tools_hot_reload=True)
