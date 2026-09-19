"""The geodesy mini-corpus: frozen queries, separate storage, source types.

The three rules from the design review, each pinned:

1. nothing is written outside `briefs/geodesy/`;
2. non-paper authorities carry a `source_type` and are never mistaken for papers;
3. the query file is hashed before discovery and cannot move silently.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.paper_3.briefs import geodesy as g


@pytest.fixture(scope="module")
def families():
    return g.load_families()


# ── the query set ─────────────────────────────────────────────────────────────

def test_the_eight_families_the_review_asked_for(families):
    ids = {fam["id"] for fam in families["families"]}
    for expected in ("datum_transformation", "height_types", "permanent_tide",
                     "icesat2_reference", "swot_reference", "gnss_levelling",
                     "reference_systems", "altimetry_harmonisation"):
        assert expected in ids


def test_every_family_has_queries_and_terms(families):
    for fam in families["families"]:
        assert fam["queries"], f"{fam['id']} has no queries"
        assert fam["terms"], f"{fam['id']} has no terms"


def test_generic_terms_are_not_geodetic_terms(families):
    """'reference frame' and 'geolocation' must not admit a paper on their own."""
    generic = {t.lower() for t in families["generic_terms"]}
    for term in g.geodetic_terms(families):
        assert term.lower() not in generic
    assert "reference frame" in generic
    assert "geolocation" in generic


def test_permanent_tide_vocabulary_is_present(families):
    terms = {t.lower() for t in g.geodetic_terms(families)}
    for expected in ("permanent tide", "zero tide", "mean tide", "tide-free"):
        assert expected in terms


def test_all_queries_preserves_family_attribution(families):
    queries = g.all_queries(families)
    assert len(queries) == sum(len(f["queries"]) for f in families["families"])
    assert all(q["family"] and q["query"] for q in queries)


def test_families_in_text_reports_only_matching_families(families):
    text = "We converted ellipsoidal heights using the EGM2008 geoid model."
    found = g.families_in_text(families, text)
    assert "height_types" in found
    assert "reference_systems" in found
    assert "gnss_levelling" not in found


# ── the frozen manifest ───────────────────────────────────────────────────────

def test_freeze_writes_the_query_hash(tmp_path):
    manifest = g.freeze_manifest(tmp_path)
    assert manifest["queries_sha256"] == g._sha256(g.QUERIES_PATH)
    assert manifest["n_families"] == 8
    assert (g.geodesy_dir(tmp_path) / g.MANIFEST).exists()


def test_refreezing_the_same_queries_keeps_the_original_time(tmp_path):
    first = g.freeze_manifest(tmp_path)
    second = g.freeze_manifest(tmp_path)
    assert second["frozen_at"] == first["frozen_at"]


def test_discovery_refuses_without_a_manifest(tmp_path):
    with pytest.raises(FileNotFoundError, match="freeze"):
        g.assert_manifest_matches(tmp_path)


def test_a_changed_query_file_is_refused_without_force(tmp_path, monkeypatch):
    g.freeze_manifest(tmp_path)
    path = g.geodesy_dir(tmp_path) / g.MANIFEST
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["queries_sha256"] = "0" * 64      # pretend the file used to differ
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="tuning the search"):
        g.freeze_manifest(tmp_path)
    with pytest.raises(RuntimeError, match="differs from the frozen"):
        g.assert_manifest_matches(tmp_path)


def test_force_refreeze_keeps_history(tmp_path):
    g.freeze_manifest(tmp_path)
    path = g.geodesy_dir(tmp_path) / g.MANIFEST
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["queries_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    g.freeze_manifest(tmp_path, force=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["history"]) == 1
    assert payload["history"][0]["queries_sha256"] == "0" * 64


def test_manifest_records_the_separation_policy(tmp_path):
    manifest = g.freeze_manifest(tmp_path)
    assert "not merged" in manifest["corpus_policy"]


# ── storage is separate ───────────────────────────────────────────────────────

def test_everything_lands_under_briefs_geodesy(tmp_path):
    assert g.geodesy_dir(tmp_path) == tmp_path / "briefs" / "geodesy"
    g.freeze_manifest(tmp_path)
    written = {p.relative_to(tmp_path).parts[0] for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {"briefs"}


# ── technical sources ─────────────────────────────────────────────────────────

def test_technical_sources_carry_a_source_type():
    frame = g.technical_sources()
    assert not frame.empty
    assert set(frame["source_type"]) <= set(g.SOURCE_TYPES)
    assert "paper" not in set(frame["source_type"])


def test_technical_sources_include_the_draft_authorities():
    slugs = set(g.technical_sources()["slug"])
    for expected in ("SWOT_PIXC_PDD", "ICESAT2_ATL03_ATBD", "IAG_RESOLUTION_16_1983",
                     "EPSG_9902", "EVRF2019", "EGG2015",
                     "STOPKHAI_2026_BS77_EVRF2019", "CRS_EU_UA_KRON_EVRF2019ZERO",
                     "TREVOHO_2021_UA_HEIGHT_SYSTEM"):
        assert expected in slugs


def test_technical_sources_do_not_invent_urls():
    """An invented URL is worse than an empty one: a URL is downloadable only
    when someone fetched it by hand (`status: url_verified`), and every verified
    entry must actually carry one."""
    frame = g.technical_sources()
    verified = frame["status"] == "url_verified"
    assert set(frame["status"]) <= {"url_needed", "url_verified"}
    assert (frame.loc[~verified, "pdf_url"] == "").all()
    assert (frame.loc[verified, "pdf_url"] != "").all()
    assert (frame.loc[~verified, "is_oa"] == False).all()   # noqa: E712


def test_a_curated_paper_is_a_source_never_a_control():
    """Стопхай et al. 2026 has no DOI and is in no index: retrieval could never
    recover it, so it must not appear in any thesis's positive_controls."""
    from src.paper_3.theses import load_theses
    frame = g.technical_sources().set_index("slug")
    row = frame.loc["STOPKHAI_2026_BS77_EVRF2019"]
    assert row["source_type"] == "curated_paper"
    assert row["doi"] == ""
    assert row["mandatory"]
    assert g.expected_sha256("STOPKHAI_2026_BS77_EVRF2019").startswith("be6569bc")
    all_control_dois = {c.doi for t in load_theses() for c in t.positive_controls}
    assert not any("zgt.com.ua" in d for d in all_control_dois)


