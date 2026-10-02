"""Data contracts (API_PLAN_v1/11_POSTGRES_TRUTH_LAYER.md §4).

Pydantic models here are the contract for the API and for every import into the
PostgreSQL layer of truth. Enumerated values are defined once (as Literal types)
and reused by the database CHECK constraints, so the two cannot drift apart.
JSON Schemas for consumers are exported with ``python -m src.contracts.export``.
"""
