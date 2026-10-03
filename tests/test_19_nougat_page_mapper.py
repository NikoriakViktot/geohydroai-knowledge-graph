"""Page-level Nougat markdown → GROBID regions (src/document/nougat_page_mapper.py)."""
from src.document.nougat_page_mapper import assign_regions, split_blocks

PAGE_MD = r"""## 3 Results

The calibrated model reproduces the observed hydrograph well.

\[NSE=1-\frac{\sum_{i}(O_{i}-S_{i})^{2}}{\sum_{i}(O_{i}-\bar{O})^{2}}\] (4)

\[KGE=1-\sqrt{(r-1)^{2}+(\beta-1)^{2}}\] (5)

\begin{table}
\begin{tabular}{l c c} \hline Station & NSE & RMSE \\ \hline
Nova Kakhovka & 0.82 & 1.35 \\ Kherson & 0.77 & 2.10 \\ \hline
\end{tabular}
\end{table}
Table 2: Performance at the gauging stations.

\begin{table}
\begin{tabular}{l c} \hline Parameter & Value \\ \hline
Manning n & 0.035 \\ Time step & 60 \\ \hline
\end{tabular}
\end{table}
Table 3: Model parameters.
"""


def words_at(y, text, x=60.0):
    out = []
    for tok in text.split():
        out.append((x, y, x + 6 * len(tok), y + 9, tok))
        x += 6 * len(tok) + 4
    return out


WORDS = (words_at(100, "NSE = 1 − Σ(Oi − Si)2 / Σ(Oi − Ō)2 (4)")
         + words_at(130, "KGE = 1 − √((r − 1)2 + (β − 1)2) (5)")
         + words_at(200, "Station NSE RMSE") + words_at(212, "Nova Kakhovka 0.82 1.35")
         + words_at(224, "Kherson 0.77 2.10") + words_at(236, "Table 2. Performance at the gauging stations.")
         + words_at(300, "Parameter Value") + words_at(312, "Manning n 0.035")
         + words_at(324, "Time step 60") + words_at(336, "Table 3. Model parameters."))

REGIONS = [
    {"region_id": "eq4", "region_type": "FORMULA_REGION", "bbox": (50, 95, 500, 112), "formula_text": "NSE = 1 - ... (4)"},
    {"region_id": "eq5", "region_type": "FORMULA_REGION", "bbox": (50, 125, 500, 142), "formula_text": None},
    # listed in the opposite order on purpose: assignment must follow content, not order
    {"region_id": "tab3", "region_type": "TABLE_REGION", "bbox": (50, 295, 500, 345)},
    {"region_id": "tab2", "region_type": "TABLE_REGION", "bbox": (50, 195, 500, 245)},
]


def test_split_blocks_finds_tables_with_captions_and_numbered_equations():
    blocks = split_blocks(PAGE_MD)
    kinds = [b.kind for b in blocks]
    assert kinds == ["equation", "equation", "table", "table"]
    assert [b.number for b in blocks if b.kind == "equation"] == ["4", "5"]
    assert "Table 2: Performance" in blocks[2].text


def test_tables_are_assigned_by_printed_content_not_order():
    a = assign_regions(PAGE_MD, REGIONS, WORDS)
    assert "Kherson" in a["tab2"].text and "0.82" in a["tab2"].text
    assert "Manning" in a["tab3"].text
    assert a["tab2"].method == "layer_cover"


def test_equations_are_assigned_by_number():
    a = assign_regions(PAGE_MD, REGIONS, WORDS)
    assert a["eq4"].method == "equation_number" and "NSE" in a["eq4"].text
    assert a["eq5"].method == "equation_number" and "KGE" in a["eq5"].text


def test_region_without_matching_block_gets_no_text():
    extra = REGIONS + [{"region_id": "tab9", "region_type": "TABLE_REGION",
                        "bbox": (50, 600, 500, 700)}]
    words = WORDS + words_at(620, "Sediment yield 412 t km−2 basin area 18 300")
    a = assign_regions(PAGE_MD, extra, words)
    assert a["tab9"].text is None and a["tab9"].method == "unmatched"


def test_split_table_shares_its_block_but_other_heights_do_not():
    # GROBID splits one table into a left and a right region at the same height
    left  = {"region_id": "left",  "region_type": "TABLE_REGION", "bbox": (50, 195, 138, 245)}
    right = {"region_id": "right", "region_type": "TABLE_REGION", "bbox": (138, 195, 500, 245)}
    a = assign_regions(PAGE_MD, [left, right], WORDS)
    assert a["left"].text and a["left"].text == a["right"].text
    # a region lower on the page that prints the same words cannot take that block
    clone = {"region_id": "clone", "region_type": "TABLE_REGION", "bbox": (50, 600, 500, 650)}
    words = WORDS + words_at(612, "Nova Kakhovka 0.82 1.35")
    b = assign_regions(PAGE_MD, [dict(REGIONS[3]), clone], words)
    assert "Kherson" in b["tab2"].text and b["clone"].text is None


def test_no_text_layer_means_no_assignment():
    a = assign_regions(PAGE_MD, REGIONS, [])
    assert all(x.text is None for x in a.values())