def test_mandatory_sources_carry_a_formal_citation():
    frame = g.technical_sources()
    mandatory = frame[frame["mandatory"]]
    assert len(mandatory) >= 3
    assert (mandatory["formal_citation"] != "").all()


def test_technical_sources_rank_first_for_download():
    assert (g.technical_sources()["download_priority"] == 99.0).all()


# ── selection and screening ───────────────────────────────────────────────────

def _cand(doi, fams, oa=True, prio=1.0):
    return {"doi": doi, "slug": doi.replace("/", "_"), "title": doi,
            "is_oa": oa, "pdf_url": "https://x/a.pdf" if oa else "",
            "n_families_matched": fams, "download_priority": prio}


def test_selection_prefers_multi_family_and_caps(tmp_path):
    frame = pd.DataFrame(
        [_cand(f"10.1/s{i}", 2, prio=10 - i) for i in range(5)]
        + [_cand(f"10.1/w{i}", 1, prio=1) for i in range(5)])
    chosen = g.select_for_download(frame, target_max=6)
    assert len(chosen) == 6
    assert (chosen["n_families_matched"] == 2).sum() == 5


def test_selection_never_picks_a_paywalled_row():
    frame = pd.DataFrame([_cand("10.1/closed", 3, oa=False)])
    assert g.select_for_download(frame).empty


def test_best_chunk_finds_the_dense_paragraph():
    terms = ["geoid", "permanent tide"]
    filler = "Nothing relevant here. " * 200
    dense = "The geoid and the permanent tide convention define the geoid heights. "
    text = filler + dense * 5 + filler
    density, cooccur, sample = g._best_chunk(terms, text)
    assert density > 0
    assert cooccur >= 1
    assert "geoid" in sample.lower()


