"""translate.py — numbers, DOIs, ids and markers are locked; a bad round-trip keeps the English."""
from __future__ import annotations

from src.paper_3.v2 import translate as tr
from src.paper_3.v2 import markers

PAR = ("The slope moved from +0.090 cm/km to +3.314 cm/km (95 % CI [+1.994, +5.071]; n = 14/14) "
       "[M1.1], see (Arcement & Schneider, 1989) and doi:10.3133/wsp2339 over 2 107 km².")


def test_lock_unlock_round_trip_is_identity():
    masked, spans = tr.lock(PAR)
    assert "0.090" not in masked and "10.3133/wsp2339" not in masked and "M1.1" not in masked
    assert "(Arcement & Schneider, 1989)" not in masked
    assert tr.unlock(masked, spans) == PAR


def test_verify_rejects_missing_or_invented_tokens():
    masked, _ = tr.lock(PAR)
    assert tr.verify_locked_tokens(masked, masked)
    assert tr.verify_locked_tokens(masked, masked.replace("⟦0⟧", "x ⟦0⟧"))       # moved is fine
    assert not tr.verify_locked_tokens(masked, masked.replace("⟦0⟧", ""))         # dropped
    assert not tr.verify_locked_tokens(masked, masked + " ⟦99⟧")                  # invented
    assert not tr.verify_locked_tokens(masked, masked.replace("⟦1⟧", "⟦0⟧"))     # duplicated


def test_paragraph_translation_keeps_tokens_and_falls_back_on_failure():
    glossary = {"slope": "ухил"}
    def good(prompt):
        masked = prompt.split("PARAGRAPH:\n", 1)[1]
        return "Ухил змінився: " + masked
    out, ok = tr.translate_paragraph(PAR, glossary, good)
    assert ok and out.startswith("Ухил змінився: ") and "+3.314 cm/km" in out and "10.3133/wsp2339" in out

    def bad(prompt):
        return "переклад без токенів"
    out, ok = tr.translate_paragraph(PAR, glossary, bad)
    assert not ok and out == PAR


def test_structure_and_markers_are_never_sent():
    calls = []
    def spy(prompt):
        calls.append(prompt); return prompt
    heading = "# 6. RESULTS"
    marker = markers.render(markers.Marker(id="OPEN41", title="x", fields={"status": "blocked"}))
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    for block in (heading, marker, table):
        out, ok = tr.translate_paragraph(block, {}, spy)
        assert ok and out == block
    assert calls == []


def test_translate_text_marks_rejected_paragraphs():
    text = "# H\n\nOne sentence with 3 m.\n\nAnother with 4 m."
    def flaky(prompt):
        return "ok ⟦0⟧" if "3" in prompt or "⟦0⟧" in prompt else None
    out, failed = tr.translate_text(text, {}, lambda p: None, delay=0)
    assert failed == [1, 2]
    assert len(markers.find_marker_ids(out)) == 2
    assert "One sentence with 3 m." in out


def test_glossary_loads_and_prompt_lists_it():
    g = tr.load_glossary()
    assert g["EVRF2019"] == "EVRF2019" and "Каховське" in g["Kakhovka Reservoir"]
    p = tr.build_prompt("⟦0⟧ x", g)
    assert "⟦0⟧" in p and "Каховське водосховище" in p
