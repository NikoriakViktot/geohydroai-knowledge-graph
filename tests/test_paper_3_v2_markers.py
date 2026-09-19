"""The placeholder grammar.

The property that matters most is that `find_marker_ids` and `parse_markers`
can never disagree — that is what makes "no marker was silently dropped between
the manuscript and the checklist" a provable claim rather than a hope.
"""
from __future__ import annotations

import pytest

from src.paper_3.v2 import markers as mk


def _marker(marker_id="FIG16", **fields):
    base = {
        "backs": "M3.1, M3.2",
        "section": "6.6 Sentinel-2 planform transformation",
        "produces": "SWOT-DNIPRO scripts/fig_sentinel_planform.py",
        "status": "not_started",
        "blocks_submission": "yes",
    }
    base.update(fields)
    return mk.Marker(id=marker_id, title="Fragmentation map of the pool",
                     fields=base)


# ── round trip ────────────────────────────────────────────────────────────────

def test_render_parse_round_trip():
    original = _marker()
    parsed = mk.parse_markers(mk.render(original))
    assert len(parsed) == 1
    assert parsed[0].id == original.id
    assert parsed[0].title == original.title
    assert parsed[0].fields == dict(original.fields)


@pytest.mark.parametrize("marker_id,kind", [
    ("FIG01", "figure"), ("TAB08", "table"),
    ("OPEN11", "open_item"), ("REF03", "reference"),
])
def test_every_kind_round_trips(marker_id, kind):
    fields = {f: "x" for f in mk.REQUIRED_FIELDS[kind]}
    fields["status"] = "not_started"
    fields["blocks_submission"] = "yes"
    marker = mk.Marker(id=marker_id, title="A thing", fields=fields)

    parsed = mk.parse_markers(mk.render(marker))[0]
    assert parsed.kind == kind
    assert parsed.fields == fields


def test_render_is_deterministic():
    """A re-run of the assembler must produce byte-identical output."""
    marker = _marker()
    assert mk.render(marker) == mk.render(marker)


def test_cyrillic_survives_a_round_trip():
    """The draft is full of НПГ, ГМО, УНС and Ukrainian place names."""
    marker = _marker()
    marker = mk.Marker(id=marker.id, title="Карта фрагментації (НПГ 16.0 м)",
                       fields={**marker.fields, "note": "Каховське водосховище"})
    parsed = mk.parse_markers(mk.render(marker))[0]
    assert parsed.title == "Карта фрагментації (НПГ 16.0 м)"
    assert parsed.fields["note"] == "Каховське водосховище"


# ── the drop oracle ───────────────────────────────────────────────────────────

def test_find_ids_and_parse_never_disagree():
    text = "\n\n".join(mk.render(_marker(f"FIG{i:02d}")) for i in range(1, 6))
    assert mk.find_marker_ids(text) == [m.id for m in mk.parse_markers(text)]


def test_markers_are_found_among_ordinary_prose():
    text = (
        "## 6.6 Sentinel-2 planform transformation\n\n"
        "A paragraph of the author's prose, with a number 2 033 km² in it.\n\n"
        + mk.render(_marker()) + "\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "> An ordinary block quote that is not a marker.\n\n"
        "More prose.\n"
    )
    found = mk.parse_markers(text)
    assert [m.id for m in found] == ["FIG16"]
    assert found[0].fields["backs"] == "M3.1, M3.2"


def test_an_ordinary_block_quote_is_not_a_marker():
    text = "> **Bold text** in a quote\n> `key: value`\n"
    assert mk.parse_markers(text) == []
    assert mk.find_marker_ids(text) == []


def test_a_marker_stops_at_the_end_of_its_own_quote():
    text = (mk.render(_marker()) + "\n\nOrdinary prose with `stray: field` in it.\n")
    parsed = mk.parse_markers(text)[0]
    assert "stray" not in parsed.fields


def test_consecutive_markers_do_not_bleed_into_each_other():
    text = mk.render(_marker("FIG01")) + "\n\n" + mk.render(
        _marker("TAB01", section="5.1 The gauge network", produces="other.py"))
    first, second = mk.parse_markers(text)
    assert first.section == "6.6 Sentinel-2 planform transformation"
    assert second.section == "5.1 The gauge network"


def test_char_offsets_are_recorded_in_document_order():
    text = "intro\n\n" + mk.render(_marker("FIG01")) + "\n\n" + mk.render(_marker("FIG02"))
    parsed = mk.parse_markers(text)
    assert parsed[0].char_offset < parsed[1].char_offset


# ── validation ────────────────────────────────────────────────────────────────

def test_a_complete_marker_validates():
    assert mk.validate(_marker()) == []


@pytest.mark.parametrize("missing", ["backs", "section", "produces", "status"])
def test_a_missing_required_field_is_rejected(missing):
    fields = dict(_marker().fields)
    fields.pop(missing)
    marker = mk.Marker(id="FIG16", title="t", fields=fields)
    assert any(missing in p for p in mk.validate(marker))


def test_an_illegal_status_is_rejected():
    assert any("illegal status" in p
               for p in mk.validate(_marker(status="nearly_done")))


def test_blocks_submission_must_be_a_yes_or_no():
    assert any("must be yes or no" in p
               for p in mk.validate(_marker(blocks_submission="maybe")))


def test_a_scientifically_blocked_marker_must_say_what_unblocks_it():
    marker = _marker(scientific_status="blocked")
    assert any("what would unblock it" in p for p in mk.validate(marker))

    marker = _marker(scientific_status="blocked", unblock_by="A3, A4")
    assert mk.validate(marker) == []
    assert marker.unblock_by == ("A3", "A4")


def test_duplicate_ids_are_rejected_across_a_set():
    problems = mk.validate_all([_marker("FIG16"), _marker("FIG16")])
    assert any("appears 2 times" in p for p in problems)


def test_an_unknown_prefix_is_rejected():
    marker = mk.Marker(id="ZZZ01", title="t", fields={})
    assert any("unknown kind" in p for p in mk.validate(marker))


# ── parsed properties ─────────────────────────────────────────────────────────

def test_backs_splits_on_commas_and_semicolons():
    assert _marker(backs="M3.1; M3.2, V6.1").backs == ("M3.1", "M3.2", "V6.1")


def test_blocks_submission_reads_truthy_values():
    for value in ("yes", "YES", "true", "1"):
        assert _marker(blocks_submission=value).blocks_submission
    for value in ("no", "false", "0", ""):
        assert not _marker(blocks_submission=value).blocks_submission


def test_empty_fields_are_dropped_from_the_render():
    rendered = mk.render(_marker(note=""))
    assert "note" not in rendered


# ── the submission gate ───────────────────────────────────────────────────────

def test_assert_none_remain_passes_on_a_clean_manuscript():
    mk.assert_none_remain("A finished manuscript with no placeholders.")


def test_assert_none_remain_names_what_is_left():
    text = mk.render(_marker("FIG16")) + "\n\n" + mk.render(_marker("TAB08"))
    with pytest.raises(AssertionError) as err:
        mk.assert_none_remain(text)
    assert "FIG16" in str(err.value)
    assert "TAB08" in str(err.value)
