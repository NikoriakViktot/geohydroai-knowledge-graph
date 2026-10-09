# Catalog API image: only src/catalog/api.py and the agent guide; the snapshot is mounted read-only.
#   docker compose -f deploy/catalog/docker-compose.yml up -d --build
FROM python:3.12-slim

RUN useradd --system --uid 10001 catalog
WORKDIR /app
COPY deploy/catalog/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY src/__init__.py /app/src/__init__.py
COPY src/catalog/__init__.py src/catalog/api.py /app/src/catalog/
COPY docs/catalog/AGENT_GUIDE.md /app/docs/catalog/AGENT_GUIDE.md

USER catalog
ENV CATALOG_SNAPSHOT_DIR=/data/current PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/health', timeout=4)"
# --root-path: served by geoai-nginx under https://geohydroai.org/catalog/ (prefix stripped by nginx)
CMD ["uvicorn", "src.catalog.api:app", "--host", "0.0.0.0", "--port", "8095", "--workers", "2", \
     "--root-path", "/catalog", "--proxy-headers", "--forwarded-allow-ips", "*"]
