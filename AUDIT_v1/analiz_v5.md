

● Now I have the complete picture. Let me produce the deep architectural analysis.

  ---
GeoHydroAI — Principal Architecture Analysis

Executive Summary

The system is at an architectural inflection point. The infrastructure is more sophisticated than it appears from any single file: you have a    
mature SDOM (TEIDocument), a KB-driven ontology with 1100+ entities, section-gated regex extraction, Neo4j graph, DuckDB registry, Parquet
analytics, and now a semantic visual parsing layer. But these components are not integrated into a coherent intelligence pipeline — they form    
parallel silos that extract from the same source independently.

The critical diagnosis: the Nougat region pipeline is an island. It produces ScientificRegion JSON that nothing downstream reads. The knowledge  
graph has no visual provenance. The ontology has no connection to visual objects. The formula model has text but no mathematical structure. This
is not a coding problem — it is a semantic integration architecture problem.
  
---
Layer-by-Layer Architecture Analysis

CURRENT ARCHITECTURE (honest representation)
═════════════════════════════════════════════

PDF ──────────────────────────────────────────────────────────────────┐
│                                                                    │
▼ GROBID                                                             ▼ GROBID
TEI XML ──────────────────────────────┐                    Coordinate Blocks
│                                    │                         │
▼ parser.py / hybrid_parser.py       │                         ▼ nougat_region_pipeline.py
TEIDocument (SDOM)                    │                    ScientificRegion JSONs
│                                    │                         │
├─ sections → regex_extractor.py     │                         ▼ NougatActor
│             scientific_extractor   │                    Nougat Markdown
│             entity_extractor.py    │                         │
│             (KB-driven patterns)   │                         ▼ ???
│                  │                 │                   [DISCONNECTED — nobody reads this]
│                  ▼                 │
│            extracted entities      │
│            (methods/sensors/       │
│             metrics/countries)     │
│                  │                 │
│             normalizer.py          │
│             ontology_matcher.py    │
│                  │                 │
▼                  ▼                 │
OpenAlex ──► enriched/*.json ─────────┘
│
▼
parquet_builder.py
DuckDB registry
│
▼
graph_loader.py ──► Neo4j
(Paper, Author, Method,
Sensor, Metric, Country,
FloodEvent nodes)
│
▼
Dashboard (Dash)

═══════════════════════════════════════════════════════════
WHAT IS MISSING: a semantic integration bus that connects
visual parsing output → extraction → ontology → graph
═══════════════════════════════════════════════════════════

  ---
1. Architectural Weakness Analysis

1.1 The Modality Silo Problem

The most fundamental architectural flaw is modality isolation. Two independent extraction pipelines operate on the same papers:

- Text pipeline: GROBID → TEIDocument → regex/KB extraction → KG
- Visual pipeline: PDF → nougat_region_pipeline → ScientificRegion JSON → nowhere

There is no integration point. The ScientificRegion has formula_text, caption_text, nougat_result — but the TEIDocument model has no regions     
field, the PaperNode in Neo4j has no visual object relations, and the scientific_extractor.py never reads Nougat output.

Consequence: Every insight locked in a figure's axis labels, a table that GROBID couldn't parse, or an equation whose explanation only appears   
visually — is permanently invisible to the KG. In hydrology papers, a substantial fraction of quantitative information lives in figures and
tables.

1.2 Formula Intelligence Is OCR, Not Understanding

The Formula model:
@dataclass(frozen=True)
class Formula:
xml_id: str
text:   str   # LaTeX or raw text content
coords: Coordinates | None

The formula_text from the Nougat region pipeline is Nougat's decoded markdown from the image crop — something like P_e = \frac{(P - I_a)^2}{P -
I_a + S}.

What the system cannot do with this:
- Parse the LHS variable (P_e = accumulated precipitation excess)
- Identify the formula's role (loss function? routing equation? calibration objective?)
- Extract the variable dictionary (P = rainfall depth, I_a = initial abstraction, S = potential retention)
- Link variables to their text definitions (the surrounding paragraph that explains what each symbol means)
- Normalize equation form for cross-paper comparison (SCS-CN equation appears in dozens of papers with different notation)
- Detect that this equation is the canonical SCS-CN loss equation from the USDA
- Store the equation in a machine-operable form (SymPy expression tree, MathML)

The system has OCR of equations. It does not have equation understanding.

1.3 Table Semantic Vacuum

The Table model has rows: tuple[tuple[str, ...], ...] — flat cell text. No schema, no column typing, no unit parsing, no row/column header       
relationship.

Critical for hydrology: tables in these papers contain:
- Calibration parameter tables (symbol, description, range, calibrated value, unit)
- Validation statistics (event, observed peak, simulated peak, NSE, PBIAS, R²)
- Runoff curve numbers by LULC class (class, description, CN)
- Rainfall event tables (date, depth_mm, duration_h, peak_flow_m3s)
- Model comparison tables (model, OA, F1, IoU, kappa)

None of this structure is preserved. The system cannot answer "which table in paper X contains the calibrated CN values?"

1.4 Figure Understanding Is Classification, Not Interpretation

The current classifier produces labels (CHART_REGION, SCIENTIFIC_DIAGRAM) based on caption keyword matching. Nougat then produces markdown from  
the image crop — which for a hydrograph chart produces degraded text artifacts, not data.

What is actually needed: figure type dispatch. Different figure types require different understanding strategies:
- Hydrographs: time-series chart → axis label extraction → peak value identification
- DEM maps: raster visualization → geographic extent → coordinate system
- LULC maps: classified map → legend extraction → class-area statistics
- Flowcharts/methodology diagrams: graph structure → method sequence extraction
- Scatter plots: OA/F1 correlation plots → axis values → paper-level metric extraction
- Bar/pie charts: categorical data → value extraction

Sending all of these to Nougat with the same parse_image() call is the wrong architecture. Nougat is excellent at reconstructing equations and
structured text. It is poor at extracting quantitative data from charts.

1.5 The Ontology Is Not Connected to Visual Objects

The ontology has 1100+ entities across methods/sensors/metrics/concepts. The KB-driven entity extractor runs on section text. But:

- No visual entity grounding: if HEC-HMS appears only in a flowchart, it's invisible
- No formula-to-concept linking: the SCS-CN equation is not linked to the SCS-CN method ontology entry
- No table column-to-metric linking: a column labeled NSE in a table is not linked to the Nash-Sutcliffe Efficiency metric node
- No figure caption-to-method linking: "Rainfall-runoff simulation results" in a caption is not linked to the HEC-HMS method

The ontology is a text-world ontology. The visual world is dark to it.

1.6 Graph Schema Has No Visual Provenance Layer

# Current schema — no visual objects anywhere
L_PAPER, L_AUTHOR, L_INSTITUTION, L_TOPIC,
L_METHOD, L_SENSOR, L_METRIC, L_COUNTRY, L_FLOOD_EVENT

You cannot currently answer:
- "Which papers have hydrographs showing NSE > 0.7 for Koraiyar basin?"
- "Which figures use SCS unit hydrograph as the transformation method?"
- "What is the range of calibrated CN values reported in table form?"
- "Which equations define the SCS-CN loss model?"

The graph has no VisualObject, Equation, ScientificTable, or ScientificFigure nodes. The visual parsing work is invisible at the knowledge
representation level.

1.7 Region Merging Is Globally Parameterized

MERGE_DISTANCE_Y = 120   # hardcoded PDF points
MERGE_DISTANCE_X = 80
PAGE_FULL_THRESHOLD = 0.55

These work adequately for A4 academic papers. But the corpus includes:
- Conference papers (two-column, dense layout — needs smaller MERGE_DISTANCE)
- Technical reports (landscape, wide figures — needs page-relative distances)
- Scanned PDFs (GROBID coordinates unreliable — the coordinate system is broken)
- Papers where GROBID generates zero figure coords (no coords attribute at all)

Adaptive thresholds based on page size, column count, and coordinate density would significantly improve recall.

1.8 NougatActor Is a Single Point of Failure With No Backpressure

# Current pattern: fire all futures, collect all results
for region in regions:
future = self.actor.parse_image.remote(image, region.region_id)
futures.append(future)
region_meta.append(...)

for idx, (region, crop_path) in enumerate(region_meta):
parsed = ray.get(futures[idx])  # blocks until that specific future is done

Issues:
- No bounded parallelism: all futures are fired immediately. For a paper with 66 regions, 66 images are sent to the actor's queue before any
  results are collected. The actor's Ray task queue and GPU VRAM are unbounded.
- Single actor = zero parallelism: one NougatActor processes one image at a time. No batching, no pipeline overlap.
- No actor health check: if the actor OOMs and restarts, ray.get() raises RayActorError. The except Exception handler logs it and continues, but
  the actor is now dead. All remaining futures silently fail.
- No GPU batching: images are sent one at a time. Nougat's VisionEncoderDecoder can batch at inference time, potentially 4-8x throughput
  improvement.

1.9 Missing Inference Cache Layer

There is no caching at any level of the visual pipeline:
- No crop hash → Nougat result cache
- No paper-level "already processed" check in nougat_region_pipeline
- The registry checks text pipeline completion but not visual completion

A 3692-paper corpus with ~20 regions per paper = ~74,000 Nougat inference calls. At 15-30 seconds per call on CPU (or 2-5s on GPU), that is
months of compute without caching.

  ---
2. Failure Mode Analysis

2.1 Silent Data Loss Modes

┌───────────────────────────────────────────────┬───────────────────────────────────────────────────┬────────────────────────────────────┐
│                   Scenario                    │                 Current Behavior                  │               Impact               │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤
│ GROBID produces figure element with no coords │ Block silently skipped in extract_semantic_blocks │ Figure never processed             │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤
│ Nougat hallucinates on tiny crop              │ Hallucinated text enters nougat_result            │ Garbage in downstream KG           │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤
│ Formula context spans multiple pages          │ Context <s> blocks on wrong page never linked     │ Formula region has no context      │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤
│ Table spans two pages                         │ Two separate TABLE_REGION objects, no link        │ Table data split and unrecoverable │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤       
│ GROBID misidentifies text column as figure    │ Text region sent to Nougat                        │ Duplicate content, poor quality    │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤       
│ PDF is scanned (no text layer)                │ GROBID produces empty TEI                         │ Zero extraction output             │
├───────────────────────────────────────────────┼───────────────────────────────────────────────────┼────────────────────────────────────┤       
│ Two-column paper with figure spanning columns │ GROBID produces two half-figure coords            │ Two separate figure regions        │
└───────────────────────────────────────────────┴───────────────────────────────────────────────────┴────────────────────────────────────┘

2.2 Inference Quality Failure Modes

The system has no quality gate on Nougat output. Nougat is known to:
- Produce repetitive looping tokens on blank or very small images (< 100px height)
- Hallucinate LaTeX syntax for non-formula content (produces $\text{...}$ for text)
- Degrade severely on images with complex backgrounds (maps, satellite imagery)
- Produce garbage on images with embedded watermarks or low DPI renders

Without a quality classifier, all of this enters the output JSON as valid scientific content.

2.3 Region Geometry Failure Modes

# From the actual TEI debug output:
# figure_body: bbox=(50,63,546,541)   # spans almost full page
# formula:     bbox=(380,517,546,546) # overlaps with figure y-range 517-541

Even after the family-isolation fix, there are GROBID coordinate issues:
- GROBID's figure element bbox sometimes includes surrounding text (captions embedded in a text flow appear as part of the figure bbox)
- Coordinates on multi-chunk elements use union of all chunks — sometimes spanning vastly different regions of the page
- The graphic element inside a figure often has the correct tight bbox, but figure has an inflated one

The fix: prefer graphic coords over figure coords when both exist. The graphic is the actual image; the figure is GROBID's layout hypothesis.

  ---
3. Scalability Analysis

THROUGHPUT BOTTLENECK STACK
═══════════════════════════

3692 papers
× ~20 regions/paper = ~74,000 Nougat inference calls

Single NougatActor, sequential:
CPU:  ~25s/call → 21 days
GPU:  ~3s/call  → 2.6 days (with single actor, no batching)
GPU + batching(4): ~0.9s/call → 19 hours (achievable)
GPU + batching + actor pool (2): ~9 hours

The bottleneck is NOT the region builder or PDF rendering.
The bottleneck IS the Nougat inference throughput.

Secondary bottleneck: fitz.open() is called once per region in render_region(). For a paper with 66 regions on 15 pages, the PDF is opened and   
closed 66 times. This is pure I/O waste. Correct pattern: open once per paper, render all regions, close.

Memory bottleneck: The FORMULA_REGION context expansion creates very large crops (page-sized). At 300 DPI, an A4 page is ~3508×4961px = 52MB in  
RGB. Sending this to Nougat means peak GPU memory for the image encoder is proportional to image size. Large crops → large pixel_values tensors →   potential OOM.

Ontology lock: The DuckDB registry uses an RLock for single-writer access. At high concurrency this becomes a bottleneck. Not a problem at 1     
worker, serious at 8+.

  ---
4. The Semantic Integration Architecture You Need

The missing layer is a Scientific Object Integration Bus — a mediator that takes outputs from both the text pipeline and visual pipeline, runs
modality-specific understanding, and produces unified scientific objects for the KG.

PROPOSED ARCHITECTURE
══════════════════════

                      PDF
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
         GROBID TEI            GROBID Coords
            │                     │
            ▼                     ▼
        TEIDocument          SemanticBlock
        (text world)         extraction
            │                     │
            │               RegionBuilder
            │                     │
            │               ScientificRegion
            │                     │
            │         ┌───────────┼───────────┐
            │         ▼           ▼           ▼
            │    FigureRouter  TableRouter  FormulaRouter
            │      (dispatch   (structured  (LaTeX parse
            │       by type)    extraction)  + variable link)
            │         │           │           │
            │    [VisionLM]  [TableParser] [SymPy/LaTeXML]
            │    or Nougat   or structure   or MathBERT
            │         │       recognizer        │
            │         ▼           ▼             ▼
            │    FigureObject  TableObject  EquationObject
            │         │           │             │
            └─────────┴───────────┴─────────────┘
                                │
                      ╔═════════▼═════════╗
                      ║  Scientific Object ║
                      ║  Integration Bus  ║
                      ║  (entity grounding║
                      ║   ontology link   ║
                      ║   quality gate)   ║
                      ╚═════════╤═════════╝
                                │
                      ┌─────────┴─────────┐
                      ▼                   ▼
                Text entities      Visual entities
                (existing KG)      (new visual KG)
                      │                   │
                      └─────────┬─────────┘
                                ▼
                          Unified Neo4j
                          (with visual
                           provenance)

  ---
5. Scientific Object Schema Proposals

5.1 EquationObject (replace Formula model)

@dataclass
class EquationObject:
paper_id:        str
region_id:       str          # links to ScientificRegion
xml_id:          str

      # OCR layer (what Nougat/GROBID sees)
      latex_raw:       str          # raw LaTeX from Nougat or GROBID
      unicode_raw:     str | None   # GROBID's text content

      # Structural layer (parsed)
      lhs_symbol:      str | None   # "P_e", "Q_peak", "CN"
      rhs_expression:  str | None   # structured LaTeX of RHS
      equation_type:   str | None   # "loss_function", "routing", "objective",
                                    #  "empirical", "definition", "dimensional"

      # Variable dictionary (from surrounding context)
      variables: list[VariableDef]  # [{symbol, definition, unit, value_range}]

      # Ontology grounding
      canonical_equation_id: str | None  # links to known equations (SCS-CN, Manning's)
      related_methods:       list[str]   # ontology method IDs

      # Provenance
      context_sentences:  list[str]   # surrounding text that defines variables
      equation_number:    str | None  # "(1)", "(2a)"
      section:            str | None  # "Methods", "Model Setup"
      confidence:         float       # Nougat output quality score

@dataclass(frozen=True)
class VariableDef:
symbol:      str         # "P_e", "I_a", "S", "CN"
definition:  str | None  # "accumulated precipitation excess"
unit:        str | None  # "mm", "m³/s", "dimensionless"
value_range: str | None  # "0–100", "> 0"

5.2 TableObject (replace Table model)

@dataclass
class TableObject:
paper_id:    str
region_id:   str
xml_id:      str

      # Structure
      caption:     str | None
      table_type:  str   # "parameter_table", "validation_table", "comparison_table",
                         #  "event_table", "lulc_table", "calibration_table"

      # Semantic schema (inferred from headers)
      column_schema: list[ColumnDef]
      rows:          list[dict[str, CellValue]]   # column_name → value

      # Extracted facts (structured from table content)
      numeric_facts: list[NumericFact]  # [{metric, value, unit, row_context}]

      # Ontology grounding
      reported_metrics: list[str]    # ontology metric IDs found in columns
      referenced_methods: list[str]  # method IDs referenced in rows/headers

@dataclass(frozen=True)
class ColumnDef:
name:        str
canonical:   str | None  # ontology concept ID ("NSE", "PBIAS", "CN")
dtype:       str         # "numeric", "categorical", "text", "date"
unit:        str | None
is_key:      bool        # row identifier column?

@dataclass(frozen=True)
class CellValue:
raw:      str
numeric:  float | None
unit:     str | None
is_range: bool
lo:       float | None   # for "0.82–0.91" ranges
hi:       float | None

5.3 FigureObject (replace Figure model)

@dataclass
class FigureObject:
paper_id:    str
region_id:   str
xml_id:      str

      # GROBID baseline
      label:       str | None     # "Figure 1", "Fig. 3"
      caption:     str | None

      # Visual classification
      figure_type: str   # "hydrograph", "dem_map", "lulc_map", "scatter_plot",
                         #  "bar_chart", "flowchart", "methodology_diagram",
                         #  "correlation_plot", "time_series", "raster_output"

      # Content extracted (type-specific)
      axis_labels:       list[AxisLabel] | None       # for charts
      legend_items:      list[LegendItem] | None       # for maps/charts
      geographic_extent: GeoBBox | None                # for maps
      data_series:       list[DataSeries] | None       # for time series

      # Nougat raw output (always preserved)
      nougat_markdown: str | None
      nougat_quality:  float       # quality score from QualityGate

      # Embedding (for visual search)
      visual_embedding: list[float] | None  # from CLIP or SigLIP

      # Ontology grounding
      referenced_methods: list[str]  # HEC-HMS, SCS-CN detected in caption
      study_area:         str | None # geographic context
      time_period:        str | None # temporal extent

@dataclass(frozen=True)
class AxisLabel:
axis:    str    # "x", "y", "y2"
label:   str    # "Time (hours)", "Discharge (m³/s)"
unit:    str | None
metric:  str | None   # ontology metric ID

@dataclass(frozen=True)
class GeoBBox:
lat_min: float
lat_max: float
lon_min: float
lon_max: float
crs:     str | None   # "WGS84", "UTM32N"

  ---
6. Knowledge Graph Architecture: Visual Provenance Layer

The Neo4j schema needs a visual objects layer:

PROPOSED NEO4J NODE ADDITIONS
═══════════════════════════════

(:VisualRegion {
region_id, paper_id, page,
region_type, bbox_json,
crop_strategy, nougat_quality
})

(:Equation {
eq_id, paper_id,
latex_raw, lhs_symbol,
equation_type,
canonical_equation_id,
equation_number
})

(:ScientificTable {
table_id, paper_id,
caption, table_type,
column_count, row_count
})

(:ScientificFigure {
fig_id, paper_id,
figure_type, caption,
has_geographic_context
})

(:VariableSymbol {
symbol,           # "P_e", "NSE", "CN"
definition,
unit,
domain            # "hydrology", "remote_sensing"
})

PROPOSED RELATIONSHIP ADDITIONS
═════════════════════════════════

(Paper)-[:HAS_EQUATION]->(Equation)
(Paper)-[:HAS_TABLE]->(ScientificTable)
(Paper)-[:HAS_FIGURE]->(ScientificFigure)
(Equation)-[:DEFINES]->(VariableSymbol)
(Equation)-[:GROUNDS_TO]->(Method)
(ScientificTable)-[:REPORTS_IN_TABLE]->(Metric {value, unit, context})
(ScientificFigure)-[:VISUALIZES]->(Method)
(ScientificFigure)-[:SHOWS_STUDY_AREA]->(Country)
(VisualRegion)-[:SOURCE_OF]->(Equation|ScientificTable|ScientificFigure)

With this schema, you can query:
// All papers that report NSE in tabular form, with values
MATCH (p:Paper)-[:HAS_TABLE]->(t:ScientificTable)
-[:REPORTS_IN_TABLE]->(m:Metric {canonical_id: "metric_nse"})
WHERE m.value > 0.7
RETURN p.title, m.value, m.context

// Papers that use SCS-CN loss model + show a hydrograph figure
MATCH (p:Paper)-[:USES_METHOD]->(m:Method {canonical_id: "method_scs_cn"}),
(p)-[:HAS_FIGURE]->(f:ScientificFigure {figure_type: "hydrograph"})
RETURN p.title, f.caption

  ---
7. Formula Intelligence Roadmap

Stage 1 — Symbol Normalization (now)
Run regex over Nougat output to extract lhs_symbol, equation number. Match against a hydrology symbol dictionary (P_e, Q_peak, I_a, S, CN, NSE,
KGE, PBIAS).

Stage 2 — Variable Dictionary Extraction
Extract the surrounding context sentences that define variables. Pattern: where X is the Y → {symbol: X, definition: Y}. This is solvable with
simple NLP without a large model.

Stage 3 — SymPy Parsing
For equations that Nougat produces as clean LaTeX, attempt sympy.parse_latex(). This gives you a symbolic expression tree — you can normalize
form, detect equation families, solve for variables, compare across papers.

Stage 4 — Canonical Equation Library
Build a library of canonical hydrology equations (SCS-CN loss, Manning's equation, Nash-Sutcliffe, Kling-Gupta, rational method, unit hydrograph
convolution, Muskingum routing). Each equation has:
- SymPy expression tree
- Variable dictionary
- Multiple notation variants (different symbols for same concept across papers)

Use SymPy symbolic equivalence to match extracted equations to the canonical library. This is what makes the system genuinely powerful.

Stage 5 — Cross-Paper Equation Comparison
Once equations are in SymPy form, you can detect when two papers use the same equation with different parameter values, or when a paper modifies
a standard equation with an additional term.

  ---
8. Figure Intelligence Roadmap

Stage 1 — Type Classification (now)
Replace the caption-keyword heuristic with a lightweight visual classifier (ViT-B fine-tuned on a synthetic hydrology figure dataset or distilled   from a large VLM). Output: hydrograph | dem_map | lulc_map | scatter | bar | flowchart | table_image | other.

Stage 2 — Type-Dispatched Parsers

For hydrographs:
- Axis detection (OpenCV contour + line detection)
- Label extraction (Nougat on axis label crops — this is where Nougat excels)
- Series identification (legend matching)
- Peak value extraction (argmax of detected curve)
- Time axis normalization (convert hours/days to ISO)

For maps (DEM/LULC/raster):
- Do NOT send to Nougat — it produces garbage on cartographic imagery
- Use CLIP embeddings for visual similarity matching
- Extract geographic extent from axes/scalebar (OCR)
- Detect legend via color palette analysis

For scatter/bar/line charts:
- Use a chart understanding model (ChartQA, DePlot, MatCha)
- These are specifically designed for quantitative data extraction from charts
- Far superior to Nougat for this task

For flowcharts/methodology diagrams:
- Nougat is reasonable here (describes the diagram in text)
- Supplement with structured node/edge extraction

Stage 3 — Visual Embeddings
Use SigLIP or CLIP to embed all figure crops. This enables:
- Visual similarity search ("find all hydrographs like this one")
- Cross-paper figure clustering (which papers show similar basin maps?)
- Zero-shot figure classification improvement
- Visual provenance linking

Stage 4 — Caption-Content Alignment
Cross-validate figure content against caption text. If the caption says "hydrograph for event 22 Nov 1999" but the figure type classifier says
"lulc_map", flag as low confidence.

  ---
9. Table Intelligence Roadmap

Stage 1 — Structural Recovery

The current Table.rows is flat OCR text. You need:
- Header row identification (bold text, position, separator line detection)
- Column alignment detection (left/right/center → numeric vs categorical)
- Multi-row header handling (merged cells in HTML-style tables)

Strategy: After GROBID produces the <table> element, use the row/cell structure already in the TEI XML (GROBID extracts table structure). The
TEIParser should be extracting <row>, <cell>, and <cell role="col"> elements — check if this is being done.

Stage 2 — Column Schema Inference

Map column headers to ontology concepts:
- NSE, Nash-Sutcliffe → metric_nse
- CN, Curve Number → parameter_cn
- Peak Flow, Qpeak → metric_peak_flow
- Event, Storm → identifier
- Observed, Simulated → value_type

Stage 3 — Numeric Fact Extraction

For each (column, row) cell that is numeric:
- Extract value and unit from raw text
- Link to the column's canonical concept
- Preserve row context (event identifier, watershed name, model name)
- Store as NumericFact(metric_id, value, unit, context)

These numeric facts are the high-value outputs for the KG.

Stage 4 — Validation Table Special Case

Papers reporting calibration/validation results always have a specific pattern:
Event | Observed_peak | Simulated_peak | NSE | PBIAS | R²
------+---------------+----------------+-----+-------+---
1999  |   245 m³/s    |   231 m³/s     | 0.87| 5.7%  |0.91

Build a specialized pattern recognizer for this template. Once detected, the entire table becomes machine-readable validation statistics linked
directly to the paper in the KG.

  ---
10. Hydrology Ontology Roadmap

Current state: The ontology has 1100+ entities covering methods/sensors/metrics/concepts. It is text-world only — it has no relationship to
mathematical formulations.

What is missing:

10.1 Equation Ontology
EquationClass: SCS-CN_Loss_Equation
canonical_form: "P_e = (P - I_a)^2 / (P - I_a + S)"
variables: [P_e, P, I_a, S]
parameter_dict: {I_a: "0.2*S", S: "25400/CN - 254"}
domain: "rainfall-runoff modeling"
source: "USDA-SCS 1986"
grounded_methods: [method_scs_cn]
notation_variants: ["Pe = ...", "Q = ..."]

10.2 Physical Parameter Ontology
ParameterClass: Curve_Number
symbol: CN
aliases: [SCS-CN, SCS curve number, curve number]
range: [0, 100]
unit: dimensionless
physical_meaning: "watershed runoff potential"
equations: [SCS-CN_Loss_Equation]
measurement_method: ["LULC-based lookup table"]

10.3 Model Component Ontology
HEC-HMS has specific components — loss methods, transform methods, routing methods, baseflow methods. Each component is its own ontology entry
that relates to the parent model:
ModelComponent: SCS_Unit_Hydrograph
parent_model: HEC-HMS
component_type: transform
parameters: [Lag_time, Peak_rate_factor]
replaces: [Clark_UH, Snyder_UH]
calibration_parameters: [Tp]

10.4 Study Area Ontology
Hydrology papers are strongly tied to geographic context. The current system has Country and a list of flood events. What is needed:
- River basin hierarchy (Koraiyar → Cauvery → Bay of Bengal)
- Watershed area ranges
- Climate zone classification (arid, semi-arid, monsoon, temperate)
- Geographic bounding boxes for known basins
- DEM dataset association (SRTM 30m, ASTER GDEM, Cartosat-1 DEM)

10.5 Ontology Drift Prevention
The current merge_ontology.py uses string normalization for deduplication. As the corpus grows:
- New method names are added without checking if they're aliases of existing entries
- Acronyms collide (CN = Curve Number but also ConvNet in some papers)
- Domain-specific meaning shifts (NSE in remote sensing ≠ NSE in hydrology)

Prevention strategy: Embedding-gated admission. Before adding a new ontology entry, compute its embedding similarity to existing entries. If
cosine similarity > 0.88 to an existing entry in the same domain, flag for human review as potential duplicate.

  ---
11. Production Redesign: The GPU Inference Architecture

PROPOSED ACTOR ARCHITECTURE
════════════════════════════

                      Ray Cluster
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
      NougatActorPool   TableActor   FigureRouter
      (N actors,        (no GPU,     (dispatch to
       GPU balanced)     Nougat for  right parser
                         structured  per figure type)
                         tables)          │
                                     ┌────┴────┐
                                     ▼         ▼
                                ChartQA     CLIP/SigLIP
                                Actor       Embedder
                                (DePlot     Actor
                                 compatible)

NougatActorPool:
- N = floor(GPU_VRAM_GB / 8)  (Nougat-base needs ~6-8GB)
- Batch processing: collect K images, call generate() with batch
- Health monitoring: RestartableActor pattern
- Work queue with backpressure: max_inflight = N * BATCH_SIZE * 2
- Result caching: SHA256(image_bytes) → nougat_result in DuckDB

Critical fix — open PDF once per paper:
def render_all_regions(pdf_path, regions):
doc = fitz.open(str(pdf_path))  # open ONCE
images = {}
for region in regions:
page = doc[region.page - 1]
# render
doc.close()
return images

GPU batch inference in NougatParser:
def parse_images_batch(self, images: list[PIL.Image]) -> list[str]:
pixel_values = self._processor(
images=images,  # batched
return_tensors="pt",
).pixel_values.to(self._model.device)

      with torch.inference_mode():
          outputs = self._model.generate(
              pixel_values,  # (B, C, H, W)
              max_new_tokens=self._max_new_tokens,
          )
      return self._processor.batch_decode(outputs, skip_special_tokens=True)

  ---
12. The Research-Grade Evolution Roadmap

PHASE 1: Integration (1-2 months)
══════════════════════════════════
Goal: Connect visual pipeline output to text pipeline and KG

1. Add ScientificRegion reference to TEIDocument
   (visual_regions: list[str] = field of region_ids)

2. Add VisualRegion, Equation, ScientificTable, ScientificFigure
   to Neo4j schema

3. Write RegionToKG loader that reads nougat_regions/*.json
   and writes to Neo4j (basic version, no semantic understanding yet)

4. Add nougat_quality scoring (blank detection, repetition detection,
   entropy-based quality gate) to reject bad Nougat output before KG write

5. Cache Nougat results in DuckDB (SHA256(crop_bytes) → result_json)

PHASE 2: Semantic Layer (2-3 months)
══════════════════════════════════════
Goal: Understand what visual objects contain

1. Formula: implement SymPy parsing for LaTeX output
   Build hydrology canonical equation library (20 core equations)
   Variable extraction from surrounding context

2. Table: extract column schema from GROBID table structure
   Implement NumericFact extraction for validation tables
   Link metric columns to ontology entries

3. Figure: train/adapt lightweight figure type classifier
   Implement type-dispatched parsers:
    - Nougat for structured text figures (flowcharts, methodology)
    - DePlot/ChartQA for quantitative charts
    - CLIP embeddings for maps and raster outputs

4. Entity grounding: run KB entity extractor on
   Nougat markdown output, caption text, axis labels

PHASE 3: Intelligence Layer (3-6 months)
════════════════════════════════════════
Goal: Reasoning over scientific objects

1. Cross-paper equation matching via SymPy equivalence
   ("This paper uses the SCS-CN equation from USDA 1986")

2. Validation table aggregation across papers
   ("NSE across 47 papers using HEC-HMS: μ=0.74, σ=0.12")

3. Figure-text alignment validation
   (cross-check method mentioned in text against figure content)

4. Study area clustering via geographic bounding box extraction
   from maps and DEM figures

5. Citation context linking to visual objects
   ("Figure 2 shows..." → link citation context to ScientificFigure)

PHASE 4: Scientific Reasoning (6-12 months)
════════════════════════════════════════════
Goal: GeoHydroAI can answer complex scientific questions

1. "What CN values are used for urban areas in South Asian basins?"
   → query ScientificTable nodes with column CN + country context

2. "Which papers validate HEC-HMS calibration with NSE > 0.8?"
   → query Equation(SCS-CN) + ScientificTable(NSE > 0.8) + Method(HEC-HMS)

3. "Show me hydrographs from papers in monsoon-climate catchments"
   → CLIP visual search over FigureObject(type=hydrograph) + GeoBBox filter

4. Automatic method genealogy extraction
   → "This paper adapts the Koraiyar model from [Natarajan 2021]"
   → Reference link + EquationObject similarity → method lineage graph

  ---
Summary: The Five Most Critical Actions

1. Connect the visual pipeline to the KG — write RegionToKG that loads nougat_regions/*.json into Neo4j. This unblocks everything downstream. It
   is the integration bus.

2. Add a Nougat quality gate — before any Nougat result touches the KG, score it for blank content, repetition loops, and entropy. Reject        
   low-quality results rather than polluting the graph.

3. Open PDFs once per paper — fitz.open() inside render_region() is an I/O catastrophe at 66 regions per paper. Fix this immediately.

4. Implement NumericFact extraction for validation tables — this is the highest-ROI semantic extraction available. Validation statistics (NSE,   
   PBIAS, R², RMSE) in tabular form are the primary quantitative output of hydrology papers and are the most valuable data for the KG.

5. Build the canonical hydrology equation library — 20 core equations (SCS-CN loss, Manning's, Nash-Sutcliffe, Kling-Gupta, rational method,     
   Muskingum routing, Green-Ampt, etc.) with SymPy expressions, variable dictionaries, and multi-notation variants. This transforms formula OCR into   formula understanding and creates a uniquely valuable knowledge asset.

The system is genuinely more sophisticated than most academic DocAI pipelines. The architecture debt is not in what exists — it is in what       
connects the pieces. The integration bus, the quality gate, and the semantic object schemas are the three pillars that transform this from a
pipeline of extractors into a genuine scientific intelligence infrastructure.
