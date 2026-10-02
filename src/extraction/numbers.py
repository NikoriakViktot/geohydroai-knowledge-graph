"""Number parsing shared by the regex, scientific and table extractors.

Two bugs lived in five separate copies of this logic (API_PLAN_v1 О-8, О-28):

* the Unicode minus sign (U+2212), which journals typeset for negative values, was
  not recognised, so "NSE = −0.27" lost its sign or failed to parse;
* every value above 1 was divided by 100 as if it were a percentage, which is right
  for overall accuracy (94.2 % → 0.942) but turns an impossible NSE of 1.7 into a
  plausible-looking 0.017.
"""

from __future__ import annotations

#: Characters typeset as a minus sign: U+2212 MINUS SIGN, U+FE63 SMALL HYPHEN-MINUS,
#: U+FF0D FULLWIDTH HYPHEN-MINUS.
MINUS_SIGNS = "−﹣－"

#: Regex fragment for a signed decimal number, accepting the minus signs above.
SIGNED_NUMBER = r"([-−﹣－]?[\d]+\.?[\d]*)"

#: Spaces used as thousands separators or between a number and its % sign.
_SPACES = "    "


def normalize_minus(text: str) -> str:
    """Replace typeset minus signs with ASCII '-' (for literal-presence checks)."""
    for ch in MINUS_SIGNS:
        text = text.replace(ch, "-")
    return text


def parse_number(raw: str) -> float:
    """Parse a number as typeset in a paper: Unicode minus, decimal comma, a
    trailing % and thin/no-break spaces are accepted. Raises ValueError otherwise.
    The value is returned as written — no percent rescaling (see `to_ratio`)."""
    text = str(raw).strip()
    for ch in MINUS_SIGNS:
        text = text.replace(ch, "-")
    for ch in _SPACES:
        text = text.replace(ch, "")
    text = text.rstrip("%").replace(",", ".")
    return float(text)


def to_ratio(value: float, lo: float, hi: float) -> float:
    """Read a percentage as a ratio, but only for bounded ratio metrics.

    A value in (1, 100] is a percentage only when the metric itself lives in
    [0, 1] or [-1, 1] (OA, F1, IoU, kappa). Efficiency scores such as NSE, KGE and
    R² have no lower bound (lo = -inf) and an upper bound of 1, so a value above 1
    is an error to reject, never a percentage to rescale.
    """
    if hi == 1.0 and lo >= -1.0 and 1.0 < value <= 100.0:
        return value / 100.0
    return value
