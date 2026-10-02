"""PostgreSQL layer of truth (API_PLAN_v1/11_POSTGRES_TRUTH_LAYER.md).

Schemas: core (papers, aliases, files, cohorts, projections), biblio, project,
evidence, ops (runs, imported files). Neo4j, Chroma and the parquet layer are
projections rebuilt from here plus the file store.
"""
