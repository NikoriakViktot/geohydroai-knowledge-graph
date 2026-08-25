
TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
.venv/bin/python3 -m src.orchestration.pipeline_runner --workers 3



  ---
Що відбувається зараз і що далі

Зараз (фоново):
GROBID обробляє 977 PDF → data/literature/grobid_xml/*.tei.xml
Прогрес: tail -f data/ingestion_logs/grobid_missing_batch.log

Після завершення GROBID (~50 хв), запускай pipeline:

TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
.venv/bin/python3 scripts/ingest_missing_pdfs.py --pipeline-only --workers 3

Або одразу обидва кроки (якщо GROBID ще не закінчив):
# pipeline-only пропустить XML-файли що вже є і доробить решту
.venv/bin/python3 scripts/ingest_missing_pdfs.py --dry-run

Після pipeline — перебудуй parquet і граф:
# 1. Нормалізація нових paper.json
python -m src.orchestration.normalization_runner \
--input-dir data/literature/paper_json \
--output-dir data/normalized

# 2. Перебудова Neo4j (додасть нові вузли)
python -m src.graph.build_graph \
--uri bolt://localhost:7687 --user neo4j --password python2024

# 3. Оновлення parquet-шару
python -m src.enrichment.build_parquet_layer

Xu 2006 MNDWI та McFeeters 1996 NDWI тоді стануть повноправними вузлами графу.

