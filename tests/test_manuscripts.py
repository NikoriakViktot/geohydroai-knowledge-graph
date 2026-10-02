"""Author–year citations in a manuscript mapped to BibTeX keys (service only; the endpoint is planned)."""

from src.services.manuscripts import find, sentences

BIB = """@article{Johnson_2019, author={Johnson, J. M. and Munasinghe, D. and Eyelade, D.}, year={2019}, doi={10.5194/nhess-19-2405-2019}}
@article{Hohle_2009, author={Höhle, Joachim and Höhle, Michael}, year={2009}}
@misc{CEOBS_2023, author={{Conflict and Environment Observatory}}, year={2023}}
@article{Bates_2022, author={Bates, Paul D.}, year={2022}}
@article{Unused_2020, author={Nobody, A.}, year={2020}}"""

MS = """# Introduction
HAND-based methods "do not accurately capture inundated cells" (Johnson et al. 2019; Bates 2022).
Höhle and Höhle (2009) give robust accuracy measures, see also (CEOBS 2023) and (Ghost et al., 2021).
```
(Johnson et al. 2019) inside code is ignored.
```
"""


def test_sentences_do_not_break_after_abbreviations():
    assert sentences("Zheng et al. 2018 is cited. See Fig. 3 and J. Smith. Next one.") == [
        "Zheng et al. 2018 is cited.", "See Fig. 3 and J. Smith.", "Next one."]


def test_citations_resolve_to_keys():
    r = find(MS, BIB)
    got = [(o["cite_text"], o["status"], o["cite_key"]) for o in r["occurrences"]]
    assert got == [("(Johnson et al. 2019)", "resolved", "Johnson_2019"), ("(Bates 2022)", "resolved", "Bates_2022"),
                   ("Höhle and Höhle (2009)", "resolved", "Hohle_2009"), ("(CEOBS 2023)", "resolved", "CEOBS_2023"),
                   ("(Ghost et al., 2021)", "missing", None)]
    assert r["occurrences"][0]["quoted"] == ["do not accurately capture inundated cells"]
    assert r["occurrences"][0]["section"] == "Introduction" and r["occurrences"][0]["doi"] == "10.5194/nhess-19-2405-2019"
    assert r["uncited_entries"] == ["Unused_2020"] and r["missing_keys"][0]["cite_text"] == "(Ghost et al., 2021)"
