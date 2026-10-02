"""Imports into the PostgreSQL layer of truth.

Each importer is split into a pure part (scan + classify, unit-tested on temporary
directories) and a writer that loads one run into Postgres in one transaction,
recording provenance in ops.run / ops.source_file.
"""
