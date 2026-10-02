# ghai-client

Python client for the GeoHydroAI Knowledge API. One module; the only dependency is `httpx`.

```bash
uv pip install "git+ssh://git@github.com/NikoriakViktot/geohydroai-knowledge-graph.git#subdirectory=clients/python"
# or, without a package manager:
curl -s http://127.0.0.1:8090/v1/client.py -o ghai_client.py
```

```python
from ghai_client import GHAI, GHAIError
api = GHAI.from_env()            # GHAI_API_URL=http://127.0.0.1:8090/v1, GHAI_API_KEY=…
api.agent_rules()                # read these before using results in a manuscript
api.papers.resolve(doi="10.1029/2025GL120832")
api.quotes.verify_open_citations(json.load(open("open_citations.json")), project_id="floodstate-eo:paper3")
api.doi.verify([{"bibtex": "@article{Monti_2024, doi={10.24425/agg.2023.146162}, …}"}])
for fact in api.metrics.facts(metric="NSE", min=0.8, method="method.swat"):
    ...
```

Batches above the API's limits (50 quotations, 50 bibliography entries, 500 resolutions)
are split and merged by the client. Cursor-paginated endpoints are iterators.