def test_generic_only_text_is_not_admitted_by_screen(tmp_path):
    """'reference frame' and 'elevation' throughout, no geodetic term at all."""
    gd = g.geodesy_dir(tmp_path)
    (gd / "text").mkdir(parents=True)
    text = {"paper_id": "p", "sections": {
        "body": ("We used a reference frame and measured elevation. " * 60)}}
    (gd / "text" / "p.json").write_text(json.dumps(text), encoding="utf-8")
    pd.DataFrame([{"slug": "p", "doi": "10.1/p", "title": "generic", "source_type": "paper",
                   "text_path": str(gd / "text" / "p.json")}]).to_parquet(
        gd / g.FULLTEXT_MANIFEST, index=False)

    sheet = g.screen(tmp_path)
    assert len(sheet) == 1
    assert bool(sheet.iloc[0]["generic_only"])
    assert not bool(sheet.iloc[0]["auto_admitted"])


def test_screening_sheet_has_the_manual_columns(tmp_path):
    gd = g.geodesy_dir(tmp_path)
    (gd / "text").mkdir(parents=True)
    text = {"paper_id": "p", "sections": {
        "methods": ("Heights were converted from tide-free ellipsoidal heights "
                    "to normal heights with the EGG2015 quasigeoid; the permanent "
                    "tide correction was 3.5 cm. " * 12)}}
    (gd / "text" / "p.json").write_text(json.dumps(text), encoding="utf-8")
    pd.DataFrame([{"slug": "p", "doi": "10.1/p", "title": "real", "source_type": "paper",
                   "text_path": str(gd / "text" / "p.json")}]).to_parquet(
        gd / g.FULLTEXT_MANIFEST, index=False)

    sheet = g.screen(tmp_path)
    for col in ("manual_verdict", "manual_note", "best_passage", "auto_admitted"):
        assert col in sheet.columns
    assert bool(sheet.iloc[0]["auto_admitted"])
    assert (sheet["manual_verdict"] == "").all()


# ── curated sources ───────────────────────────────────────────────────────────

def _curated_yaml(tmp_path, sha):
    path = tmp_path / "technical_sources.yaml"
    path.write_text(
        "- id: CURATED_OK\n  source_type: curated_paper\n  title: ok\n"
        "  bears_on: [datum_transformation]\n  url: https://x/ok.pdf\n"
        f"  url_sha256: '{sha}'\n  status: url_verified\n  mandatory: true\n"
        "  formal_citation: A (2026). Ok.\n"
        "- id: NOT_VERIFIED\n  source_type: registry\n  title: nv\n  bears_on: [x]\n"
        "  url: https://x/never.pdf\n  status: url_needed\n",
        encoding="utf-8")
    return path


def test_curated_step_fetches_only_verified_sources_and_checks_the_sha(tmp_path, monkeypatch):
    import hashlib
    body = b"%PDF-1.4 curated"
    monkeypatch.setattr(g, "TECHNICAL_PATH", _curated_yaml(tmp_path, hashlib.sha256(body).hexdigest()))
    fetched = []

    def fake_fetch(session, url, target):
        fetched.append(url)
        target.write_bytes(body)
        return "downloaded"
    monkeypatch.setattr(g, "_fetch_pdf", fake_fetch)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    manifest = g.download_curated(tmp_path, session=object())
    assert fetched == ["https://x/ok.pdf"], "an unverified URL is never fetched"
    row = manifest.set_index("slug").loc["CURATED_OK"]
    assert row["download_status"] == "downloaded"
    assert row["source_type"] == "curated_paper"
    # Idempotent: a second run leaves the manifest alone.
    again = g.download_curated(tmp_path, session=object())
    assert len(again) == 1 and fetched == ["https://x/ok.pdf"]


def test_curated_step_refuses_bytes_that_differ_from_the_vetted_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "TECHNICAL_PATH", _curated_yaml(tmp_path, "0" * 64))
    monkeypatch.setattr(g, "_fetch_pdf",
                        lambda s, u, t: (t.write_bytes(b"%PDF-1.4 other"), "downloaded")[1])
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    manifest = g.download_curated(tmp_path, session=object())
    assert manifest.set_index("slug").loc["CURATED_OK", "download_status"] == "sha_mismatch"
