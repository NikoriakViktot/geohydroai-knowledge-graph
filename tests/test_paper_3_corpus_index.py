

# ── the live parquet layer, and what its missing values look like (2026-09-23) ──

def test_normalize_doi_survives_a_missing_parquet_value():
    """A DOI absent from papers.parquet arrives as NaN, a float. Before this the
    corpus index crashed on the live layer and only worked against a stale copy."""
    import math
    from src.paper_3._utils import normalize_doi
    assert normalize_doi(float("nan")) == ""
    assert normalize_doi(None) == ""
    assert normalize_doi("  https://doi.org/10.1000/XYZ ") == "10.1000/xyz"


def test_views_read_papers_from_the_live_layer_not_the_frozen_one():
    """papers.parquet and references.parquet exist in both trees; the live layer
    is rebuilt by build_parquet_layer and the other is not."""
    from src.paper_3._utils import _VIEWS, PARQUET_DIR, ANALYTICS_DIR
    by_name = {n: base for n, _f, base in _VIEWS}
    assert by_name["papers"] == PARQUET_DIR
    assert by_name["references_tbl"] == PARQUET_DIR
    # the derived fact tables exist only in the frozen tree
    assert by_name["numeric_facts"] == ANALYTICS_DIR
    assert by_name["sensors"] == ANALYTICS_DIR
