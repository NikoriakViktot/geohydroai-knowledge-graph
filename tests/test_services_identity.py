"""Canonical DOI normalisation (src/services/identity.py)."""

from __future__ import annotations

import pytest

from src.services.identity import normalize_doi


@pytest.mark.parametrize("raw, expected", [
    ("10.1029/2024WR038314", "10.1029/2024wr038314"),
    ("https://doi.org/10.1029/2025GL120832", "10.1029/2025gl120832"),
    ("http://dx.doi.org/10.1016/j.rse.2014.02.015", "10.1016/j.rse.2014.02.015"),
    ("DOI: 10.5194/nhess-19-2405-2019", "10.5194/nhess-19-2405-2019"),
    ("doi:10.1007/s10712-015-9346-y.", "10.1007/s10712-015-9346-y"),
    ("<10.1088/1748-9326/ac4d4f>", "10.1088/1748-9326/ac4d4f"),
    ("  10.24425/agg.2023.146162 ;", "10.24425/agg.2023.146162"),
    ("10.1002/(SICI)1099-1085(199602)10:2<175::AID-HYP359>3.0.CO;2-#",
     "10.1002/(sici)1099-1085(199602)10:2<175::aid-hyp359>3.0.co;2-#"),
    ("10.1000/abc(1)", "10.1000/abc(1)"),          # balanced bracket is part of the DOI
    ("10.1000/abc(1)).", "10.1000/abc(1)"),        # the extra one came from the sentence
    ("(see 10.1000/xyz)", None),                   # not a bare DOI
])
def test_normalizes(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "not a doi", "11.1000/x", "10.12/x", "10.1000"])
def test_rejects_non_dois(raw):
    assert normalize_doi(raw) is None


def test_idempotent():
    once = normalize_doi("https://doi.org/10.1029/2024WR038314")
    assert normalize_doi(once) == once
