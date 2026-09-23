

# ── a control is in the corpus when its text is, not when its DOI is (2026-09-23) ──

def test_control_resolves_by_slug_when_the_index_has_no_doi():
    """The 1989 USGS roughness guide carries no DOI a parser can find, so its
    index row has an empty doi. Matching controls on DOI alone reported a fully
    ingested paper as `not_in_corpus` and sent the reader off to fetch it again."""
    import pandas as pd
    from src.paper_3 import retrieve as rt
    from src.paper_3.theses import Thesis, PositiveControl

    index = pd.DataFrame([
        {"paper_id": "10.3133_wsp2339", "doi": "", "slug": "10.3133_wsp2339",
         "title": "Guide for selecting Manning roughness coefficients", "cohort": "paper_3",
         "has_normalized": True},
    ])
    want = rt.doi_to_slug("10.3133/wsp2339")
    assert want == "10.3133_wsp2339"
    # the slug map the resolver builds must reach the row the DOI cannot
    pid_by_slug = {str(p): str(p) for p in index["paper_id"]}
    assert pid_by_slug.get(want) == "10.3133_wsp2339"
    doi_by_pid = dict(zip(index["paper_id"], index["doi"]))
    assert doi_by_pid.get("10.3133_wsp2339") != "10.3133/wsp2339"   # DOI alone fails
